"""API wiring (Addendum A.5/A.6): create-task brief validation, campaign
snapshot isolation, publishing board brief checks, public-task shape."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes import tasks
from src.auto_director.content_requirements import validate_content_brief
from src.services import publishing_service

BRIEF_PAYLOAD = {
    "requirements": [
        {"text": "Expedia must be a major focus of the content.", "kind": "mandatory"}
    ],
    "allowed_ranges": [{"start": "2:00", "end": "8:00"}],
}
YOUTUBE_URL = "https://www.youtube.com/watch?v=abcdefghijk"


def _request(body, queue_adapter=None):
    return SimpleNamespace(
        json=AsyncMock(return_value=body),
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(queue_adapter=queue_adapter or _QueueAdapter())
        ),
    )


class _QueueAdapter:
    async def enqueue_processing_job(self, *args, **kwargs):
        return "job-1"


class _FakeBillingService:
    def __init__(self, db):
        pass

    async def assert_can_create_task(self, *args, **kwargs):
        return {
            "max_youtube_duration_seconds": 100000,
            "max_upload_duration_seconds": 100000,
        }


class _FakeTaskService:
    captured: dict = {}
    reused_upload_task = False

    def __init__(self, db):
        self.video_service = SimpleNamespace(
            determine_source_type=lambda url: "youtube"
        )
        self.task_repo = SimpleNamespace(update_task_status=AsyncMock())

    async def create_task_with_source(self, **kwargs):
        _FakeTaskService.captured = kwargs
        return "task-1"


@contextlib.asynccontextmanager
async def _no_guard(*args, **kwargs):
    yield


def _wire_common(monkeypatch):
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    monkeypatch.setattr("src.source_submission.record_source_submission", AsyncMock())
    monkeypatch.setattr(tasks, "BillingService", _FakeBillingService)
    monkeypatch.setattr(tasks, "TaskService", _FakeTaskService)
    monkeypatch.setattr(tasks, "generation_allowance_guard", _no_guard)
    monkeypatch.setattr(tasks, "_save_task_source_metadata", AsyncMock())
    monkeypatch.setattr(
        tasks,
        "_preflight_youtube_source",
        AsyncMock(return_value={"duration": 600.0, "title": "Source"}),
    )
    _FakeTaskService.captured = {}


async def test_create_task_rejects_malformed_brief_with_422(monkeypatch):
    _wire_common(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            _request(
                {
                    "source": {"url": YOUTUBE_URL},
                    "content_brief": {
                        "requirements": [],
                        "allowed_ranges": [{"start": "banana", "end": None}],
                    },
                }
            ),
            db=None,
        )
    assert error.value.status_code == 422


async def test_create_task_range_beyond_duration_is_422(monkeypatch):
    _wire_common(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            _request(
                {
                    "source": {"url": YOUTUBE_URL},
                    "content_brief": {
                        "requirements": [],
                        "allowed_ranges": [{"start": "20:00", "end": None}],
                    },
                }
            ),
            db=None,
        )
    assert error.value.status_code == 422  # source is only 600s long


async def test_create_task_stores_payload_brief(monkeypatch):
    _wire_common(monkeypatch)
    result = await tasks.create_task(
        _request({"source": {"url": YOUTUBE_URL}, "content_brief": BRIEF_PAYLOAD}),
        db=None,
    )
    assert result["task_id"] == "task-1"
    stored = _FakeTaskService.captured["content_brief"]
    assert stored["version"] == 2
    assert stored["allowed_ranges"][0]["start_seconds"] == 120.0


async def test_campaign_snapshot_isolation_and_payload_precedence(monkeypatch):
    _wire_common(monkeypatch)
    campaign = {
        "id": "camp-1",
        "brief": validate_content_brief(BRIEF_PAYLOAD),
        "example_url": None,
    }

    class _FakePublishingRepository:
        @staticmethod
        async def get_campaign(db, user_id, campaign_id):
            return campaign

    monkeypatch.setattr(tasks, "PublishingRepository", _FakePublishingRepository)
    await tasks.create_task(
        _request({"source": {"url": YOUTUBE_URL}, "campaign_id": "camp-1"}), db=None
    )
    snapshot = _FakeTaskService.captured["content_brief"]
    assert snapshot["requirements"][0]["text"].startswith("Expedia")
    assert _FakeTaskService.captured["campaign_id"] == "camp-1"
    # Later campaign edits never mutate the task's stored brief: the task
    # received the snapshot value, and the repository row for the task is
    # independent storage (create_task json-dumps it at write time).
    campaign["brief"]["requirements"][0]["text"] = "CHANGED AFTERWARDS"
    assert snapshot["requirements"][0]["text"].startswith("Expedia")

    # Payload brief wins over the campaign snapshot.
    other = {
        "requirements": [{"text": "Mention the code", "type": "must_mention", "phrase": "CREATOR"}]
    }
    await tasks.create_task(
        _request(
            {"source": {"url": YOUTUBE_URL}, "campaign_id": "camp-1", "content_brief": other}
        ),
        db=None,
    )
    assert _FakeTaskService.captured["content_brief"]["requirements"][0]["type"] == "must_mention"


async def test_campaign_example_feeds_reference_style_only_without_user_style(monkeypatch):
    _wire_common(monkeypatch)
    campaign = {"id": "camp-1", "brief": None, "example_url": "https://example.com/ref.mp4"}

    class _FakePublishingRepository:
        @staticmethod
        async def get_campaign(db, user_id, campaign_id):
            return campaign

    monkeypatch.setattr(tasks, "PublishingRepository", _FakePublishingRepository)
    style = AsyncMock(return_value="user-ref-style")
    monkeypatch.setattr(tasks, "_reference_style_from_campaign_example", style)
    await tasks.create_task(
        _request({"source": {"url": YOUTUBE_URL}, "campaign_id": "camp-1"}), db=None
    )
    assert style.await_count == 1
    assert _FakeTaskService.captured["niche_override"] == "user-ref-style"

    style2 = AsyncMock(return_value="user-ref-style")
    monkeypatch.setattr(tasks, "_reference_style_from_campaign_example", style2)
    await tasks.create_task(
        _request(
            {
                "source": {"url": YOUTUBE_URL},
                "campaign_id": "camp-1",
                "niche_override": "gaming",
            }
        ),
        db=None,
    )
    assert style2.await_count == 0
    assert _FakeTaskService.captured["niche_override"] == "gaming"


async def test_unknown_campaign_is_404(monkeypatch):
    _wire_common(monkeypatch)

    class _FakePublishingRepository:
        @staticmethod
        async def get_campaign(db, user_id, campaign_id):
            return None

    monkeypatch.setattr(tasks, "PublishingRepository", _FakePublishingRepository)
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            _request({"source": {"url": YOUTUBE_URL}, "campaign_id": "nope"}), db=None
        )
    assert error.value.status_code == 404


def test_upload_request_key_differs_by_brief():
    request = SimpleNamespace(headers={})
    upload_url = "upload://video.mp4"
    base = {"url": upload_url, "clip_length": "auto", "target_clip_count": None}
    key_a = tasks._upload_request_key(request, upload_url, base)
    key_b = tasks._upload_request_key(
        request, upload_url,
        {**base, "content_brief": validate_content_brief(BRIEF_PAYLOAD)},
    )
    assert key_a and key_b and key_a != key_b
    key_c = tasks._upload_request_key(
        request, upload_url, {**base, "campaign_id": "camp-1"}
    )
    assert key_c != key_a


def test_public_task_strips_brief_internals():
    task = {
        "id": "t1",
        "content_brief": validate_content_brief(BRIEF_PAYLOAD),
        "brief_report": {"outcome": "clips_selected"},
        "clips": [
            {
                "id": "c1",
                "compliance_status": "non_compliant",
                "compliance_report": {"status": "non_compliant", "requirements": []},
                "compliance_summary": {
                    "status": "non_compliant",
                    "mandatory_failed": ["r1"],
                    "mandatory_uncertain": [],
                },
                "path": "clip.mp4",
            }
        ],
    }
    public = tasks._build_public_task(task, "share-token")
    assert "content_brief" not in public
    assert "brief_report" not in public
    clip = public["clips"][0]
    assert "compliance_report" not in clip
    # Only the status badge survives — never the mandatory lists.
    assert clip["compliance_summary"] == {"status": "non_compliant"}


# ---------------------------------------------------------------- publishing


def _board_clip(status, brief=None):
    return {
        "id": "clip-1",
        "task_id": "task-1",
        "platform": "tiktok",
        "compliance_status": status,
        "compliance_report": {"status": status, "requirements": []} if status else None,
        "task_content_brief": brief,
        "clips": {},
        "platform_clips": {},
        "video_probe": {},
        "clip_qc": None,
    }


def test_board_brief_check_blocks_non_compliant():
    brief = validate_content_brief(BRIEF_PAYLOAD)
    clip = _board_clip("non_compliant", brief=brief)
    readiness = publishing_service.compute_readiness(clip, "tiktok", clip=clip)
    assert readiness["status"] == "blocked"
    assert any(c["name"] == "brief" and c["status"] == "fail" for c in readiness["checks"])


def test_board_brief_check_warns_on_uncertain():
    brief = validate_content_brief(BRIEF_PAYLOAD)
    clip = _board_clip("uncertain", brief=brief)
    readiness = publishing_service.compute_readiness(clip, "tiktok", clip=clip)
    assert any(c["name"] == "brief" and c["status"] == "warn" for c in readiness["checks"])


def test_board_unchanged_without_brief():
    clip = _board_clip("non_compliant", brief=None)
    readiness = publishing_service.compute_readiness(clip, "tiktok", clip=clip)
    assert not any(c["name"] == "brief" for c in readiness["checks"])
    assert publishing_service.brief_summary_for_clip(clip)["has_brief"] is False
