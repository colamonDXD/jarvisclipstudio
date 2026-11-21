"""Discarded speaker analysis guard (integration phase 2b, Lane D §1e-2).

``build_clip_speaker_turns`` runs a low-res decode + face detection per
clip. On the direct-EDL path its result fed nothing: the layout plan is
skipped there and the EDL renderer takes no speaker input. The
computation is now guarded off for that path ONLY:

* direct EDL (segments, no plain delegation) -> turns NOT computed;
* plain path (no EDL) -> turns computed and passed to the renderer;
* EDL that delegates to the plain path (reaction clips) -> turns still
  computed, because the plain layout stack consumes them.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import src.video_utils as video_utils


@pytest.fixture
def harness(monkeypatch):
    calls = {"turns": 0, "render_kwargs": []}

    def spy_turns(path, ranges):
        calls["turns"] += 1
        return ([{"start": 0.0, "end": 1.0, "speaker": 0}], [{"word": "hi"}])

    def fake_render(source, output, *args, **kwargs):
        calls["render_kwargs"].append(kwargs)
        Path(output).write_bytes(b"fake mp4")
        return (True, 1080, 1920)

    monkeypatch.setattr(video_utils, "build_clip_speaker_turns", spy_turns)
    monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda path: True)
    monkeypatch.setattr(
        video_utils, "ffprobe_video_size", lambda path: (1920, 1080)
    )
    monkeypatch.setattr(
        video_utils, "render_source_ranges_ffmpeg", lambda *a, **k: True
    )
    monkeypatch.setattr(video_utils, "render_reframed_clip_ffmpeg", fake_render)
    monkeypatch.setattr(
        video_utils,
        "get_config",
        lambda: SimpleNamespace(speaker_framing=True),
    )
    return calls


def _run(tmp_path, edl):
    return video_utils.create_optimized_clip(
        tmp_path / "source.mp4",
        0.0,
        10.0,
        tmp_path / "out.mp4",
        add_subtitles=False,
        output_format="vertical",
        keep_ranges=[(0.0, 10.0)],
        extend_to_sentence=False,
        edl=edl,
    )


def test_direct_edl_skips_speaker_turns(tmp_path, harness):
    edl = {"clip_id": "clip-1", "segments": [{"index": 0}]}
    assert _run(tmp_path, edl) is True
    assert harness["turns"] == 0
    assert harness["render_kwargs"][0].get("edl") is not None


def test_plain_path_still_computes_speaker_turns(tmp_path, harness):
    assert _run(tmp_path, None) is True
    assert harness["turns"] == 1
    passed = harness["render_kwargs"][0].get("speaker_turns")
    assert passed and passed[0]["speaker"] == 0


def test_delegate_plain_edl_still_computes_speaker_turns(tmp_path, harness):
    edl = {
        "clip_id": "clip-2",
        "segments": [{"index": 0}],
        "reframe": {"delegate": "plain"},
    }
    assert _run(tmp_path, edl) is True
    assert harness["turns"] == 1
