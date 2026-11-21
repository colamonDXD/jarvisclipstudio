"""Learning endpoints + service glue: feedback capture, status, rollback.

Route tests follow the test_clip_rerender.py pattern — direct route
invocation with fake repositories — so they run without Postgres. The
service tests swap the repository for an in-memory fake and exercise the
full loop: record -> version -> resolve -> rollback -> resolve.
"""

import pytest
from fastapi import HTTPException

from src.api.routes import learning as learning_routes
from src.api.routes import tasks as task_routes
from src.services import learning_service


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeLearningRepository:
    """In-memory stand-in for LearningRepository (same async surface)."""

    def __init__(self):
        self.events = []
        self.versions = []

    async def record_event(self, db, **kwargs):
        self.events.append(
            {
                "niche": kwargs["niche"],
                "verdict": kwargs["verdict"],
                "reason": kwargs["reason"],
                "source": kwargs["source"],
                "weight": kwargs["weight"],
                "features": kwargs["features"],
            }
        )
        return f"event-{len(self.events)}"

    async def list_events(self, db, user_id):
        return list(self.events)

    async def count_events(self, db, user_id):
        return len(self.events)

    async def list_versions(self, db, user_id):
        return list(self.versions)

    async def current_version(self, db, user_id):
        return self.versions[-1] if self.versions else None

    async def insert_version(self, db, user_id, document):
        self.versions.append(document)


class ExplodingRepository:
    """Every call raises: feedback capture must degrade, never break."""

    async def record_event(self, db, **kwargs):
        raise RuntimeError("database is down")

    async def list_events(self, db, user_id):
        raise RuntimeError("database is down")

    async def count_events(self, db, user_id):
        raise RuntimeError("database is down")

    async def list_versions(self, db, user_id):
        raise RuntimeError("database is down")

    async def current_version(self, db, user_id):
        raise RuntimeError("database is down")

    async def insert_version(self, db, user_id, document):
        raise RuntimeError("database is down")


TASK = {
    "id": "task-1",
    "user_id": "user-1",
    "caption_template": "default",
    "niche_override": "meme_comedy",
    "music_override": None,
}

CLIP = {
    "id": "clip-1",
    "task_id": "task-1",
    "virality_score": 72,
    "hook_score": 12,
    "engagement_score": 20,
    "value_score": 20,
    "shareability_score": 20,
    "hook_type": "statement",
    "relevance_score": 0.9,
    "niche_override": None,
}


@pytest.fixture
def fake_repo(monkeypatch):
    repo = FakeLearningRepository()
    monkeypatch.setattr(learning_service, "LearningRepository", repo)
    return repo


# ---------------------------------------------------------------------------
# Service: record -> version -> resolve -> rollback
# ---------------------------------------------------------------------------


async def test_explicit_feedback_creates_a_version(fake_repo):
    result = await learning_service.record_explicit_feedback(
        None,
        user_id="user-1",
        task=TASK,
        clip=CLIP,
        verdict="reject",
        reason="hook_weak",
    )
    assert result == {"recorded": True, "learning_version": 1}
    event = fake_repo.events[0]
    assert event["niche"] == "meme_comedy"  # task's pinned niche
    assert event["source"] == "explicit"
    assert event["weight"] == 1.0
    # Snapshot carries the clip's scores and the profile's caption preset.
    assert event["features"]["hook_score"] == 12
    assert event["features"]["caption_preset_id"] == "large_centre_pop"
    assert event["features"]["sfx_enabled"] is True


