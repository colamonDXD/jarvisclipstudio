"""Hook-variant previews (Lane K): derivation, service, routes, workers.

Derivation is deterministic from the clip + its stored ClipPlan; the
service tests drive the real service code with the re-render fakes so
the EDL each variant renders with can be asserted exactly, the way the
Lane-5 re-render tests assert theirs.
"""

import inspect
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes import tasks as task_routes
from src.auto_director import hook_variants as hv
from src.auto_director.clip_plan import (
    CaptionStyleChoice,
    ClipPlan,
    HookDecision,
    OpeningTreatment,
    PlannedSfxEvent,
    TransitionChoice,
)
from src.auto_director.edl import load_profile
from src.services.task_service import TaskService
from src.workers import tasks as worker_tasks

from test_clip_rerender import FakeClipRepo, FakeRedis, FakeVideoService, _clip_dict


@pytest.fixture
def edit_boundary(monkeypatch):
    """Keep service unit tests independent of the PostgreSQL edit boundary."""

    @asynccontextmanager
    async def transaction(_db, _task_id):
        yield

    monkeypatch.setattr(
        "src.repositories.edit_transaction.task_edit_transaction", transaction
    )


# --- Derivation (pure) -------------------------------------------------------

SEGMENTS = [
    {
        "start": 0.0,
        "end": 10.0,
        "duration": 10.0,
        "transition_in": "none",
        "transition_duration": 0.0,
        "source_offset": 0.0,
        "source_share": 0.5,
    },
    {
        "start": 10.0,
        "end": 20.0,
        "duration": 10.0,
        "transition_in": "cut",
        "transition_duration": 0.0,
        "source_offset": 10.0,
        "source_share": 0.5,
    },
]

PROFILE = load_profile("podcast_news")


def _plan(*, hook_ts=12.0, punch="subtle", source="llm"):
    return ClipPlan(
        niche="podcast_news",
        source=source,
        hook=HookDecision(
            type="bold_claim",
            timestamp=hook_ts,
            confidence=0.9,
            reason="He says the quiet part out loud",
        ),
        opening_treatment=OpeningTreatment(
            punch_zoom=punch, at_offset=hook_ts, reason="the hook moment"
        ),
        transition_choices=[
            TransitionChoice(name="whip_pan", at_offset=10.0, reason="topic change")
        ],
        caption_style=CaptionStyleChoice(name="default", reason="readable"),
        sfx_events=[
            PlannedSfxEvent(at_offset=1.0, name="riser", reason="opening lift")
        ],
    )


def _by_variant(specs):
    return {spec["variant"]: spec for spec in specs}


def test_derive_offers_three_variants_for_a_planned_clip():
    specs = _by_variant(hv.derive_variants(SEGMENTS, _plan(), PROFILE))
    assert set(specs) == {"current", "cold_open", "punch"}
    # The control is the clip's layout, untouched.
    assert specs["current"]["segments"] == hv.normalize_segments(SEGMENTS)


def test_cold_open_moves_the_hook_window_to_the_front():
    specs = _by_variant(hv.derive_variants(SEGMENTS, _plan(), PROFILE))
    cold = specs["cold_open"]["segments"]
    # Hook at 12s: [12,20] leads, then [0,10], then the [10,12] remainder.
    assert [(s["start"], s["end"], s["source_offset"]) for s in cold] == [
        (0.0, 8.0, 12.0),
        (8.0, 18.0, 0.0),
        (18.0, 20.0, 10.0),
    ]
    assert specs["cold_open"]["hook_timestamp"] == 12.0
    assert "0:12" in specs["cold_open"]["description"]


@pytest.mark.parametrize(
    "plan",
    [
        _plan(source="fallback"),  # record-only plans never reorder
        _plan(hook_ts=0.4),  # the hook already (nearly) opens the clip
        _plan(hook_ts=19.0),  # the hook block would be a 1s flash
        None,  # no plan at all
    ],
)
def test_cold_open_is_omitted_when_it_cannot_be_honest(plan):
    specs = _by_variant(hv.derive_variants(SEGMENTS, plan, PROFILE))
    assert "cold_open" not in specs
    assert set(specs) == {"current", "punch"}


