"""Plain — just cuts: the per-task template mode (Luffy, 2026-11-10).

His ruling after the template covered his speaker with a stock
railway-bridge clip matched on the word "train" and framed him in a
blurred-pad strip: remove the auto template — "JUST NEED COMPLETE CLIP
WITH CUTS AND NO SILENCE NO PIC OR RANDOM SHIT I WILL EDIT MY OWN".

These tests pin the contract:
- the validator mirrors music_mode (absent is fine, garbage is not);
- plainify_edl strips every template-driven EDL element and keeps
  segments, captions and the hook title;
- the plainified EDL's audio graph is source-only (no plan at all);
- build_pipeline_edl with template_mode="plain" returns the stripped
  EDL even when a music pick and a Brand Kit are offered, and the Auto
  path (None / "auto") is byte-identical to before;
- the re-render builder keeps plain clips plain (no Auto bed returns).
"""

import copy
from pathlib import Path

import pytest

from src.auto_director.template_mode import (
    TEMPLATE_AUTO,
    TEMPLATE_PLAIN,
    is_plain,
    plainify_edl,
    validate_template_mode,
)
from src.media import edl_render, music_library
from src.media.music_library import apply_music_off
from src.services.clip_service import ClipEditingMixin
from src.services.video_service import VideoService


# --------------------------------------------------------------------------
# Validator
# --------------------------------------------------------------------------


def test_validate_template_mode_absent_is_none():
    assert validate_template_mode(None) is None


def test_validate_template_mode_accepts_the_two_values():
    assert validate_template_mode("auto") == TEMPLATE_AUTO
    assert validate_template_mode("plain") == TEMPLATE_PLAIN
    assert validate_template_mode(" Plain ") == TEMPLATE_PLAIN


def test_validate_template_mode_rejects_garbage():
    for bad in ("", "fancy", "off", 3, ["plain"]):
        with pytest.raises(ValueError):
            validate_template_mode(bad)


def test_is_plain():
    assert is_plain("plain")
    assert not is_plain("auto")
    assert not is_plain(None)


# --------------------------------------------------------------------------
# plainify_edl
# --------------------------------------------------------------------------


def _full_edl():
    """An EDL with every template channel populated."""
    return {
        "clip_id": "clip-x",
        "duration": 12.0,
        "segments": [
            {
                "index": 0, "start": 0.0, "end": 6.0,
                "transition_in": "fade", "transition_duration": 0.4,
            },
            {
                "index": 1, "start": 6.0, "end": 12.0,
                "transition_in": "zoom", "transition_duration": 0.3,
                "speed": 1.5, "speed_reason": "beat_drop", "speed_at": 8.0,
            },
        ],
        "zoom_keyframes": [{"time": 1.0, "scale": 1.2}],
        "captions": {"preset_id": "tiktok_animated", "words_per_line": 3,
                     "position": "bottom"},
        "overlays": [{"type": "info_card", "text": "700-year-old tree"}],
        "broll": [{"query": "railway bridge", "start": 2.0, "duration": 3.0}],
        "sfx": ["whoosh"],
        "sfx_events": [{"name": "whoosh", "at": 6.0}],
        "audio": {
            "music_track": "music/calm.wav", "music_mood": "calm",
            "music_volume": 0.2, "music_volume_0_to_1": 0.2,
            "ducking_db": -12.0, "music_source": "profile",
        },
        "color": {"lut": "punchy"},
        "hook": {"title_style": "statement", "title_seconds": 12.0,
                 "title_persistent": True},
        "beat_grid": {"bpm": 120.0},
        "scene_rules": [{"at": 3.0, "rule": "punch"}],
        "brand_template_override": {"template": "kit"},
        "brand_title": {"text": "KIT"},
        "brand_watermark": {"position": "tr"},
        "brand_bumpers": {"intro": "intro.mp4"},
    }


def test_plainify_strips_every_template_channel():
    edl = plainify_edl(_full_edl())
    assert edl["template"] == "plain"
    assert edl["broll"] == []
    assert edl["sfx"] == []
    assert edl["sfx_events"] == []
    assert edl["overlays"] == []
    assert edl["zoom_keyframes"] == []
    assert edl["color"] == {"lut": "none"}
    assert "beat_grid" not in edl
    assert "scene_rules" not in edl
    for key in ("brand_template_override", "brand_title",
                "brand_watermark", "brand_bumpers"):
        assert key not in edl
    for segment in edl["segments"]:
        assert segment["transition_in"] == "none"
        assert segment["transition_duration"] == 0
        assert "speed" not in segment
        assert "speed_reason" not in segment
        assert "speed_at" not in segment


def test_plainify_keeps_segments_captions_hook_and_timings():
    edl = plainify_edl(_full_edl())
    assert [(s["start"], s["end"]) for s in edl["segments"]] == [
        (0.0, 6.0), (6.0, 12.0),
    ]
    assert edl["captions"] == {
        "preset_id": "tiktok_animated", "words_per_line": 3,
        "position": "bottom",
    }
    assert edl["hook"]["title_seconds"] == 12.0
    assert edl["duration"] == 12.0