async def test_service_learns_then_rolls_back(fake_repo):
    # One explicit verdict first: version 1 exists but is below every
    # threshold, so it carries no adjustments.
    await learning_service.record_explicit_feedback(
        None,
        user_id="user-1",
        task=TASK,
        clip=CLIP,
        verdict="reject",
        reason="hook_weak",
    )
    assert fake_repo.versions[-1]["niches"] == {}

    for _ in range(7):
        await learning_service.record_feedback_safely(
            None,
            user_id="user-1",
            task=TASK,
            clip=CLIP,
            verdict="reject",
            reason="hook_weak",
            source="explicit",
        )
    document = await learning_service.resolve_learning_document(None, "user-1")
    assert document is not None
    assert document["version"] == 2
    assert document["niches"]["meme_comedy"]["score_weights"] == {
        "hook_score": 1.15
    }

    # Rollback appends version 3 carrying version 1's (empty) adjustments;
    # the resolver then serves base behaviour again.
    rolled = await learning_service.rollback_learning(None, "user-1", 1)
    assert rolled["version"] == 3
    assert rolled["niches"] == {}
    current = await learning_service.resolve_learning_document(None, "user-1")
    assert current["version"] == 3
    assert current["niches"] == {}


async def test_recording_failure_never_raises(monkeypatch):
    monkeypatch.setattr(
        learning_service, "LearningRepository", ExplodingRepository()
    )
    recorded = await learning_service.record_feedback_safely(
        None,
        user_id="user-1",
        task=TASK,
        clip=CLIP,
        verdict="reject",
        reason=None,
        source="passive_delete",
    )
    assert recorded is False
    result = await learning_service.record_explicit_feedback(
        None,
        user_id="user-1",
        task=TASK,
        clip=CLIP,
        verdict="accept",
        reason=None,
    )
    assert result == {"recorded": False, "learning_version": None}
    # The pipeline resolver degrades to base parameters, not an exception.
    assert (
        await learning_service.resolve_learning_document(None, "user-1") is None
    )


async def test_kill_switch_disables_everything(monkeypatch, fake_repo):
    monkeypatch.setenv("LEARNING_ENABLED", "false")
    recorded = await learning_service.record_feedback_safely(
        None,
        user_id="user-1",
        task=TASK,
        clip=CLIP,
        verdict="accept",
        reason=None,
        source="passive_download",
    )
    assert recorded is False
    assert fake_repo.events == []
    assert (
        await learning_service.resolve_learning_document(None, "user-1") is None
    )
    status = await learning_service.learning_status(None, "user-1")
    assert status["enabled"] is False
    assert status["current_version"] is None


async def test_passive_hook_swallows_session_failure(monkeypatch):
    """The route-level passive wrapper must survive a dead session factory."""

    def exploding_factory():
        raise RuntimeError("no database")

    monkeypatch.setattr(task_routes, "AsyncSessionLocal", exploding_factory)
    # Must simply return; the user action it rides on is unaffected.
    await task_routes._record_passive_feedback(
        TASK, CLIP, "accept", "passive_download"
    )


# ---------------------------------------------------------------------------
# Routes: clip feedback endpoint
# ---------------------------------------------------------------------------


class FakeTaskRepo:
    async def get_task_by_id(self, _db, task_id):
        return dict(TASK) if task_id == TASK["id"] else None


class FakeClipRepo:
    async def get_clip_by_id(self, _db, clip_id):
        return dict(CLIP) if clip_id == CLIP["id"] else None


class FakeTaskService:
    def __init__(self, _db):
        self.task_repo = FakeTaskRepo()
        self.clip_repo = FakeClipRepo()


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.fixture
def feedback_route(monkeypatch):
    monkeypatch.setattr(task_routes, "TaskService", FakeTaskService)

    async def fake_user_id(_request, _db):
        return "user-1"

    monkeypatch.setattr(task_routes, "_get_user_id_from_headers", fake_user_id)
    calls = []

    async def fake_record(_db, **kwargs):
        calls.append(kwargs)
        return {"recorded": True, "learning_version": 3}

    monkeypatch.setattr(task_routes, "record_explicit_feedback", fake_record)
    return calls