def test_cold_open_is_not_offered_twice():
    specs = _by_variant(
        hv.derive_variants(
            SEGMENTS, _plan(), PROFILE, hook_variant_applied="cold_open"
        )
    )
    assert "cold_open" not in specs


def test_punch_variant_flips_the_opening_strength():
    specs = _by_variant(hv.derive_variants(SEGMENTS, _plan(punch="subtle"), PROFILE))
    assert specs["punch"]["from_punch_zoom"] == "subtle"
    assert specs["punch"]["punch_zoom"] == "strong"
    assert specs["punch"]["punch_at_offset"] == 12.0

    specs = _by_variant(hv.derive_variants(SEGMENTS, _plan(punch="strong"), PROFILE))
    assert specs["punch"]["from_punch_zoom"] == "strong"
    assert specs["punch"]["punch_zoom"] == "subtle"


def test_plan_from_clip_roundtrip_and_garbage():
    clip = {"clip_plan": json.dumps(_plan().to_dict())}
    plan = hv.plan_from_clip(clip)
    assert plan is not None and plan.hook.timestamp == 12.0
    assert hv.plan_from_clip({"clip_plan": "not json"}) is None
    assert hv.plan_from_clip({}) is None
    assert hv.plan_from_clip(None) is None


# --- EDL consumption (pure) ---------------------------------------------------


def _edl():
    return {
        "duration": 20.0,
        "segments": [dict(segment) for segment in SEGMENTS],
        "zoom_keyframes": [
            {"type": "punch_in", "trigger": "hook", "time": 0.0, "scale": 1.1, "duration": 0.5}
        ],
        "sfx_events": [],
        "captions": {},
    }


def test_consume_current_consumes_the_plan_like_the_pipeline_does():
    edl = _edl()
    spec = {"variant": "current"}
    hv.consume_plan_for_variant(edl, spec, _plan(), PROFILE)
    # Plan transition choice landed on the join nearest its offset.
    assert edl["segments"][1]["transition_in"] == "whip_pan"
    # Plan SFX accent merged.
    assert any(
        event["name"] == "riser" and event["at_sec"] == 1.0
        for event in edl["sfx_events"]
    )
    # Plan opening punch re-scaled the profile keyframe at the hook moment.
    punches = [k for k in edl["zoom_keyframes"] if k["type"] == "punch_in"]
    assert any(
        k["time"] == 12.0 and 1.15 <= k["scale"] <= 1.25 for k in punches
    )


def test_consume_cold_open_retimes_the_punch_and_skips_old_offsets():
    edl = _edl()
    spec = {"variant": "cold_open"}
    hv.consume_plan_for_variant(edl, spec, _plan(), PROFILE)
    punches = [k for k in edl["zoom_keyframes"] if k["type"] == "punch_in"]
    # The plan's punch lands on the hook moment, which now leads at 0.
    assert any(k["time"] == 0.0 and 1.15 <= k["scale"] <= 1.25 for k in punches)
    # Plan transitions/SFX name moments on the ORIGINAL timeline; they do
    # not transfer to a reordered one.
    assert edl["segments"][1]["transition_in"] == "cut"
    assert not any(event["name"] == "riser" for event in edl["sfx_events"])


def test_apply_opening_punch_replaces_rather_than_stacks():
    edl = _edl()
    applied = hv.apply_opening_punch(
        edl, level="strong", at_offset=0.0, profile=PROFILE
    )
    assert applied is True
    punches = [k for k in edl["zoom_keyframes"] if k["type"] == "punch_in"]
    assert len(punches) == 1
    assert punches[0]["time"] == 0.0 and punches[0]["scale"] >= 1.30
    assert hv.apply_opening_punch(
        edl, level="none", at_offset=0.0, profile=PROFILE
    ) is False


