"""Pipeline seam for per-clip QC (TaskService._qc_persisted_clip).

The seam is exercised with fake repositories and a stubbed Lane-5 re-render,
but the QC pass itself runs the real checks against real files: a corrupt
first render must trigger exactly one re-render and a re-QC, a still-broken
re-render must leave the clip flagged (never a loop, never a failed task),
a warning must never trigger a re-render, and a QC crash must land on the
clip as qc_status "error" without raising.
"""

import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from src.services import task_service as task_service_module
from src.services.task_service import TaskService


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *args],
        check=True,
        capture_output=True,
        timeout=300,
    )


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    directory = tmp_path_factory.mktemp("clip-qc-pipeline")
    good = directory / "good.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-filter_complex", "[1:a]volume=0.5[a]",
        "-map", "0:v", "-map", "[a]",
        "-t", "3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(good),
    )
    silent = directory / "silent.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=10",
        "-f", "lavfi", "-i", "anullsrc=sample_rate=44100",
        "-map", "0:v", "-map", "1:a",
        "-t", "3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(silent),
    )
    corrupt = directory / "corrupt.mp4"
    corrupt.write_bytes(b"definitely not an mp4" * 32)
    corrupt2 = directory / "corrupt2.mp4"
    corrupt2.write_bytes(b"still not an mp4" * 32)
    return {"good": good, "silent": silent, "corrupt": corrupt, "corrupt2": corrupt2}


class FakeClipRepo:
    def __init__(self, clip: dict):
        self.clip = clip
        self.qc_updates: list[tuple[str, dict]] = []

    async def get_clip_by_id(self, _db, clip_id):
        if self.clip is None or self.clip["id"] != clip_id:
            return None
        return dict(self.clip)

    async def get_clips_by_task(self, _db, task_id):
        return [dict(self.clip)] if self.clip else []

    async def update_clip_qc(self, _db, clip_id, qc_status, qc_report):
        self.qc_updates.append((qc_status, qc_report))
        self.clip = {**self.clip, "qc_status": qc_status, "qc_report": qc_report}


@pytest.fixture
def seam(monkeypatch):
    """Neutralise the DB edit boundary and media storage for the seam."""

    @asynccontextmanager
    async def transaction(_db, _task_id, *, processing=False):
        yield

    monkeypatch.setattr(
        task_service_module, "task_edit_transaction", transaction
    )

    class _Storage:
        def local_path(self, reference, **_kwargs):
            return Path(reference)

    monkeypatch.setattr(task_service_module, "MediaStorage", _Storage)


def _clip_dict(path: Path) -> dict:
    return {
        "id": "clip-1",
        "task_id": "task-1",
        "file_path": str(path),
        "filename": path.name,
        "start_time": "00:10",
        "end_time": "00:13",
        "duration": 3.0,
        "text": "a perfectly ordinary clip",
        "clip_order": 1,
        "niche_override": None,
    }


def _service(clip: dict) -> TaskService:
    service = TaskService(db=None)
    service.clip_repo = FakeClipRepo(clip)
    return service


async def _run_qc(service: TaskService) -> None:
    await service._qc_persisted_clip(
        task_id="task-1",
        clip_id="clip-1",
        segment={"text": "a perfectly ordinary clip"},
        clip_niche_override="podcast_news",
        project_niche_override=None,
        output_format="original",
        output_short_side=None,
        music_override=None,
    )


@pytest.mark.asyncio
async def test_hard_fail_triggers_exactly_one_rerender_then_passes(seam, media):
    service = _service(_clip_dict(media["corrupt"]))
    rerender_calls = []

    async def fake_rerender(task_id, clip_id, niche_override, **kwargs):
        rerender_calls.append((task_id, clip_id, niche_override, kwargs))
        service.clip_repo.clip = {
            **service.clip_repo.clip,
            "file_path": str(media["good"]),
            "filename": media["good"].name,
        }
        return dict(service.clip_repo.clip)

    service.rerender_clip = fake_rerender
    await _run_qc(service)

    assert len(rerender_calls) == 1
    task_id, clip_id, niche_override, kwargs = rerender_calls[0]
    assert (task_id, clip_id) == ("task-1", "clip-1")
    # The re-render keeps the clip's own niche and the task's music pick.
    assert niche_override == "podcast_news"
    assert kwargs.get("music_override") is None

    updates = service.clip_repo.qc_updates
    assert [status for status, _ in updates] == ["fail", "pass"]
    final_report = updates[-1][1]
    assert final_report["attempts"] == 2
    assert final_report["retry"] == {"attempted": True, "succeeded": True}


@pytest.mark.asyncio
async def test_still_failing_after_rerender_is_flagged_not_looped(seam, media):
    service = _service(_clip_dict(media["corrupt"]))
    rerender_calls = []

    async def fake_rerender(task_id, clip_id, niche_override, **kwargs):
        rerender_calls.append(clip_id)
        service.clip_repo.clip = {
            **service.clip_repo.clip,
            "file_path": str(media["corrupt2"]),
            "filename": media["corrupt2"].name,
        }
        return dict(service.clip_repo.clip)

    service.rerender_clip = fake_rerender
    await _run_qc(service)

    assert len(rerender_calls) == 1
    updates = service.clip_repo.qc_updates
    assert [status for status, _ in updates] == ["fail", "fail"]
    assert updates[-1][1]["attempts"] == 2
    assert service.clip_repo.clip["qc_status"] == "fail"


@pytest.mark.asyncio
async def test_warning_never_triggers_a_rerender(seam, media):
    service = _service(_clip_dict(media["silent"]))
    rerender_calls = []

    async def fake_rerender(task_id, clip_id, niche_override, **kwargs):
        rerender_calls.append(clip_id)
        return {}

    service.rerender_clip = fake_rerender
    await _run_qc(service)

    assert rerender_calls == []
    updates = service.clip_repo.qc_updates
    assert [status for status, _ in updates] == ["warn"]


@pytest.mark.asyncio
async def test_failed_rerender_keeps_flagged_original(seam, media):
    service = _service(_clip_dict(media["corrupt"]))

    async def broken_rerender(task_id, clip_id, niche_override, **kwargs):
        raise RuntimeError("source video is gone")

    service.rerender_clip = broken_rerender
    await _run_qc(service)

    updates = service.clip_repo.qc_updates
    assert [status for status, _ in updates] == ["fail", "fail"]
    assert updates[-1][1]["retry"] == {"attempted": True, "succeeded": False}


@pytest.mark.asyncio
async def test_qc_crash_is_stored_as_error_and_never_raises(seam, media, monkeypatch):
    service = _service(_clip_dict(media["good"]))

    def exploding_qc(path, context):
        raise RuntimeError("ffprobe exploded")

    monkeypatch.setattr(task_service_module, "run_clip_qc", exploding_qc)
    await _run_qc(service)  # must not raise

    updates = service.clip_repo.qc_updates
    assert [status for status, _ in updates] == ["error"]
    assert service.clip_repo.clip["qc_status"] == "error"
