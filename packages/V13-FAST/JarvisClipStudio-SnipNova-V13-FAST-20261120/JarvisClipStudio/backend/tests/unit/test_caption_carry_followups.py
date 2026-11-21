"""Caption-carry follow-ups (F-B items 1+2, QC stretch item 3).

Fail-first tests for the gaps the F-B lane recorded but did not fix:

1. PATCH-route gap: ``update_clip_captions`` persists position /
   position_y to the ``caption_settings`` sidecar key only, which
   ``_build_rerender_edl`` never reads — so a PATCH position does not
   survive a later re-render. The PATCH must also write the edit plan,
   which ``rerender_clip`` already carries forward (F-B).
2. Hook-preview bypass: ``generate_hook_previews`` calls
   ``_build_rerender_edl`` directly with ``None`` for
   caption_overrides and never loads the editor plan.
3. QC corrective path (stretch): ``_qc_persisted_clip`` funnels into
   ``rerender_clip`` with no overrides — the stored position must
   survive that re-render too.

The service tests drive the real service code with the re-render
fakes from ``test_clip_rerender`` / ``test_hook_variants``, the way
the F-B lane's tests assert the EDL each render uses.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auto_director.clip_edits import build_edit_plan
from src.clip_source_map import (
    load_clip_caption_settings,
    load_clip_editor_settings,
    save_clip_editor_settings,
    save_clip_source_ranges,
)
from src.config import Config
from src.services import clip_service as clip_service_module
from src.services.task_service import TaskService

from test_clip_rerender import FakeClipRepo, FakeVideoService, _clip_dict


@pytest.fixture
def edit_boundary(monkeypatch):
    """Keep service unit tests independent of the PostgreSQL edit boundary."""

    @asynccontextmanager
    async def transaction(_db, _task_id, **_kwargs):
        yield

    monkeypatch.setattr(
        "src.repositories.edit_transaction.task_edit_transaction", transaction
    )
    # task_service holds its own imported reference (the QC path calls it
    # directly); the source-module patch alone does not reach it.
    monkeypatch.setattr(
        "src.services.task_service.task_edit_transaction", transaction
    )


def _store_caption_plan(clip_path: Path, captions: dict, **extra) -> None:
    save_clip_editor_settings(
        clip_path,
        build_edit_plan(
            [{"index": 0, "start": 0.0, "end": 10.0}],
            captions=captions,
            **extra,
        ),
    )


# --- Shared PATCH + re-render service ---------------------------------------
#
# One service drives both halves of item 1: the PATCH render is faked the
# way test_task_service fakes it (cached upload source, fake overlay +
# fake clean render), and the re-render half uses the FakeVideoService
# from test_clip_rerender so the EDL can be asserted exactly.


def _patch_service(tmp_path, monkeypatch, *, task_fields=None):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 12.0)])

    config = Config()
    config.temp_dir = str(tmp_path)
    service = TaskService(db=None, config=config)
    clip = _clip_dict(clip_path)
    clip.update(
        {
            "start_time": "00:10",
            "end_time": "00:12",
            "duration": 2.0,
        }
    )
    clip_repo = FakeClipRepo(clip)
    service.clip_repo = clip_repo

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
    }
    if task_fields:
        task.update(task_fields)

    class FakeTaskRepo:
        async def get_task_by_id(self, _db, _task_id):
            return task

    service.task_repo = FakeTaskRepo()
    service.cache_repo = SimpleNamespace(
        get_cache=AsyncMock(return_value={"video_path": str(source_path)})
    )
    service.video_service = FakeVideoService(source_path)
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

    output_path = tmp_path / "clips" / "edited.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def fake_overlay(*args, **kwargs):
        return output_path

    def fake_render(*args, **kwargs):
        args[3].write_bytes(b"clean")
        return True

    monkeypatch.setattr(clip_service_module, "overlay_custom_captions", fake_overlay)
    monkeypatch.setattr(clip_service_module, "create_optimized_clip", fake_render)
    return service, clip_repo, output_path


# --- Item 1: the caption PATCH must write the edit plan ---------------------


@pytest.mark.asyncio
async def test_patch_position_survives_later_rerender(
    edit_boundary, monkeypatch, tmp_path
):
    """F-B follow-up 1: a PATCH Top must still be Top after a re-render.

    podcast_news defaults to bottom; the PATCH says top. The plan
    written next to the PATCH output must say top, and the re-render
    EDL (no overrides, the F-B carry-forward) must carry top / 0.18.
    """
    service, clip_repo, output_path = _patch_service(tmp_path, monkeypatch)

    await service.update_clip_captions("task-1", "clip-1", "edited", "top", [])

    stored = load_clip_editor_settings(output_path)
    assert stored is not None
    assert stored.get("captions", {}).get("position") == "top"
    assert stored.get("captions", {}).get("position_y") == 0.18
    # The legacy sidecar key keeps working for the Studio-editor seed.
    legacy = load_clip_caption_settings(output_path)
    assert legacy is not None
    assert legacy.get("position") == "top"

    await service.rerender_clip("task-1", "clip-1", "podcast_news")

    render = service.video_service.render_call
    assert render is not None
    assert render["edl"]["captions"]["position"] == "top"
    assert render["edl"]["captions"]["position_y"] == 0.18


@pytest.mark.asyncio
async def test_patch_merges_into_existing_plan_without_dropping_blocks(
    edit_boundary, monkeypatch, tmp_path
):
    """F-B follow-up 1: the PATCH merges into the stored plan, it does
    not replace it — segments, the look block and an editor-set template
    survive, only the PATCH fields change."""
    service, clip_repo, output_path = _patch_service(tmp_path, monkeypatch)
    _store_caption_plan(
        tmp_path / "clip.mp4",
        {"template": "hormozi", "position": "bottom"},
        look={"transition": "fade"},
    )

    await service.update_clip_captions(
        "task-1", "clip-1", "edited", "top", [], font_size=20
    )

    stored = load_clip_editor_settings(output_path)
    assert stored is not None
    assert stored.get("captions", {}).get("template") == "hormozi"
    assert stored.get("captions", {}).get("position") == "top"
    assert stored.get("captions", {}).get("position_y") == 0.18
    assert stored.get("captions", {}).get("font_size") == 20
    assert stored.get("look", {}).get("transition") == "fade"
    assert stored.get("segments")

    await service.rerender_clip("task-1", "clip-1", "podcast_news")
    render = service.video_service.render_call
    assert render["edl"]["captions"]["position"] == "top"


@pytest.mark.asyncio
async def test_patch_still_succeeds_when_plan_write_is_impossible(
    edit_boundary, monkeypatch, tmp_path
):
    """Fail-safe: a task font size outside the valid range must never
    break the PATCH — the caption_settings sidecar is still saved and
    the PATCH returns, even though no edit plan is written."""
    service, clip_repo, output_path = _patch_service(
        tmp_path, monkeypatch, task_fields={"font_size": 200}
    )

    updated = await service.update_clip_captions(
        "task-1", "clip-1", "edited", "top", []
    )

    assert updated["id"] == "clip-1"
    legacy = load_clip_caption_settings(output_path)
    assert legacy is not None
    assert legacy.get("position") == "top"


# --- Item 2: hook previews must carry the stored caption position ----------


@pytest.mark.asyncio
async def test_hook_previews_carry_stored_caption_position(
    edit_boundary, monkeypatch, tmp_path
):
    """F-B follow-up 2: every hook preview EDL must carry the stored
    Top, not the profile-default bottom."""
    from test_hook_variants import _plan as hook_plan
    from test_hook_variants import _service as hook_service

    service, _clip_repo = hook_service(
        tmp_path, monkeypatch, plan=hook_plan(hook_ts=5.0, punch="subtle")
    )
    _store_caption_plan(tmp_path / "clip.mp4", {"position": "top"})

    result = await service.generate_hook_previews("task-1", "clip-1")

    assert [p["variant"] for p in result["previews"]] == [
        "current",
        "cold_open",
        "punch",
    ]
    calls = service.video_service.render_calls
    assert len(calls) == 3
    for call in calls:
        assert call["edl"]["captions"]["position"] == "top"
        assert call["edl"]["captions"]["position_y"] == 0.18


# --- Item 3 (stretch): the QC corrective re-render -------------------------


class QcFakeClipRepo(FakeClipRepo):
    def __init__(self, clip):
        super().__init__(clip)
        self.qc_updates = []

    async def get_clips_by_task(self, _db, task_id):
        return [dict(self.clip)] if self.clip else []

    async def update_clip_qc(self, _db, clip_id, qc_status, qc_report):
        self.qc_updates.append((qc_status, qc_report))
        self.clip = {**self.clip, "qc_status": qc_status, "qc_report": qc_report}


@pytest.mark.asyncio
async def test_qc_corrective_rerender_carries_stored_caption_position(
    edit_boundary, monkeypatch, tmp_path
):
    """F-B follow-up 3 (stretch): a corrupt clip fails QC, the
    corrective re-render runs with no overrides, and the stored Top
    must reach its EDL — the QC path funnels through rerender_clip,
    so the F-B carry-forward has to cover it."""
    from src.clip_source_map import save_clip_source_ranges as _save_ranges
    from test_clip_rerender import _service as rerender_service

    service, clip_repo = rerender_service(tmp_path, monkeypatch)
    service.clip_repo = QcFakeClipRepo(clip_repo.clip)
    # The clip file is corrupt (not an mp4) so QC hard-fails; the stored
    # plan says Top while podcast_news defaults to bottom.
    corrupt = tmp_path / "clip.mp4"
    corrupt.write_bytes(b"definitely not an mp4" * 32)
    _save_ranges(corrupt, [(10.0, 20.0)])
    _store_caption_plan(corrupt, {"position": "top"})

    await service._qc_persisted_clip(
        task_id="task-1",
        clip_id="clip-1",
        segment={"text": "the truth is the data shows 42 percent growth"},
        clip_niche_override="podcast_news",
        project_niche_override=None,
        output_format="original",
        output_short_side=None,
        music_override=None,
    )

    render = service.video_service.render_call
    assert render is not None, "QC hard-fail must trigger exactly one re-render"
    assert render["edl"]["captions"]["position"] == "top"
    assert render["edl"]["captions"]["position_y"] == 0.18
    assert [status for status, _ in service.clip_repo.qc_updates][0] == "fail"