def test_truncate_edl_for_preview_keeps_only_the_window():
    edl = _edl()
    edl["sfx_events"] = [
        {"name": "riser", "at_sec": 1.0, "gain_db": -3.0},
        {"name": "pop", "at_sec": 9.0, "gain_db": -3.0},
    ]
    hv.truncate_edl_for_preview(edl)
    assert edl["duration"] == hv.PREVIEW_SECONDS
    assert edl["segments"][-1]["end"] == hv.PREVIEW_SECONDS
    assert [event["name"] for event in edl["sfx_events"]] == ["riser"]
    assert all(
        keyframe["time"] < hv.PREVIEW_SECONDS
        for keyframe in edl["zoom_keyframes"]
    )


# --- Service (fakes) ------------------------------------------------------------


class HookFakeClipRepo(FakeClipRepo):
    def __init__(self, clip):
        super().__init__(clip)
        self.hook_variant_updates = []

    async def update_clip_hook_variant(
        self, _db, clip_id, hook_variant, hook_variant_batch
    ):
        self.hook_variant_updates.append((hook_variant, hook_variant_batch))
        self.clip = {
            **self.clip,
            "hook_variant": hook_variant,
            "hook_variant_batch": hook_variant_batch,
        }


class FakeHookPreviewRepo:
    """In-memory stand-in with the real repo's batch semantics."""

    _RANK = {"current": 0, "cold_open": 1, "punch": 2}

    def __init__(self):
        self.rows = {}
        self._latest_batch = {}

    async def replace_previews_for_clip(self, _db, clip_id, previews):
        removed = [row for row in self.rows.values() if row["clip_id"] == clip_id]
        self.rows = {
            key: row for key, row in self.rows.items() if row["clip_id"] != clip_id
        }
        for preview in previews:
            self.rows[preview["id"]] = dict(preview)
        if previews:
            self._latest_batch[clip_id] = previews[0]["batch_id"]
        return removed

    async def list_previews_for_clip(self, _db, clip_id):
        batch = self._latest_batch.get(clip_id)
        rows = [
            row
            for row in self.rows.values()
            if row["clip_id"] == clip_id and row["batch_id"] == batch
        ]
        return sorted(rows, key=lambda row: self._RANK.get(row["variant"], 3))

    async def get_preview(self, _db, preview_id):
        row = self.rows.get(preview_id)
        return dict(row) if row else None


class MultiRenderFakeVideoService(FakeVideoService):
    """Records every render call and writes a distinct file per call."""

    def __init__(self, source_path):
        super().__init__(source_path)
        self.render_calls = []

    async def create_single_clip(
        self,
        video_path,
        segment,
        clip_index,
        output_dir,
        font_family=None,
        font_size=None,
        font_color=None,
        caption_template="default",
        output_format="vertical",
        add_subtitles=True,
        cleanup_settings=None,
        max_short_side=None,
        max_clip_seconds=60.0,
        include_broll=False,
        broll_session=None,
        edl=None,
        niche_override=None,
        project_niche_override=None,
        classifier_niche=None,
    ):
        self.render_calls.append(
            {
                "segment": segment,
                "edl": edl,
                "caption_template": caption_template,
                "niche_override": niche_override,
            }
        )
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered = output_dir / f"render_{len(self.render_calls)}.mp4"
        rendered.write_bytes(f"rendered-{len(self.render_calls)}".encode())
        return {
            "filename": rendered.name,
            "path": str(rendered),
            "start_time": segment["start_time"],
            "end_time": segment["end_time"],
            "duration": 6.0,
            "text": segment.get("text", ""),
        }


