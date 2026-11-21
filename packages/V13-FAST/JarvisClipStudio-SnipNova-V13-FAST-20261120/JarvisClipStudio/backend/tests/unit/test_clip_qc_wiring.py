"""Phase 2 QC wiring: loudness target and boundary words reach ClipQcContext.

The checks themselves are covered in test_clip_qc_phase2.py. Here: the env
switches, the boundary-word extraction the render stores in its facts
sidecar, and TaskService._attach_render_facts filling the context from them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.media import clip_qc
from src.media.clip_qc import (
    ClipQcContext,
    qc_loudness_target,
    qc_thought_boundaries_enabled,
    transcript_boundary_words,
)
from src.services import task_service as task_service_module
from src.services.task_service import TaskService


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CLIP_QC_LOUDNESS_LUFS", raising=False)
    monkeypatch.delenv("CLIP_QC_THOUGHT_BOUNDARIES", raising=False)


def test_loudness_target_defaults_to_minus_14(monkeypatch):
    assert qc_loudness_target() == -14.0
    monkeypatch.setenv("CLIP_QC_LOUDNESS_LUFS", "-16")
    assert qc_loudness_target() == -16.0


@pytest.mark.parametrize("value", ["off", "0", "false", "none", "", "not-a-number"])
def test_loudness_target_off_values(monkeypatch, value):
    monkeypatch.setenv("CLIP_QC_LOUDNESS_LUFS", value)
    assert qc_loudness_target() is None


def test_thought_boundaries_switch(monkeypatch):
    assert qc_thought_boundaries_enabled() is True
    monkeypatch.setenv("CLIP_QC_THOUGHT_BOUNDARIES", "off")
    assert qc_thought_boundaries_enabled() is False


def _write_cache(tmp_path: Path, words) -> Path:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"")
    video.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"version": 2, "words": words}), encoding="utf-8"
    )
    return video


def test_boundary_words_keep_only_words_near_either_edge(tmp_path):
    words = [
        {"text": " Hello", "start": 1000, "end": 1400},     # near start (10 s)? no: 1 s
        {"text": " world.", "start": 9000, "end": 9500},    # near start
        {"text": " Today", "start": 10200, "end": 10600},   # near start
        {"text": " middle", "start": 25000, "end": 25400},  # far from both
        {"text": " ends", "start": 39000, "end": 39600},    # near end (40 s)
        {"text": " here.", "start": 39700, "end": 40100},   # near end
        {"text": " After", "start": 41000, "end": 41300},   # near end (after it)
        {"text": " later", "start": 60000, "end": 60400},   # far
    ]
    video = _write_cache(tmp_path, words)
    got = transcript_boundary_words(video, 10.0, 40.0, pad=4.0)
    assert [w["text"] for w in got] == ["world.", "Today", "ends", "here.", "After"]
    assert got[0] == {"text": "world.", "start": 9.0, "end": 9.5}


def test_boundary_words_empty_without_a_transcript(tmp_path):
    assert transcript_boundary_words(tmp_path / "none.mp4", 0.0, 10.0) == []


def test_stored_boundary_words_drive_the_real_check(tmp_path):
    words = [
        {"text": " sentence.", "start": 9000, "end": 9500},
        {"text": " Start", "start": 10100, "end": 10500},
        {"text": " finish.", "start": 39500, "end": 39900},
        {"text": " Next", "start": 40600, "end": 41000},
    ]
    stored = transcript_boundary_words(_write_cache(tmp_path, words), 10.0, 40.0)
    result = clip_qc.check_thought_boundaries(stored, 10.0, 40.0)
    assert result["status"] == "pass"


@pytest.mark.asyncio
async def test_attach_render_facts_sets_target_and_words(monkeypatch, tmp_path):
    stored = [{"text": "Start", "start": 10.1, "end": 10.5}]
    monkeypatch.setattr(
        task_service_module, "load_clip_render_facts",
        lambda _path: {"segment_count": 3, "boundary_words": stored},
    )
    context = ClipQcContext()
    await TaskService._attach_render_facts(context, tmp_path / "clip.mp4")
    assert context.loudness_target_lufs == -14.0
    assert context.transcript_words == stored
    assert context.rendered_segment_count == 3


@pytest.mark.asyncio
async def test_attach_render_facts_respects_both_off_switches(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIP_QC_LOUDNESS_LUFS", "off")
    monkeypatch.setenv("CLIP_QC_THOUGHT_BOUNDARIES", "0")
    monkeypatch.setattr(
        task_service_module, "load_clip_render_facts",
        lambda _path: {"boundary_words": [{"text": "x", "start": 1.0, "end": 1.2}]},
    )
    context = ClipQcContext()
    await TaskService._attach_render_facts(context, tmp_path / "clip.mp4")
    assert context.loudness_target_lufs is None
    assert context.transcript_words is None


@pytest.mark.asyncio
async def test_loudness_target_is_set_even_without_render_facts(monkeypatch, tmp_path):
    monkeypatch.setattr(task_service_module, "load_clip_render_facts", lambda _path: {})
    context = ClipQcContext()
    await TaskService._attach_render_facts(context, tmp_path / "clip.mp4")
    assert context.loudness_target_lufs == -14.0
    assert context.transcript_words is None
