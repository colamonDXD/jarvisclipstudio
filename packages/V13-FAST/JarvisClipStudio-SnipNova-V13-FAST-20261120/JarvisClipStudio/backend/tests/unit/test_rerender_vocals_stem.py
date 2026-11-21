"""Re-render seam carries the vocals stem for separation-opt-in tasks (N3).

The main pipeline resolves the source's vocals stem once per task and
passes it to every render; the re-render seam never did, so a
re-rendered clip from a music-heavy source was cut from the raw mix
while the original was cut from clean vocals. Hook previews render
through the same seam and shared the gap. These tests drive the real
service code with the re-render fakes and assert the stem reaches the
render call — and that a non-opted-in task's call shape is unchanged.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.media import separation as separation_module
from src.services.task_service import TaskService
from test_clip_rerender import FakeClipRepo, _clip_dict, edit_boundary  # noqa: F401  (fixture re-export)

from src.clip_source_map import save_clip_source_ranges


# --- Shared resolution: resolve_render_vocals_stem --------------------------


def test_resolve_flag_off_returns_none_without_calling_separator(monkeypatch):
    def forbidden(_path):  # pragma: no cover - guard
        raise AssertionError("separator must not run for a non-opted task")

    monkeypatch.setattr(separation_module, "separate_vocals", forbidden)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")

    assert separation_module.resolve_render_vocals_stem(False, Path("/tmp/source.mp4")) is None


def test_resolve_disabled_returns_none_without_calling_separator(monkeypatch):
    def forbidden(_path):  # pragma: no cover - guard
        raise AssertionError("separator must not run while the flag is off")

    monkeypatch.setattr(separation_module, "separate_vocals", forbidden)
    monkeypatch.delenv("SEPARATION_ENABLED", raising=False)

    assert separation_module.resolve_render_vocals_stem(True, Path("/tmp/source.mp4")) is None


def test_resolve_none_path_returns_none(monkeypatch):
    def forbidden(_path):  # pragma: no cover - guard
        raise AssertionError("separator must not run without a source")

    monkeypatch.setattr(separation_module, "separate_vocals", forbidden)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")

    assert separation_module.resolve_render_vocals_stem(True, None) is None


def test_resolve_separator_failure_returns_none(monkeypatch):
    monkeypatch.setattr(
        separation_module, "separate_vocals", lambda _path: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setenv("SEPARATION_ENABLED", "true")

    assert separation_module.resolve_render_vocals_stem(True, Path("/tmp/source.mp4")) is None


def test_resolve_returns_separator_stem(tmp_path, monkeypatch):
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    monkeypatch.setattr(separation_module, "separate_vocals", lambda path: stem)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")

    assert separation_module.resolve_render_vocals_stem(True, tmp_path / "source.mp4") == stem


# --- Re-render service harness ----------------------------------------------
# Mirrors test_clip_rerender._service, but the video fake records the
# vocals_stem kwarg instead of rejecting it.


class StemRecordingVideoService:
    """Records the render call; transcription/analysis must never run."""

    def __init__(self, source_path: Path):
        self.source_path = source_path
        self.download_calls = []
        self.render_call = None
        self.generate_transcript = AsyncMock(
            side_effect=AssertionError("re-render must not transcribe")
        )
        self.analyze_transcript = AsyncMock(
            side_effect=AssertionError("re-render must not re-analyse")
        )

    async def download_video(self, url, quality=None, task_id=None, ranges=None):
        self.download_calls.append(
            {"url": url, "quality": quality, "task_id": task_id, "ranges": ranges}
        )
        return str(self.source_path)

    async def prepare_source(self, path):
        return path

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
        vocals_stem=None,
        **_ignored,
    ):
        self.render_call = {
            "video_path": video_path,
            "segment": segment,
            "clip_index": clip_index,
            "output_dir": output_dir,
            "caption_template": caption_template,
            "output_format": output_format,
            "edl": edl,
            "niche_override": niche_override,
            "project_niche_override": project_niche_override,
            "classifier_niche": classifier_niche,
            "vocals_stem": vocals_stem,
            "vocals_stem_passed": vocals_stem is not None,
        }
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered = output_dir / "clip_rerendered.mp4"
        rendered.write_bytes(b"rendered")
        return {
            "filename": rendered.name,
            "path": str(rendered),
            "start_time": segment["start_time"],
            "end_time": segment["end_time"],
            "duration": 10.0,
            "text": segment.get("text", ""),
        }


def _service(tmp_path, monkeypatch, *, task_fields=None):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 20.0)])

    service = TaskService(db=None)
    clip_repo = FakeClipRepo(_clip_dict(clip_path))
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
    service.cache_repo = SimpleNamespace(get_cache=AsyncMock(return_value=None))
    service.video_service = StemRecordingVideoService(source_path)
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


# --- rerender_clip -----------------------------------------------------------


@pytest.mark.asyncio
async def test_rerender_opted_in_task_passes_stem_to_render(
    edit_boundary, monkeypatch, tmp_path
):
    """N3: a re-render of a separation-opt-in task cuts from the stem."""
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    seen_sources = []

    def fake_separate(source_path):
        seen_sources.append(source_path)
        return stem

    monkeypatch.setattr(separation_module, "separate_vocals", fake_separate)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    service, _clip_repo = _service(
        tmp_path, monkeypatch, task_fields={"separation_opt_in": True}
    )

    refreshed = await service.rerender_clip("task-1", "clip-1", "podcast_news")

    render = service.video_service.render_call
    assert render is not None
    assert render["vocals_stem_passed"] is True
    assert render["vocals_stem"] == stem
    # Resolved against the prepared source the render consumes.
    assert seen_sources == [tmp_path / "source.mp4"]
    assert refreshed["niche_profile"] == "podcast_news"


@pytest.mark.asyncio
async def test_rerender_non_opted_in_task_passes_no_stem(
    edit_boundary, monkeypatch, tmp_path
):
    """Non-opt-in re-renders keep the exact historical call shape."""
    def forbidden(_path):  # pragma: no cover - guard
        raise AssertionError("separator must not run for a non-opted task")

    monkeypatch.setattr(separation_module, "separate_vocals", forbidden)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    service, _clip_repo = _service(tmp_path, monkeypatch)

    await service.rerender_clip("task-1", "clip-1", "podcast_news")

    render = service.video_service.render_call
    assert render is not None
    assert render["vocals_stem_passed"] is False
    assert render["vocals_stem"] is None


@pytest.mark.asyncio
async def test_rerender_opted_in_separation_failure_renders_original(
    edit_boundary, monkeypatch, tmp_path
):
    """Separator returns nothing: the re-render proceeds on the original
    audio (no kwarg) and still succeeds — the never-fail contract."""
    monkeypatch.setattr(separation_module, "separate_vocals", lambda _path: None)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    service, clip_repo = _service(
        tmp_path, monkeypatch, task_fields={"separation_opt_in": True}
    )

    refreshed = await service.rerender_clip("task-1", "clip-1", "podcast_news")

    render = service.video_service.render_call
    assert render is not None
    assert render["vocals_stem_passed"] is False
    assert clip_repo.updates and clip_repo.updates[0]["filename"] == "clip_rerendered.mp4"
    assert refreshed["niche_profile"] == "podcast_news"


@pytest.mark.asyncio
async def test_rerender_opted_in_flag_off_never_separates(
    edit_boundary, monkeypatch, tmp_path
):
    def forbidden(_path):  # pragma: no cover - guard
        raise AssertionError("separator must not run while the flag is off")

    monkeypatch.setattr(separation_module, "separate_vocals", forbidden)
    monkeypatch.delenv("SEPARATION_ENABLED", raising=False)
    service, _clip_repo = _service(
        tmp_path, monkeypatch, task_fields={"separation_opt_in": True}
    )

    await service.rerender_clip("task-1", "clip-1", "podcast_news")

    render = service.video_service.render_call
    assert render is not None
    assert render["vocals_stem_passed"] is False


# --- generate_hook_previews ---------------------------------------------------
# Previews render through the same seam, so they share the gap; the
# batch resolves the stem once and every variant renders from it.


def _opt_in_hook_service(service):
    """Flip the hook-preview harness task to separation-opt-in."""
    orig_get = service.task_repo.get_task_by_id

    async def opted_in_get(db, task_id):
        row = await orig_get(db, task_id)
        return {**row, "separation_opt_in": True}

    service.task_repo.get_task_by_id = opted_in_get
    return service


@pytest.mark.asyncio
async def test_hook_previews_opted_in_task_passes_stem_to_every_variant(
    edit_boundary, monkeypatch, tmp_path
):
    from test_hook_variants import _service as hook_service, _service_plan

    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    seen_sources = []

    def fake_separate(source_path):
        seen_sources.append(source_path)
        return stem

    monkeypatch.setattr(separation_module, "separate_vocals", fake_separate)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    service, _clip_repo = hook_service(tmp_path, monkeypatch, plan=_service_plan())
    _opt_in_hook_service(service)

    # The hook harness fake rejects unknown kwargs: record the stem and
    # strip it before delegating, so the render itself is unchanged.
    stems_seen = []
    orig_render = service.video_service.create_single_clip

    async def recording_render(*args, **kwargs):
        stems_seen.append(kwargs.get("vocals_stem"))
        kwargs.pop("vocals_stem", None)
        return await orig_render(*args, **kwargs)

    service.video_service.create_single_clip = recording_render

    result = await service.generate_hook_previews("task-1", "clip-1")

    assert [p["variant"] for p in result["previews"]] == [
        "current",
        "cold_open",
        "punch",
    ]
    assert len(stems_seen) == 3
    assert all(stem_seen == stem for stem_seen in stems_seen)
    # Resolved once for the batch, against the prepared source.
    assert seen_sources == [tmp_path / "source.mp4"]


@pytest.mark.asyncio
async def test_hook_previews_non_opted_in_task_passes_no_stem(
    edit_boundary, monkeypatch, tmp_path
):
    from test_hook_variants import _service as hook_service, _service_plan

    def forbidden(_path):  # pragma: no cover - guard
        raise AssertionError("separator must not run for a non-opted task")

    monkeypatch.setattr(separation_module, "separate_vocals", forbidden)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    service, _clip_repo = hook_service(tmp_path, monkeypatch, plan=_service_plan())

    stems_seen = []
    orig_render = service.video_service.create_single_clip

    async def recording_render(*args, **kwargs):
        stems_seen.append("vocals_stem" in kwargs)
        kwargs.pop("vocals_stem", None)
        return await orig_render(*args, **kwargs)

    service.video_service.create_single_clip = recording_render

    result = await service.generate_hook_previews("task-1", "clip-1")

    assert len(result["previews"]) == 3
    assert stems_seen == [False, False, False]