def test_plainify_audio_matches_the_music_off_shape():
    plain_audio = plainify_edl(_full_edl())["audio"]
    reference = _full_edl()
    apply_music_off(reference)
    for key in ("music_track", "music_mood", "music_volume",
                "music_volume_0_to_1", "music_source"):
        assert plain_audio[key] == reference["audio"][key]
    assert plain_audio["ducking_db"] == 0.0


def test_plainify_is_idempotent():
    once = plainify_edl(_full_edl())
    twice = plainify_edl(copy.deepcopy(once))
    assert twice == once


def test_plainified_edl_audio_graph_is_source_only(tmp_path):
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "music" / "calm.wav").touch()
    edl = _full_edl()
    # The un-stripped EDL resolves its bed into an audio graph...
    assert edl_render.build_edl_audio_graph(edl, 1, assets_dir=assets) is not None
    # ...the plainified one plans no graph at all: legacy source audio.
    plainify_edl(edl)
    assert edl_render.build_edl_audio_graph(edl, 1, assets_dir=assets) is None


# --------------------------------------------------------------------------
# Pipeline wiring: build_pipeline_edl
# --------------------------------------------------------------------------


def _library(tmp_path: Path, monkeypatch) -> Path:
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "sfx").mkdir(parents=True)
    (assets / "music" / "calm.wav").touch()
    user_dir = tmp_path / "user_music"
    user_dir.mkdir()
    monkeypatch.setattr(edl_render, "ASSETS_DIR", assets)
    monkeypatch.setattr(music_library, "user_music_dir", lambda: user_dir)
    return assets


def _segment():
    return {"start_time": "00:00", "end_time": "00:12",
            "text": "a podcast segment"}


def _words():
    return [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "there", "start": 0.5, "end": 0.9},
    ]


def test_pipeline_auto_and_legacy_none_are_identical(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    legacy = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0,
    )
    explicit_auto = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0,
        template_mode="auto",
    )
    assert legacy is not None
    assert explicit_auto == legacy
    # The fixture really exercises the music chain: Auto stamps its bed.
    assert legacy["audio"]["music_source"] == "auto_bed"
    assert "template" not in legacy


def test_pipeline_plain_strips_despite_music_pick_and_brand_kit(
    tmp_path, monkeypatch
):
    assets = _library(tmp_path, monkeypatch)
    (assets / "music" / "chosen.wav").touch()
    brand = {
        "caption_template": "kit_template",
        "title_style": "kit_style",
        "font_color": "#FF0000",
        "watermark": {"enabled": True},
        "music_override": None,
        "music_mode": None,
    }
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0,
        music_override="chosen.wav",
        template_mode="plain",
        brand_application=brand,
    )
    assert edl is not None
    assert edl["template"] == "plain"
    assert edl["audio"]["music_track"] == ""
    assert edl["audio"]["music_mood"] == "none"
    assert edl["audio"]["music_volume"] == 0.0
    assert edl["audio"]["music_source"] == "user_none"
    assert edl["broll"] == []
    assert edl["sfx_events"] == []
    assert edl["overlays"] == []
    assert edl["zoom_keyframes"] == []
    assert edl["color"] == {"lut": "none"}
    for key in ("brand_template_override", "brand_title",
                "brand_watermark", "brand_bumpers"):
        assert key not in edl
    for segment in edl["segments"]:
        assert segment["transition_in"] == "none"
        assert segment["transition_duration"] == 0
    # What Plain keeps: captions and the hook title.
    assert edl["captions"]
    assert edl["hook"]


# --------------------------------------------------------------------------
# Re-render builder: plain clips re-render plain
# --------------------------------------------------------------------------


def _clip():
    return {"id": "clip-1", "text": "hello there podcast words"}


def test_rerender_edl_plain_has_no_auto_bed(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    plain = ClipEditingMixin._build_rerender_edl(
        _clip(), 0.0, 12.0, "podcast_news", None, template_mode="plain",
    )
    assert plain["template"] == "plain"
    assert plain["audio"]["music_source"] == "user_none"
    assert plain["audio"]["music_mood"] == "none"
    assert plain["broll"] == []


def test_rerender_edl_auto_keeps_the_auto_bed(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    auto = ClipEditingMixin._build_rerender_edl(
        _clip(), 0.0, 12.0, "podcast_news", None,
    )
    assert auto["audio"]["music_source"] == "auto_bed"
    assert "template" not in auto


def test_rerender_edl_plain_restores_an_explicit_transition():
    edl = ClipEditingMixin._build_rerender_edl(
        _clip(), 0.0, 12.0, "podcast_news", "fade", template_mode="plain",
    )
    joins = [s for s in edl["segments"] if s.get("index", 0) > 0]
    assert joins, "fixture should produce a multi-segment EDL"
    assert all(s["transition_in"] == "fade" for s in joins)
    assert edl["audio"]["music_source"] == "user_none"
