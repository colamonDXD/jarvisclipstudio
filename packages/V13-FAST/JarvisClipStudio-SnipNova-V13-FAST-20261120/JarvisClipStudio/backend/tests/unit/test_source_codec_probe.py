"""probe_video_codec: the per-task codec log line's data source.

The download format string is codec-agnostic, so the pipeline logs which
codec it actually landed (AV1/VP9/H.264) once per task. These tests pin
the probe's parsing and its failure-is-None contract: a probe failure
must never cost a task its run.
"""

import json
import subprocess
from pathlib import Path

from src.media import ffmpeg as ffmpeg_module


def _payload(streams):
    return json.dumps({"streams": streams})


def _install_probe(monkeypatch, payload, returncode=0):
    def fake_run(command, timeout=60):
        assert command[0] == "ffprobe"
        return subprocess.CompletedProcess(
            command, returncode, stdout=payload, stderr=""
        )

    monkeypatch.setattr(ffmpeg_module, "run_ffmpeg_command", fake_run)


def test_probe_reports_codec_and_dimensions(monkeypatch):
    _install_probe(
        monkeypatch,
        _payload([{"codec_name": "av1", "width": 1920, "height": 1080}]),
    )
    assert ffmpeg_module.probe_video_codec(Path("source.mkv")) == "av1 1920x1080"


def test_probe_reports_codec_without_dimensions(monkeypatch):
    _install_probe(monkeypatch, _payload([{"codec_name": "vp9"}]))
    assert ffmpeg_module.probe_video_codec(Path("source.mkv")) == "vp9"


def test_probe_returns_none_when_ffprobe_fails(monkeypatch):
    _install_probe(monkeypatch, "", returncode=1)
    assert ffmpeg_module.probe_video_codec(Path("source.mkv")) is None


def test_probe_returns_none_on_unparseable_output(monkeypatch):
    _install_probe(monkeypatch, "not json")
    assert ffmpeg_module.probe_video_codec(Path("source.mkv")) is None


def test_probe_returns_none_when_ffmpeg_missing(monkeypatch):
    def fake_run(command, timeout=60):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(ffmpeg_module, "run_ffmpeg_command", fake_run)
    assert ffmpeg_module.probe_video_codec(Path("source.mkv")) is None
