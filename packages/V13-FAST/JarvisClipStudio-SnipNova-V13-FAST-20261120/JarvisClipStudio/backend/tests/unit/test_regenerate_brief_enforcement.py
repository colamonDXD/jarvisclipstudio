"""Regeneration brief enforcement (campaign brief enforcement lane).

``regenerate_all_clips_for_task`` rebuilt segments from stored clip
ranges and rendered them through a chain that carried no brief ranges
at all — and it deleted the old clip rows and created fresh ones
without re-running compliance, so a brief's ``needs_review`` flags were
silently lost on every regeneration.

These tests drive the real service code with recording fakes (the
test_regenerate_vocals_stem.py harness pattern) and assert:

- a brief task's regeneration passes the brief's allowed/excluded
  ranges into the batch render call;
- a non-brief task's render call shape is unchanged (no range kwargs);
- each regenerated clip gets a fresh compliance stamp, so the flag
  that stamped the original clip stamps its replacement too.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.clip_source_map import save_clip_source_ranges
from src.services.task_service import TaskService
from test_clip_rerender import FakeClipRepo, _clip_dict, edit_boundary  # noqa: F401  (fixture re-export)


class BriefRecordingVideoService:
    """Records the batch render call; writes the regenerated file +
    sidecar so the compliance check reads real render facts."""

    def __init__(self, source_path: Path):
        self.source_path = source_path
        self.create_video_clips_calls = []
        self.generate_transcript = AsyncMock(
            side_effect=AssertionError("regeneration must not transcribe")
        )
        self.analyze_transcript = AsyncMock(
            side_effect=AssertionError("regeneration must not re-analyse")
        )

    async def download_video(self, url, quality=None, task_id=None, ranges=None):
        return str(self.source_path)

    async def prepare_source(self, path):
        return path

    async def create_video_clips(
        self,
        video_path,
        segments,
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
        **kwargs,
    ):
        # allowed_ranges / excluded_ranges are deliberately NOT named
        # parameters: recording whether the kwargs were passed at all
        # is the assertion (the conditional-kwarg pattern).
        self.create_video_clips_calls.append(
            {
                "segments": segments,
                "allowed_ranges": kwargs.get("allowed_ranges"),
                "allowed_ranges_passed": "allowed_ranges" in kwargs,
                "excluded_ranges": kwargs.get("excluded_ranges"),
                "excluded_ranges_passed": "excluded_ranges" in kwargs,
            }
        )
        clip_path = video_path.parent / "clip_regenerated.mp4"
        clip_path.write_bytes(b"clip")
        save_clip_source_ranges(clip_path, [(10.0, 20.0)])
        return [
            {
                "clip_id": 1,
                "filename": "clip_regenerated.mp4",
                "path": str(clip_path),
                "start_time": "00:10",
                "end_time": "00:20",
                "duration": 10.0,
                "text": "the truth is the data shows 42 percent growth",
                "relevance_score": 0.9,
                "reasoning": "Regenerated with updated settings",
            }
        ]


class BriefFakeClipRepo(FakeClipRepo):
    def __init__(self, clip):
        super().__init__(clip)
        self.deleted_task_ids = []
        self.created = []
        self.created_rows = {}
        self.compliance_updates = []

    async def get_clips_by_task(self, _db, task_id):
        return [dict(self.clip)] if self.clip is not None else []

    async def get_clip_by_id(self, _db, clip_id):
        if clip_id in self.created_rows:
            return dict(self.created_rows[clip_id])
        return await super().get_clip_by_id(_db, clip_id)

    async def delete_clips_by_task(self, _db, task_id):
        self.deleted_task_ids.append(task_id)

    async def create_clip(self, _db, **kwargs):
        self.created.append(kwargs)
        clip_id = f"clip-new-{len(self.created)}"
        self.created_rows[clip_id] = {
            "id": clip_id,
            "task_id": kwargs.get("task_id"),
            "file_path": kwargs.get("file_path"),
            "filename": kwargs.get("filename"),
            "start_time": kwargs.get("start_time"),
            "end_time": kwargs.get("end_time"),
            "duration": kwargs.get("duration"),
            "text": kwargs.get("text"),
            "hook_title": kwargs.get("hook_title"),
            "clip_plan": None,
        }
        return clip_id

    async def update_clip_compliance(
        self, _db, clip_id, compliance_status, compliance_report
    ):
        self.compliance_updates.append(
            {
                "clip_id": clip_id,
                "status": compliance_status,
                "report": compliance_report,
            }
        )


class BriefFakeTaskRepo:
    def __init__(self, task):
        self.task = task
        self.clip_updates = []

    async def get_task_by_id(self, _db, _task_id):
        return self.task

    async def update_task_clips(self, _db, task_id, clip_ids):
        self.clip_updates.append((task_id, clip_ids))


BRIEF = {
    "version": 2,
    "requirements": [],
    "excluded_ranges": [{"start": 25, "end": 30}],
    "flag_points": [
        {"at": 15, "category": "licence_plate", "note": "regen fixture flag"}
    ],
}


def _regen_service(tmp_path, monkeypatch, *, task_fields=None):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 20.0)])

    service = TaskService(db=None)
    clip_repo = BriefFakeClipRepo(_clip_dict(clip_path))
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

    service.task_repo = BriefFakeTaskRepo(task)
    service.cache_repo = SimpleNamespace(get_cache=AsyncMock(return_value=None))
    service.video_service = BriefRecordingVideoService(source_path)
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


@pytest.mark.asyncio
async def test_regenerate_brief_task_passes_ranges_to_batch_render(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo = _regen_service(
        tmp_path, monkeypatch, task_fields={"content_brief": BRIEF}
    )

    await service.regenerate_all_clips_for_task(
        "task-1", None, None, None, "default"
    )

    calls = service.video_service.create_video_clips_calls
    assert len(calls) == 1
    assert calls[0]["excluded_ranges_passed"] is True
    assert calls[0]["excluded_ranges"] == [(25.0, 30.0)]
    # The fixture brief carries no allowed ranges: the kwarg stays
    # absent rather than passing an empty/None restriction.
    assert calls[0]["allowed_ranges_passed"] is False


@pytest.mark.asyncio
async def test_regenerate_non_brief_task_passes_no_ranges(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo = _regen_service(tmp_path, monkeypatch)

    await service.regenerate_all_clips_for_task(
        "task-1", None, None, None, "default"
    )

    calls = service.video_service.create_video_clips_calls
    assert len(calls) == 1
    assert calls[0]["allowed_ranges_passed"] is False
    assert calls[0]["excluded_ranges_passed"] is False


@pytest.mark.asyncio
async def test_regenerate_brief_task_restamps_compliance_with_flag(
    edit_boundary, monkeypatch, tmp_path
):
    service, clip_repo = _regen_service(
        tmp_path, monkeypatch, task_fields={"content_brief": BRIEF}
    )

    await service.regenerate_all_clips_for_task(
        "task-1", None, None, None, "default"
    )

    assert clip_repo.deleted_task_ids == ["task-1"]
    assert len(clip_repo.created) == 1
    updates = clip_repo.compliance_updates
    assert len(updates) == 1
    assert updates[0]["clip_id"] == "clip-new-1"
    report = updates[0]["report"]
    assert report["needs_review"] is True
    assert [f["category"] for f in report["flags"]] == ["licence_plate"]
    assert report["flags"][0]["at_seconds"] == 15.0


@pytest.mark.asyncio
async def test_regenerate_non_brief_task_writes_no_compliance(
    edit_boundary, monkeypatch, tmp_path
):
    service, clip_repo = _regen_service(tmp_path, monkeypatch)

    await service.regenerate_all_clips_for_task(
        "task-1", None, None, None, "default"
    )

    assert clip_repo.compliance_updates == []