def _service(tmp_path, monkeypatch, *, plan=None):
    from src.clip_source_map import save_clip_source_ranges

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 20.0)])

    clip = _clip_dict(clip_path)
    clip["hook_variant"] = None
    clip["hook_variant_batch"] = None
    if plan is not None:
        clip["clip_plan"] = json.dumps(plan.to_dict())

    service = TaskService(db=None)
    service.config = SimpleNamespace(
        temp_dir=str(tmp_path), default_processing_mode="fast"
    )
    clip_repo = HookFakeClipRepo(clip)
    service.clip_repo = clip_repo
    service.hook_preview_repo = FakeHookPreviewRepo()

    task = {
        "id": "task-1",
        "user_id": "user-1",
        "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
        "source_type": "youtube",
        "processing_mode": "fast",
        "caption_template": "default",
        "font_family": None,
        "font_size": None,
        "font_color": None,
        "niche_override": None,
        "music_override": None,
    }

    class FakeTaskRepo:
        async def get_task_by_id(self, _db, _task_id):
            return task

    service.task_repo = FakeTaskRepo()
    service.cache_repo = SimpleNamespace(get_cache=AsyncMock(return_value=None))
    service.video_service = MultiRenderFakeVideoService(source_path)
    service._media_limits = AsyncMock(return_value=(None, None))
    service._cleanup_source_video = lambda *args: None

    async def fake_settings(_task_id):
        return {
            "output_format": "vertical",
            "add_subtitles": True,
            "clip_length": "auto",
            "cut_long_pauses": False,
            "pause_threshold_ms": 1200,
            "remove_filler_words": False,
            "filtered_words": [],
        }

    monkeypatch.setattr(service, "_load_task_source_settings", fake_settings)
    return service, clip_repo


def _service_plan():
    # Hook at 5s on the 10s test clip; plan punch is subtle there.
    return _plan(hook_ts=5.0, punch="subtle")


def _covers(ranges, start, end):
    """Ranges tile [start, end] contiguously (splits at segment joins ok)."""
    assert ranges, "expected at least one range"
    assert ranges[0][0] == start and ranges[-1][1] == end
    assert all(
        ranges[i][1] == ranges[i + 1][0] for i in range(len(ranges) - 1)
    )
    assert sum(e - s for s, e in ranges) == pytest.approx(end - start)


async def _generate(service):
    return await service.generate_hook_previews("task-1", "clip-1")


