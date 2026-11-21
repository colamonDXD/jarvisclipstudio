"""Every render path logs the encoder it actually used.

A morning of failed renders produced logs that could not say which
encoder had been running -- the EDL path received the encoder's name
from get_video_encoder() and threw it away. These tests pin the log
lines so the name is never discarded again.
"""

import logging
from pathlib import Path

from src.media import edl_render
from src.media import ffmpeg as ffmpeg_module


def _minimal_edl():
    return {
        "duration": 8,
        "segments": [
            {"start": 0, "end": 4, "transition_in": "none", "transition_duration": 0},
            {"start": 4, "end": 8, "transition_in": "cut", "transition_duration": 0},
        ],
        "overlays": [],
    }


def test_edl_graph_build_logs_the_encoder(monkeypatch, caplog):
    monkeypatch.setattr(
        edl_render,
        "get_video_encoder",
        lambda: ("h264_nvenc", ["-c:v", "h264_nvenc"]),
    )
    with caplog.at_level(logging.INFO, logger="src.media.common"):
        edl_render.build_edl_filter_graph(_minimal_edl(), Path("source.mp4"))
    assert "EDL encoder: h264_nvenc" in caplog.text


def test_plain_selector_logs_nvenc(monkeypatch, caplog):
    monkeypatch.setattr(ffmpeg_module, "check_nvenc_available", lambda: True)
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)
    with caplog.at_level(logging.INFO, logger="src.media.common"):
        ffmpeg_module.build_final_video_encode_args()
    assert "Final encode encoder: h264_nvenc" in caplog.text


def test_plain_selector_logs_libx264(monkeypatch, caplog):
    monkeypatch.setattr(ffmpeg_module, "check_nvenc_available", lambda: False)
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)
    with caplog.at_level(logging.INFO, logger="src.media.common"):
        ffmpeg_module.build_final_video_encode_args()
    assert "Final encode encoder: libx264" in caplog.text
