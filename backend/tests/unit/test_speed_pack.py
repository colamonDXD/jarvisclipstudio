"""Speed Pack 1: bounded parallel clip rendering + NVENC final encodes."""

import asyncio
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.config import Config
from src.media import ffmpeg as ffmpeg_module
from src.services import task_service as task_service_module
from src.services.task_service import TaskService, clip_render_concurrency


def build_clip_result(index: int = 1) -> dict:
    return {
        "filename": f"clip-{index}.mp4",
        "path": f"/tmp/clip-{index}.mp4",
        "start_time": "00:00",
        "end_time": "00:10",
        "duration": 10.0,
        "text": "Hook text",
        "relevance_score": 0.95,
        "reasoning": "Strong hook",
    }


def build_task_service() -> TaskService:
    """Trimmed clone of test_task_service.build_task_service (no DB needed)."""
    config = Config()
    config.app_base_url = "http://localhost:3107"
    config.aws_region = "us-east-1"
    config.aws_access_key_id = "AKIATEST"
    config.aws_secret_access_key = "secret-test"
    config.ses_from_email = "SupoClip <noreply@example.com>"
    service = TaskService(db=AsyncMock(), config=config)
    service._load_performance_context = AsyncMock(return_value=None)
    service._processing_transaction = lambda _task_id: nullcontext()
    service._task_run_guard = lambda _task_id: nullcontext()
    service.task_repo.get_task_by_id = AsyncMock(return_value={"status": "queued"})
    service.cache_repo.get_cache = AsyncMock(return_value=None)
    service.cache_repo.upsert_cache = AsyncMock()
    service.checkpoint_repo.get = AsyncMock(return_value={})
    service.checkpoint_repo.save = AsyncMock()
    service.task_repo.update_task_runtime_metadata = AsyncMock()
    service.task_repo.update_task_status = AsyncMock()
    service.task_repo.update_task_clips = AsyncMock()
    service.task_repo.get_task_notification_context = AsyncMock(return_value=None)
    service.clip_repo.delete_clips_by_task = AsyncMock(return_value=0)
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=[])
    service.clip_repo.create_clip = AsyncMock(return_value="clip-1")
    service.clip_repo.get_clip_by_id = AsyncMock(return_value=None)
    service.video_service.create_single_clip = AsyncMock(
        return_value=build_clip_result()
    )
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "clips": [build_clip_result()],
            "segments_to_render": [{"start": 0, "end": 10}],
            "video_path": "/tmp/source.mp4",
            "segments": [],
            "summary": None,
            "key_topics": [],
            "transcript": "Transcript",
            "analysis_json": "{}",
        }
    )
    return service


def use_segments(service: TaskService, count: int) -> None:
    service.video_service.process_video_complete.return_value[
        "segments_to_render"
    ] = [{"start": 0, "end": 10} for _ in range(count)]


# ---------------------------------------------------------------------------
# Parallel rendering
# ---------------------------------------------------------------------------


async def test_process_task_renders_clips_concurrently_up_to_bound(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", "3")
    # SPEED-PACK-3 caps CPU-only hosts at 2; pin the GPU probe so this test
    # keeps exercising the env-configured bound itself.
    monkeypatch.setattr(task_service_module, "check_nvenc_available", lambda: True)
    service = build_task_service()
    service.config.temp_dir = str(tmp_path)
    use_segments(service, 6)

    state = {"active": 0, "peak": 0}

    async def fake_render(*args, **kwargs):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.05)
        state["active"] -= 1
        return build_clip_result(args[2] + 1 if len(args) > 2 else 1)

    service.video_service.create_single_clip = AsyncMock(side_effect=fake_render)

    result = await service.process_task(
        task_id="task-1", url="upload://source.mp4", source_type="video_url"
    )

    # The semaphore bound is reached (real parallelism) and never exceeded.
    assert state["peak"] == 3
    # Every clip is persisted, in plan order.
    assert result["clips_count"] == 6
    assert service.clip_repo.create_clip.await_count == 6
    assert service.checkpoint_repo.save.await_args.args[-1] == {
        "completed_positions": [1, 2, 3, 4, 5, 6]
    }