async def test_feedback_endpoint_records(feedback_route):
    result = await task_routes.record_clip_feedback(
        "task-1",
        "clip-1",
        FakeRequest({"verdict": "reject", "reason": "hook_weak"}),
        None,
    )
    assert result == {"recorded": True, "learning_version": 3}
    assert feedback_route[0]["verdict"] == "reject"
    assert feedback_route[0]["reason"] == "hook_weak"


async def test_feedback_endpoint_validates_verdict_and_reason(feedback_route):
    with pytest.raises(HTTPException) as excinfo:
        await task_routes.record_clip_feedback(
            "task-1", "clip-1", FakeRequest({"verdict": "meh"}), None
        )
    assert excinfo.value.status_code == 422

    with pytest.raises(HTTPException) as excinfo:
        await task_routes.record_clip_feedback(
            "task-1",
            "clip-1",
            FakeRequest({"verdict": "reject", "reason": "boring"}),
            None,
        )
    assert excinfo.value.status_code == 422


async def test_feedback_endpoint_404s(feedback_route):
    with pytest.raises(HTTPException) as excinfo:
        await task_routes.record_clip_feedback(
            "nope", "clip-1", FakeRequest({"verdict": "accept"}), None
        )
    assert excinfo.value.status_code == 404

    with pytest.raises(HTTPException) as excinfo:
        await task_routes.record_clip_feedback(
            "task-1", "nope", FakeRequest({"verdict": "accept"}), None
        )
    assert excinfo.value.status_code == 404


async def test_feedback_endpoint_rejects_non_owner(monkeypatch, feedback_route):
    async def other_user(_request, _db):
        return "someone-else"

    monkeypatch.setattr(task_routes, "_get_user_id_from_headers", other_user)
    with pytest.raises(HTTPException) as excinfo:
        await task_routes.record_clip_feedback(
            "task-1", "clip-1", FakeRequest({"verdict": "accept"}), None
        )
    assert excinfo.value.status_code == 403


# ---------------------------------------------------------------------------
# Routes: learning status + rollback
# ---------------------------------------------------------------------------


@pytest.fixture
def learning_route_auth(monkeypatch):
    async def fake_user_id(_request, _db):
        return "user-1"

    monkeypatch.setattr(
        learning_routes, "_get_authenticated_user_id", fake_user_id
    )


async def test_status_route_returns_service_payload(
    monkeypatch, learning_route_auth
):
    async def fake_status(_db, _user_id):
        return {"enabled": True, "current_version": 2, "adjustments": {}}

    monkeypatch.setattr(learning_routes, "learning_status", fake_status)
    result = await learning_routes.get_learning_status(FakeRequest({}), None)
    assert result["current_version"] == 2


async def test_rollback_route_appends_a_version(
    monkeypatch, learning_route_auth
):
    async def fake_rollback(_db, _user_id, version):
        return {
            "version": 5,
            "niches": {},
            "summary": [f"Rolled back to version {version} (from version 4)"],
        }

    monkeypatch.setattr(
        learning_routes, "rollback_learning_document", fake_rollback
    )
    result = await learning_routes.rollback_learning(
        FakeRequest({"version": 2}), None
    )
    assert result["version"] == 5
    assert result["rolled_back_to"] == 2


async def test_rollback_route_validates_version(learning_route_auth):
    for payload in ({"version": 0}, {"version": "2"}, {}):
        with pytest.raises(HTTPException) as excinfo:
            await learning_routes.rollback_learning(FakeRequest(payload), None)
        assert excinfo.value.status_code == 422


async def test_rollback_route_unknown_version_is_404(
    monkeypatch, learning_route_auth
):
    async def fake_rollback(_db, _user_id, version):
        raise ValueError(f"Unknown learning version: {version}")

    monkeypatch.setattr(
        learning_routes, "rollback_learning_document", fake_rollback
    )
    with pytest.raises(HTTPException) as excinfo:
        await learning_routes.rollback_learning(
            FakeRequest({"version": 99}), None
        )
    assert excinfo.value.status_code == 404