@pytest.mark.asyncio
async def test_generate_renders_each_variant_through_the_edl_path(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo = _service(tmp_path, monkeypatch, plan=_service_plan())

    result = await _generate(service)

    previews = result["previews"]
    assert [p["variant"] for p in previews] == ["current", "cold_open", "punch"]
    # The control is marked picked while no variant is applied.
    assert [p["selected"] for p in previews] == [True, False, False]
    assert all(
        p["video_url"].endswith(f"/hook-variants/{p['id']}/file")
        for p in previews
    )

    calls = service.video_service.render_calls
    assert len(calls) == 3
    by_variant = {
        preview["variant"]: call
        for preview, call in zip(previews, calls)
    }
    for call in calls:
        # Every preview is a truncated EDL render of the first ~6s.
        assert call["edl"]["duration"] == pytest.approx(hv.PREVIEW_SECONDS)
        total = sum(
            end - start for start, end in call["segment"]["keep_ranges"]
        )
        assert total == pytest.approx(hv.PREVIEW_SECONDS)

    current = by_variant["current"]
    _covers(current["segment"]["keep_ranges"], 10.0, 16.0)
    # The plan's SFX accent and subtle punch are in the control's EDL.
    assert any(
        event["name"] == "riser" and event["at_sec"] == 1.0
        for event in current["edl"]["sfx_events"]
    )
    assert any(
        k["type"] == "punch_in"
        and abs(k["time"] - 5.0) <= 0.35
        and 1.15 <= k["scale"] <= 1.25
        for k in current["edl"]["zoom_keyframes"]
    )

    cold = by_variant["cold_open"]
    # The hook moment (clip t=5s -> source 15s) leads the preview.
    assert cold["segment"]["keep_ranges"][0][0] == 15.0
    assert cold["edl"]["segments"][0]["source_offset"] == 5.0
    # The plan's punch rides on the hook moment at the new start: the
    # retention lane's opening ramp (2026-11-15) already treats t=0, so
    # the punch re-scales the ramp to the plan's subtle scale instead
    # of stacking a punch_in step keyframe on top of it.
    ramps = [
        k
        for k in cold["edl"]["zoom_keyframes"]
        if k["type"] == "ken_burns" and k["trigger"] == "opening_punch"
    ]
    assert len(cold["edl"]["zoom_keyframes"]) == 1
    assert len(ramps) == 1
    assert ramps[0]["start"] == 0.0
    assert ramps[0]["scale_end"] == pytest.approx(1.25)

    punch = by_variant["punch"]
    _covers(punch["segment"]["keep_ranges"], 10.0, 16.0)
    assert any(
        k["type"] == "punch_in" and k["time"] == 5.0 and k["scale"] >= 1.30
        for k in punch["edl"]["zoom_keyframes"]
    )

    # Records persisted with the layout snapshot a later pick applies.
    rows = await service.hook_preview_repo.list_previews_for_clip(None, "clip-1")
    assert [row["variant"] for row in rows] == ["current", "cold_open", "punch"]
    cold_row = rows[1]
    assert cold_row["params"]["segments"][0]["source_offset"] == 5.0
    assert rows[2]["params"]["punch_zoom"] == "strong"
    for row in rows:
        assert Path(row["file_path"]).is_file()


@pytest.mark.asyncio
async def test_generate_without_a_plan_offers_two_variants(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo = _service(tmp_path, monkeypatch, plan=None)

    result = await _generate(service)

    assert [p["variant"] for p in result["previews"]] == ["current", "punch"]
    assert len(service.video_service.render_calls) == 2


@pytest.mark.asyncio
async def test_generate_unknown_clip_fails_honestly(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo = _service(tmp_path, monkeypatch, plan=_service_plan())

    with pytest.raises(ValueError, match="Clip not found"):
        await service.generate_hook_previews("task-1", "missing-clip")
    assert service.video_service.render_calls == []


@pytest.mark.asyncio
async def test_apply_cold_open_rerenders_the_reordered_layout(
    edit_boundary, monkeypatch, tmp_path
):
    service, clip_repo = _service(tmp_path, monkeypatch, plan=_service_plan())
    result = await _generate(service)
    cold = next(p for p in result["previews"] if p["variant"] == "cold_open")
    renders_before = len(service.video_service.render_calls)

    refreshed = await service.apply_hook_variant("task-1", "clip-1", cold["id"])

    assert refreshed["hook_variant"] == "cold_open"
    assert clip_repo.hook_variant_updates == [
        ("cold_open", result["batch_id"])
    ]
    apply_call = service.video_service.render_calls[-1]
    assert len(service.video_service.render_calls) == renders_before + 1
    # The full clip now plays the hook footage first, then the start:
    # kept ranges lead with the source footage from the hook moment
    # (source 15s) and end with the clip's opening footage.
    kept = apply_call["segment"]["keep_ranges"]
    assert kept[0][0] == 15.0
    assert kept[-1] == (10.0, 15.0)
    assert sum(end - start for start, end in kept) == pytest.approx(10.0)
    assert apply_call["edl"]["segments"][0]["source_offset"] == 5.0
    # The plan's opening punch rides on the hook moment at the new
    # start: the retention lane's opening ramp (2026-11-15) already
    # treats t=0, so the punch re-scales the ramp to the plan's subtle
    # scale (<= 1.25) instead of stacking a punch_in step keyframe on
    # top of it.
    ramps = [
        k
        for k in apply_call["edl"]["zoom_keyframes"]
        if k["type"] == "ken_burns" and k["trigger"] == "opening_punch"
    ]
    assert len(apply_call["edl"]["zoom_keyframes"]) == 1
    assert len(ramps) == 1
    assert ramps[0]["start"] == 0.0
    assert ramps[0]["scale_end"] == pytest.approx(1.25)
    # The pick is visible on the clip afterwards, and in the list flags.
    clip = await clip_repo.get_clip_by_id(None, "clip-1")
    assert clip["hook_variant"] == "cold_open"
    listing = await service.list_hook_previews("task-1", "clip-1")
    assert [p["selected"] for p in listing["previews"]] == [False, True, False]


@pytest.mark.asyncio
async def test_apply_punch_rerenders_with_the_stronger_opening(
    edit_boundary, monkeypatch, tmp_path
):
    service, clip_repo = _service(tmp_path, monkeypatch, plan=_service_plan())
    result = await _generate(service)
    punch = next(p for p in result["previews"] if p["variant"] == "punch")

    refreshed = await service.apply_hook_variant("task-1", "clip-1", punch["id"])

    assert refreshed["hook_variant"] == "punch"
    apply_call = service.video_service.render_calls[-1]
    # Same footage, same order — only the opening treatment changed.
    _covers(apply_call["segment"]["keep_ranges"], 10.0, 20.0)
    assert any(
        k["type"] == "punch_in" and k["time"] == 5.0 and k["scale"] >= 1.30
        for k in apply_call["edl"]["zoom_keyframes"]
    )


@pytest.mark.asyncio
async def test_apply_current_after_cold_open_restores_the_original_opening(
    edit_boundary, monkeypatch, tmp_path
):
    service, clip_repo = _service(tmp_path, monkeypatch, plan=_service_plan())
    result = await _generate(service)
    cold = next(p for p in result["previews"] if p["variant"] == "cold_open")
    await service.apply_hook_variant("task-1", "clip-1", cold["id"])

    # A fresh batch on the reordered clip: no second cold open is offered
    # (the plan's timestamps describe the original timeline).
    result2 = await _generate(service)
    assert [p["variant"] for p in result2["previews"]] == ["current", "punch"]
    current = result2["previews"][0]

    refreshed = await service.apply_hook_variant("task-1", "clip-1", current["id"])
    assert refreshed["hook_variant"] is None
    assert clip_repo.hook_variant_updates[-1] == (None, result2["batch_id"])


@pytest.mark.asyncio
async def test_apply_unknown_or_foreign_preview_fails_honestly(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo = _service(tmp_path, monkeypatch, plan=_service_plan())
    await _generate(service)
    renders_before = len(service.video_service.render_calls)

    with pytest.raises(ValueError, match="Hook preview not found"):
        await service.apply_hook_variant("task-1", "clip-1", "no-such-preview")

    rows = await service.hook_preview_repo.list_previews_for_clip(None, "clip-1")
    foreign = dict(rows[0])
    foreign["id"] = "foreign-preview"
    foreign["clip_id"] = "another-clip"
    service.hook_preview_repo.rows["foreign-preview"] = foreign
    with pytest.raises(ValueError, match="Hook preview not found"):
        await service.apply_hook_variant("task-1", "clip-1", "foreign-preview")

    assert len(service.video_service.render_calls) == renders_before


# --- Routes ---------------------------------------------------------------------


def _request(body, queue_adapter):
    return SimpleNamespace(
        json=AsyncMock(return_value=body),
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(queue_adapter=queue_adapter)),
    )


def _stub_route_service(monkeypatch, clip, previews=()):
    class StubClipRepo:
        async def get_clip_by_id(self, _db, _clip_id):
            return clip

    class StubHookPreviewRepo:
        async def get_preview(self, _db, preview_id):
            for preview in previews:
                if preview["id"] == preview_id:
                    return preview
            return None

    class StubTaskService:
        def __init__(self, _db):
            self.clip_repo = StubClipRepo()
            self.hook_preview_repo = StubHookPreviewRepo()

        async def list_hook_previews(self, task_id, clip_id):
            if clip is None or clip["task_id"] != task_id:
                raise ValueError("Clip not found")
            return {
                "clip_id": clip_id,
                "hook_variant": clip.get("hook_variant"),
                "previews": list(previews),
            }

    monkeypatch.setattr(task_routes, "TaskService", StubTaskService)
    monkeypatch.setattr(
        task_routes, "_require_task_owner", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        task_routes, "_require_task_reader", AsyncMock(return_value={})
    )


@pytest.fixture
def fake_queue(monkeypatch):
    redis = FakeRedis()
    queue = SimpleNamespace(
        enqueue_job=AsyncMock(return_value="job"),
        get_pool=AsyncMock(return_value=redis),
        get_job_status=AsyncMock(return_value="queued"),
    )
    monkeypatch.setattr(task_routes, "JobQueue", queue)
    return queue, redis


@pytest.mark.asyncio
async def test_generate_route_queues_preview_job(monkeypatch, fake_queue):
    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, redis = fake_queue

    result = await task_routes.generate_clip_hook_variants(
        "task-1", "clip-1", _request({}, queue), db=None
    )

    assert result["status"] == "queued"
    assert result["job_id"].startswith("hook-previews:clip-1:")
    args, kwargs = queue.enqueue_job.call_args
    assert args[0] == "generate_hook_previews_task"
    assert args[1:3] == ("task-1", "clip-1")
    assert kwargs["_job_id"] == result["job_id"]
    stored = json.loads(redis.store[f"clip_rerender:{result['job_id']}"])
    assert stored["status"] == "queued"
    assert stored["kind"] == "hook_previews"


@pytest.mark.asyncio
async def test_generate_route_missing_clip_is_404(monkeypatch, fake_queue):
    _stub_route_service(monkeypatch, None)
    queue, _redis = fake_queue

    with pytest.raises(HTTPException) as error:
        await task_routes.generate_clip_hook_variants(
            "task-1", "clip-1", _request({}, queue), db=None
        )
    assert error.value.status_code == 404
    queue.enqueue_job.assert_not_called()


@pytest.mark.asyncio
async def test_generation_status_route_returns_recorded_outcome(
    monkeypatch, fake_queue
):
    from src.workers.job_queue import set_rerender_outcome

    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, redis = fake_queue
    job_id = "hook-previews:clip-1:abc123"
    await set_rerender_outcome(
        redis,
        job_id,
        {
            "status": "completed",
            "clip_id": "clip-1",
            "kind": "hook_previews",
            "previews": [{"id": "pv-1", "variant": "current"}],
        },
    )

    result = await task_routes.get_clip_hook_variants_status(
        "task-1", "clip-1", job_id, _request({}, queue), db=None
    )
    assert result["status"] == "completed"
    assert result["previews"][0]["variant"] == "current"

    with pytest.raises(HTTPException) as error:
        await task_routes.get_clip_hook_variants_status(
            "task-1", "clip-1", "hook-previews:other:abc123",
            _request({}, queue), db=None,
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_list_route_returns_latest_batch(monkeypatch, fake_queue):
    previews = [{"id": "pv-1", "variant": "current", "selected": True}]
    _stub_route_service(
        monkeypatch, {"id": "clip-1", "task_id": "task-1"}, previews
    )
    queue, _redis = fake_queue

    result = await task_routes.list_clip_hook_variants(
        "task-1", "clip-1", _request({}, queue), db=None
    )
    assert result["previews"] == previews


@pytest.mark.asyncio
async def test_select_route_queues_a_rerender_job(monkeypatch, fake_queue):
    preview = {
        "id": "pv-1",
        "task_id": "task-1",
        "clip_id": "clip-1",
        "variant": "cold_open",
    }
    _stub_route_service(
        monkeypatch, {"id": "clip-1", "task_id": "task-1"}, [preview]
    )
    queue, redis = fake_queue

    result = await task_routes.select_clip_hook_variant(
        "task-1", "clip-1", "pv-1", _request({}, queue), db=None
    )

    assert result["status"] == "queued"
    assert result["hook_variant"] == "cold_open"
    # The apply job polls on the re-render status route's key space.
    assert result["job_id"].startswith("clip-rerender:clip-1:")
    args, kwargs = queue.enqueue_job.call_args
    assert args[0] == "apply_hook_variant_task"
    assert args[1:4] == ("task-1", "clip-1", "pv-1")
    stored = json.loads(redis.store[f"clip_rerender:{result['job_id']}"])
    assert stored["kind"] == "hook_variant"


@pytest.mark.asyncio
async def test_select_route_unknown_preview_is_404(monkeypatch, fake_queue):
    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"}, [])
    queue, _redis = fake_queue

    with pytest.raises(HTTPException) as error:
        await task_routes.select_clip_hook_variant(
            "task-1", "clip-1", "nope", _request({}, queue), db=None
        )
    assert error.value.status_code == 404
    assert error.value.detail == "Hook preview not found"
    queue.enqueue_job.assert_not_called()


@pytest.mark.asyncio
async def test_preview_file_route_serves_the_file(monkeypatch, fake_queue, tmp_path):
    preview_file = tmp_path / "preview.mp4"
    preview_file.write_bytes(b"preview-bytes")
    preview = {
        "id": "pv-1",
        "task_id": "task-1",
        "clip_id": "clip-1",
        "variant": "current",
        "file_name": "preview.mp4",
        "file_path": str(preview_file),
    }
    _stub_route_service(
        monkeypatch, {"id": "clip-1", "task_id": "task-1"}, [preview]
    )
    queue, _redis = fake_queue

    response = await task_routes.get_clip_hook_variant_file(
        "task-1", "clip-1", "pv-1", _request({}, queue), db=None
    )
    assert Path(response.path) == preview_file

    with pytest.raises(HTTPException) as error:
        await task_routes.get_clip_hook_variant_file(
            "task-1", "clip-1", "nope", _request({}, queue), db=None
        )
    assert error.value.status_code == 404


# --- Worker jobs ------------------------------------------------------------------


def _stub_worker(monkeypatch, *, generate=None, apply=None):
    class StubTaskService:
        def __init__(self, _db):
            pass

        async def generate_hook_previews(self, *args, **kwargs):
            result = generate(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result

        async def apply_hook_variant(self, *args, **kwargs):
            result = apply(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result

    @asynccontextmanager
    async def fake_session():
        yield object()

    monkeypatch.setattr("src.database.AsyncSessionLocal", fake_session)
    monkeypatch.setattr(
        "src.runtime_settings.load_runtime_settings_cache", AsyncMock()
    )
    monkeypatch.setattr("src.services.task_service.TaskService", StubTaskService)


@pytest.mark.asyncio
async def test_worker_generate_records_completed_outcome(monkeypatch):
    _stub_worker(
        monkeypatch,
        generate=AsyncMock(
            return_value={"batch_id": "b-1", "previews": [{"id": "pv-1"}]}
        ),
    )
    redis = FakeRedis()
    ctx = {"redis": redis, "job_id": "hook-previews:clip-1:abc123"}

    outcome = await worker_tasks.generate_hook_previews_task(
        ctx, "task-1", "clip-1"
    )

    assert outcome["status"] == "completed"
    assert outcome["previews"] == [{"id": "pv-1"}]
    stored = json.loads(redis.store["clip_rerender:hook-previews:clip-1:abc123"])
    assert stored["status"] == "completed"


@pytest.mark.asyncio
async def test_worker_generate_records_failure_without_raising(monkeypatch):
    _stub_worker(
        monkeypatch,
        generate=AsyncMock(side_effect=RuntimeError("source unavailable")),
    )
    redis = FakeRedis()
    ctx = {"redis": redis, "job_id": "hook-previews:clip-1:abc123"}

    outcome = await worker_tasks.generate_hook_previews_task(
        ctx, "task-1", "clip-1"
    )

    assert outcome["status"] == "failed"
    assert "source unavailable" in outcome["error"]


@pytest.mark.asyncio
async def test_worker_apply_records_the_refreshed_clip(monkeypatch):
    _stub_worker(
        monkeypatch,
        apply=AsyncMock(
            return_value={"id": "clip-1", "hook_variant": "cold_open"}
        ),
    )
    redis = FakeRedis()
    ctx = {"redis": redis, "job_id": "clip-rerender:clip-1:abc123"}

    outcome = await worker_tasks.apply_hook_variant_task(
        ctx, "task-1", "clip-1", "pv-1"
    )

    assert outcome["status"] == "completed"
    assert outcome["hook_variant"] == "cold_open"
    assert outcome["clip"]["id"] == "clip-1"
    stored = json.loads(redis.store["clip_rerender:clip-rerender:clip-1:abc123"])
    assert stored["status"] == "completed"