async def test_process_task_render_concurrency_one_is_sequential(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", "1")
    service = build_task_service()
    service.config.temp_dir = str(tmp_path)
    use_segments(service, 3)

    state = {"active": 0, "peak": 0}

    async def fake_render(*args, **kwargs):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return build_clip_result()

    service.video_service.create_single_clip = AsyncMock(side_effect=fake_render)

    result = await service.process_task(
        task_id="task-1", url="upload://source.mp4", source_type="video_url"
    )

    assert state["peak"] == 1
    assert result["clips_count"] == 3


async def test_process_task_one_crashing_render_does_not_fail_the_batch(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", "3")
    service = build_task_service()
    service.config.temp_dir = str(tmp_path)
    use_segments(service, 3)

    async def flaky_render(*args, **kwargs):
        if args[2] == 1:  # second clip crashes outright
            raise RuntimeError("ffmpeg exploded")
        return build_clip_result(args[2] + 1)

    service.video_service.create_single_clip = AsyncMock(side_effect=flaky_render)

    with pytest.raises(ValueError, match="Your completed clips are saved"):
        await service.process_task(
            task_id="task-1", url="upload://source.mp4", source_type="video_url"
        )

    # The other two clips were still rendered and persisted.
    assert service.clip_repo.create_clip.await_count == 2
    assert service.checkpoint_repo.save.await_args.args[-1] == {
        "completed_positions": [1, 3]
    }


def test_clip_render_concurrency_parsing(monkeypatch):
    # Parsing is exercised with NVENC available: on a CPU-only host the
    # value is additionally capped (see test_speed_pack_3.py).
    monkeypatch.setattr(task_service_module, "check_nvenc_available", lambda: True)
    monkeypatch.delenv("CLIP_RENDER_CONCURRENCY", raising=False)
    assert clip_render_concurrency() == 3
    monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", "5")
    assert clip_render_concurrency() == 5
    monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", "0")
    assert clip_render_concurrency() == 3
    monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", "many")
    assert clip_render_concurrency() == 3


# ---------------------------------------------------------------------------
# Encoder selection
# ---------------------------------------------------------------------------


def _arg_after(args, flag):
    return args[args.index(flag) + 1]


def test_final_encode_args_use_nvenc_when_available(monkeypatch):
    monkeypatch.setattr(ffmpeg_module, "check_nvenc_available", lambda: True)
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)

    args = ffmpeg_module.build_final_video_encode_args()

    assert args[:2] == ["-c:v", "h264_nvenc"]
    assert "libx264" not in args
    # CRF 19 maps to CQ 22 (FINAL_VIDEO_CRF + FINAL_VIDEO_NVENC_CQ_OFFSET).
    assert _arg_after(args, "-cq") == "22"
    assert _arg_after(args, "-preset") == "p4"
    assert _arg_after(args, "-r") == "30"
    assert _arg_after(args, "-level") == "4.1"


def test_final_encode_args_use_x264_when_nvenc_unavailable(monkeypatch):
    monkeypatch.setattr(ffmpeg_module, "check_nvenc_available", lambda: False)
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)

    args = ffmpeg_module.build_final_video_encode_args()

    assert args[:2] == ["-c:v", "libx264"]
    assert _arg_after(args, "-crf") == "19"
    assert _arg_after(args, "-preset") == "medium"


def test_final_encode_args_force_cpu_encode_overrides_nvenc(monkeypatch):
    monkeypatch.setattr(ffmpeg_module, "check_nvenc_available", lambda: True)
    monkeypatch.setenv("FORCE_CPU_ENCODE", "true")

    args = ffmpeg_module.build_final_video_encode_args()

    assert args[:2] == ["-c:v", "libx264"]
    assert "h264_nvenc" not in args


def test_nvenc_args_carry_caller_fps_and_level(monkeypatch):
    monkeypatch.setattr(ffmpeg_module, "check_nvenc_available", lambda: True)
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)

    args = ffmpeg_module.build_final_video_encode_args(fps=60, level="4.2")

    assert _arg_after(args, "-r") == "60"
    assert _arg_after(args, "-level") == "4.2"


def test_check_nvenc_available_is_false_without_gpu(monkeypatch):
    # The probe must reject builds that merely list the encoder: simulate an
    # encoder listing that mentions h264_nvenc but a failing probe encode.
    calls = []

    class Result:
        def __init__(self, returncode, stdout=""):
            self.returncode = returncode
            self.stdout = stdout

    def fake_run(command, timeout=900):
        calls.append(command)
        if "-encoders" in command:
            return Result(0, " V..... h264_nvenc  NVIDIA NVENC H.264 encoder")
        return Result(1)

    monkeypatch.setattr(ffmpeg_module, "run_ffmpeg_command", fake_run)
    ffmpeg_module.check_nvenc_available.cache_clear()
    try:
        assert ffmpeg_module.check_nvenc_available() is False
    finally:
        ffmpeg_module.check_nvenc_available.cache_clear()
    assert len(calls) == 2  # listing, then the one-frame probe encode
