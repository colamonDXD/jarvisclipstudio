"""OOM guard: killed renders must be named in the log, and CPU-encoder
hosts must default to rendering one clip at a time.

Background: on a memory-tight machine, concurrent profiled renders peak
at several GB each and the OS kills the excess ones with SIGKILL. The
killed ffmpeg's stderr simply stops mid-stream, and the render runner
used to log only the command and that truncated stderr -- the
returncode, the only evidence of the kill, was recorded nowhere, so the
failure surfaced as an unexplained "ffmpeg reframe render failed".
"""

import logging
import os
import sys

import pytest

from src.media.ffmpeg import describe_abnormal_exit, run_ffmpeg_command
from src.services import task_service as task_service_module
from src.services.task_service import clip_render_concurrency

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="signal-death tests need a POSIX shell"
)


# ---------------------------------------------------------------------------
# The runner names a signal death
# ---------------------------------------------------------------------------


def test_runner_names_sigkill_death(caplog):
    with caplog.at_level(logging.ERROR):
        result = run_ffmpeg_command(
            ["sh", "-c", "echo frame=  10 >&2; kill -9 $$"]
        )
    assert result.returncode == -9
    assert "exit status -9" in caplog.text
    assert "SIGKILL" in caplog.text
    assert "killed by" in caplog.text
    assert "ran out of memory" in caplog.text


def test_runner_names_137_as_sigkill(caplog):
    # A shell wrapper between the runner and the process converts the
    # SIGKILL death into an ordinary exit with status 128 + 9 = 137.
    with caplog.at_level(logging.ERROR):
        result = run_ffmpeg_command(
            ["sh", "-c", "sh -c 'kill -9 $$'; exit $?"]
        )
    assert result.returncode == 137
    assert "exit status 137" in caplog.text
    assert "SIGKILL" in caplog.text
    assert "ran out of memory" in caplog.text


def test_runner_plain_failure_has_no_kill_language(caplog):
    with caplog.at_level(logging.ERROR):
        result = run_ffmpeg_command(["sh", "-c", "echo boom >&2; exit 1"])
    assert result.returncode == 1
    assert "exit status 1" in caplog.text
    assert "boom" in caplog.text
    assert "killed by" not in caplog.text


@pytest.mark.parametrize(
    "returncode,expected_fragments",
    [
        (-9, ["SIGKILL", "signal 9", "ran out of memory"]),
        (-15, ["SIGTERM", "signal 15"]),
        (137, ["SIGKILL", "137", "ran out of memory"]),
    ],
)
def test_describe_abnormal_exit_names_kills(returncode, expected_fragments):
    description = describe_abnormal_exit(returncode)
    assert description is not None
    for fragment in expected_fragments:
        assert fragment in description


@pytest.mark.parametrize("returncode", [0, 1, 69, 255])
def test_describe_abnormal_exit_ignores_ordinary_exits(returncode):
    assert describe_abnormal_exit(returncode) is None


def test_describe_abnormal_exit_sigterm_is_not_blamed_on_memory():
    description = describe_abnormal_exit(-15)
    assert description is not None
    assert "ran out of memory" not in description


# ---------------------------------------------------------------------------
# Concurrency: probe / force / env matrix
# ---------------------------------------------------------------------------


def _set_encoder(monkeypatch, probe: bool, forced: bool) -> None:
    monkeypatch.setattr(
        task_service_module, "check_nvenc_available", lambda: probe
    )
    monkeypatch.setattr(task_service_module, "force_cpu_encode", lambda: forced)


def _set_env(monkeypatch, value) -> None:
    if value is None:
        monkeypatch.delenv("CLIP_RENDER_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", value)


@pytest.mark.parametrize(
    "probe,forced,env,expected",
    [
        # GPU-effective: historical behaviour (default 3, env honoured).
        (True, False, None, 3),
        (True, False, "5", 5),
        (True, False, "0", 3),
        (True, False, "many", 3),
        # CPU-effective via probe: default 1, explicit env still wins.
        (False, False, None, 1),
        (False, False, "5", 5),
        (False, False, "0", 1),
        # CPU-effective via FORCE_CPU_ENCODE on a GPU host (the failing
        # machine's configuration): the old cap never engaged here.
        (True, True, None, 1),
        (True, True, "4", 4),
        (False, True, None, 1),
    ],
)
def test_clip_render_concurrency_matrix(monkeypatch, probe, forced, env, expected):
    _set_encoder(monkeypatch, probe, forced)
    _set_env(monkeypatch, env)
    assert clip_render_concurrency() == expected


def test_clip_render_concurrency_ignores_ambient_env(monkeypatch):
    # Guard against the suite's own environment leaking in: with the
    # encoder pinned to GPU-effective and no explicit setting, the
    # historical default is 3 regardless of os.environ leftovers.
    _set_encoder(monkeypatch, probe=True, forced=False)
    monkeypatch.delenv("CLIP_RENDER_CONCURRENCY", raising=False)
    assert os.environ.get("CLIP_RENDER_CONCURRENCY") is None
    assert clip_render_concurrency() == 3
