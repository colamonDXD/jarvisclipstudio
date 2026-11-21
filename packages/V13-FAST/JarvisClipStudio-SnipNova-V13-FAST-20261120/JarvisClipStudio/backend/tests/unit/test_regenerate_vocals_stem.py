"""Settings regeneration carries the vocals stem (N3 adjacent gap).

``regenerate_all_clips_for_task`` re-rendered through
``video_service.create_video_clips`` -> ``create_clips_with_transitions``
-> ``create_clips_from_segments``, a legacy path that took no
``vocals_stem`` at all — so regenerating the clips of a
separation-opt-in task cut them from the raw mix while the originals
were cut from clean vocals. These tests drive the real service code
and the real chain with recording fakes and assert the stem reaches
the leaf render — and that a non-opted-in task's call shape is
unchanged at every seam.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.clip_source_map import save_clip_source_ranges
from src.media import separation as separation_module
from src.services.task_service import TaskService
from test_clip_rerender import FakeClipRepo, _clip_dict, edit_boundary  # noqa: F401  (fixture re-export)


# --- Regeneration service harness -------------------------------------------
# Mirrors test_rerender_vocals_stem._service, but the video fake records
# the create_video_clips call instead of a single-clip render.


class RegenRecordingVideoService:
    """Records the batch render call; transcription/analysis must never run."""

    def __init__(self, source_path: Path):
        self.source_path = source_path
        self.download_calls = []
        self.create_video_clips_calls = []
        self.generate_transcript = AsyncMock(
            side_effect=AssertionError("regeneration must not transcribe")
        )
        self.analyze_transcript = AsyncMock(
            side_effect=AssertionError("regeneration must not re-analyse")
        )

    async def download_video(self, url, quality=None, task_id=None, ranges=None):
        self.download_calls.append(
            {"url": url, "quality": quality, "task_id": task_id, "ranges": ranges}
        )
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
        # vocals_stem is deliberately NOT a named parameter: recording
        # whether the kwarg was passed at all is the assertion.
        self.create_video_clips_calls.append(
            {
                "video_path": video_path,
                "segments": segments,
                "vocals_stem": kwargs.get("vocals_stem"),
                "vocals_stem_passed": "vocals_stem" in kwargs,
            }
        )
        return [
            {
                "clip_id": 1,
                "filename": "clip_regenerated.mp4",
                "path": str(video_path.parent / "clip_regenerated.mp4"),
                "start_time": "00:10",
                "end_time": "00:20",
                "duration": 10.0,
                "text": "the truth is the data shows 42 percent growth",
                "relevance_score": 0.9,
                "reasoning": "Regenerated with updated settings",
            }
        ]


class RegenFakeClipRepo(FakeClipRepo):
    def __init__(self, clip):
        super().__init__(clip)
        self.deleted_task_ids = []
        self.created = []

    async def get_clips_by_task(self, _db, task_id):
        return [dict(self.clip)] if self.clip is not None else []

    async def delete_clips_by_task(self, _db, task_id):
        self.deleted_task_ids.append(task_id)

    async def create_clip(self, _db, **kwargs):
        self.created.append(kwargs)
        return f"clip-new-{len(self.created)}"


class RegenFakeTaskRepo:
    def __init__(self, task):
        self.task = task
        self.clip_updates = []

    async def get_task_by_id(self, _db, _task_id):
        return self.task

    async def update_task_clips(self, _db, task_id, clip_ids):
        self.clip_updates.append((task_id, clip_ids))


def _regen_service(tmp_path, monkeypatch, *, task_fields=None):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 20.0)])

    service = TaskService(db=None)
    clip_repo = RegenFakeClipRepo(_clip_dict(clip_path))
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

    service.task_repo = RegenFakeTaskRepo(task)
    service.cache_repo = SimpleNamespace(get_cache=AsyncMock(return_value=None))
    service.video_service = RegenRecordingVideoService(source_path)
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


# --- regenerate_all_clips_for_task -------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_opted_in_task_passes_stem_to_batch_render(
    edit_boundary, monkeypatch, tmp_path
):
    """A regeneration of a separation-opt-in task cuts from the stem."""
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    seen_sources = []

    def fake_separate(source_path):
        seen_sources.append(source_path)
        return stem

    monkeypatch.setattr(separation_module, "separate_vocals", fake_separate)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    service, clip_repo = _regen_service(
        tmp_path, monkeypatch, task_fields={"separation_opt_in": True}
    )

    await service.regenerate_all_clips_for_task(
        "task-1", None, None, None, "default"
    )

    calls = service.video_service.create_video_clips_calls
    assert len(calls) == 1
    assert calls[0]["vocals_stem_passed"] is True
    assert calls[0]["vocals_stem"] == stem
    # Resolved once for the regeneration, against the prepared source.
    assert seen_sources == [tmp_path / "source.mp4"]
    # The regeneration still replaces the clips.
    assert clip_repo.deleted_task_ids == ["task-1"]
    assert len(clip_repo.created) == 1
    assert clip_repo.created[0]["filename"] == "clip_regenerated.mp4"
    assert service.task_repo.clip_updates == [("task-1", ["clip-new-1"])]


@pytest.mark.asyncio
async def test_regenerate_non_opted_in_task_passes_no_stem(
    edit_boundary, monkeypatch, tmp_path
):
    """Non-opt-in regenerations keep the exact historical call shape."""
    def forbidden(_path):  # pragma: no cover - guard
        raise AssertionError("separator must not run for a non-opted task")

    monkeypatch.setattr(separation_module, "separate_vocals", forbidden)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    service, clip_repo = _regen_service(tmp_path, monkeypatch)

    await service.regenerate_all_clips_for_task(
        "task-1", None, None, None, "default"
    )

    calls = service.video_service.create_video_clips_calls
    assert len(calls) == 1
    assert calls[0]["vocals_stem_passed"] is False
    assert calls[0]["vocals_stem"] is None
    assert clip_repo.deleted_task_ids == ["task-1"]
    assert len(clip_repo.created) == 1


@pytest.mark.asyncio
async def test_regenerate_opted_in_separation_failure_renders_original(
    edit_boundary, monkeypatch, tmp_path
):
    """Separator returns nothing: the regeneration proceeds on the original
    audio (no kwarg) and still succeeds — the never-fail contract."""
    monkeypatch.setattr(separation_module, "separate_vocals", lambda _path: None)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    service, clip_repo = _regen_service(
        tmp_path, monkeypatch, task_fields={"separation_opt_in": True}
    )

    await service.regenerate_all_clips_for_task(
        "task-1", None, None, None, "default"
    )

    calls = service.video_service.create_video_clips_calls
    assert len(calls) == 1
    assert calls[0]["vocals_stem_passed"] is False
    assert clip_repo.deleted_task_ids == ["task-1"]
    assert len(clip_repo.created) == 1


@pytest.mark.asyncio
async def test_regenerate_opted_in_flag_off_never_separates(
    edit_boundary, monkeypatch, tmp_path
):
    def forbidden(_path):  # pragma: no cover - guard
        raise AssertionError("separator must not run while the flag is off")

    monkeypatch.setattr(separation_module, "separate_vocals", forbidden)
    monkeypatch.delenv("SEPARATION_ENABLED", raising=False)
    service, clip_repo = _regen_service(
        tmp_path, monkeypatch, task_fields={"separation_opt_in": True}
    )

    await service.regenerate_all_clips_for_task(
        "task-1", None, None, None, "default"
    )

    calls = service.video_service.create_video_clips_calls
    assert len(calls) == 1
    assert calls[0]["vocals_stem_passed"] is False
    assert len(clip_repo.created) == 1


# --- The chain below the service ---------------------------------------------
# Each seam forwards the stem; the leaf omits the kwarg entirely when
# there is no stem, so the no-separation render call is byte-identical.


@pytest.mark.asyncio
async def test_create_video_clips_forwards_stem(monkeypatch, tmp_path):
    """create_video_clips hands the stem to create_clips_with_transitions."""
    from src.services.video_service import VideoService

    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    calls = []

    def fake_transitions(*args, **kwargs):
        calls.append(kwargs)
        return [{"filename": "clip-1.mp4", "path": str(tmp_path / "clip-1.mp4")}]

    monkeypatch.setattr(
        "src.services.video_service.create_clips_with_transitions",
        fake_transitions,
    )
    service = VideoService()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    result = await service.create_video_clips(
        source,
        [{"start_time": "00:00", "end_time": "00:10", "text": "hook"}],
        vocals_stem=stem,
    )

    assert result[0]["filename"] == "clip-1.mp4"
    assert len(calls) == 1
    assert calls[0]["vocals_stem"] == stem


@pytest.mark.asyncio
async def test_create_video_clips_without_stem_forwards_none(monkeypatch, tmp_path):
    """Without a stem the forward is the historical default (None)."""
    from src.services.video_service import VideoService

    calls = []

    def fake_transitions(*args, **kwargs):
        calls.append(kwargs)
        return [{"filename": "clip-1.mp4", "path": str(tmp_path / "clip-1.mp4")}]

    monkeypatch.setattr(
        "src.services.video_service.create_clips_with_transitions",
        fake_transitions,
    )
    service = VideoService()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    await service.create_video_clips(
        source,
        [{"start_time": "00:00", "end_time": "00:10", "text": "hook"}],
    )

    assert len(calls) == 1
    assert calls[0]["vocals_stem"] is None


def test_create_clips_with_transitions_forwards_stem(monkeypatch, tmp_path):
    """The wrapper hands the stem to create_clips_from_segments."""
    from src import video_utils

    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    calls = []

    def fake_segments(*args, **kwargs):
        calls.append(kwargs)
        return [{"filename": "clip-1.mp4", "path": str(tmp_path / "clip-1.mp4")}]

    monkeypatch.setattr(
        "src.video_utils.create_clips_from_segments", fake_segments
    )

    result = video_utils.create_clips_with_transitions(
        tmp_path / "source.mp4",
        [{"start_time": "00:00", "end_time": "00:10", "text": "hook"}],
        tmp_path,
        vocals_stem=stem,
    )

    assert result[0]["filename"] == "clip-1.mp4"
    assert len(calls) == 1
    assert calls[0]["vocals_stem"] == stem


def _leaf_segment():
    return {
        "start_time": "00:10",
        "end_time": "00:20",
        "text": "hello world",
        "relevance_score": 0.9,
        "reasoning": "test",
        # Stored ranges: no snapping, no sentence extension, no teaser.
        "keep_ranges": [(10.0, 20.0)],
    }


def test_create_clips_from_segments_passes_stem_to_leaf(monkeypatch, tmp_path):
    """The leaf render is called with the stem for an opted-in task."""
    from src import video_utils

    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    calls = []

    def fake_render(*args, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr("src.video_utils.create_optimized_clip", fake_render)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    infos = video_utils.create_clips_from_segments(
        source, [_leaf_segment()], tmp_path, vocals_stem=stem
    )

    assert len(infos) == 1
    assert infos[0]["duration"] == 10.0
    assert len(calls) == 1
    assert calls[0]["vocals_stem"] == stem


def test_create_clips_from_segments_omits_stem_kwarg_when_none(monkeypatch, tmp_path):
    """No stem: the leaf call keeps its exact historical shape — the
    ``vocals_stem`` kwarg is absent, not merely None."""
    from src import video_utils

    calls = []

    def fake_render(*args, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr("src.video_utils.create_optimized_clip", fake_render)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    infos = video_utils.create_clips_from_segments(
        source, [_leaf_segment()], tmp_path
    )

    assert len(infos) == 1
    assert len(calls) == 1
    assert "vocals_stem" not in calls[0]
