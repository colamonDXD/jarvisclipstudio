"""Niche override validation, resolution, and render-completion logging."""

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes import tasks
from src.auto_director import classify as classifier_module
from src.auto_director.classify import NICHE_VALUES as CLASSIFIER_NICHES
from src.auto_director.edl import load_profile
from src.auto_director.niche import (
    AUTO_NICHE_VALUES,
    MANUAL_ONLY_NICHES,
    NICHE_VALUES,
    resolve_effective_profile,
    validate_niche_override,
)
from src.services.video_service import VideoService


def test_niche_values_match_the_classifier_contract():
    assert NICHE_VALUES == CLASSIFIER_NICHES


def test_auto_and_manual_sets_match_the_classifier_contract():
    assert AUTO_NICHE_VALUES == classifier_module.AUTO_NICHE_VALUES
    assert MANUAL_ONLY_NICHES == classifier_module.MANUAL_ONLY_NICHES
    assert AUTO_NICHE_VALUES.isdisjoint(MANUAL_ONLY_NICHES)
    assert AUTO_NICHE_VALUES | MANUAL_ONLY_NICHES == NICHE_VALUES
    assert len(NICHE_VALUES) == 14


@pytest.mark.parametrize(
    "niche", ["football_edit", "japan_travel_cinematic", "anime_funk_edit"]
)
def test_manual_only_niches_are_valid_overrides(niche):
    """Visual/beat-driven niches are reachable by explicit selection."""
    assert validate_niche_override(niche) == niche
    effective = resolve_effective_profile(niche, None, "podcast_news")
    assert effective == niche
    assert load_profile(effective)["niche"] == niche


@pytest.mark.parametrize(
    "niche", ["football_edit", "japan_travel_cinematic", "anime_funk_edit"]
)
def test_manual_only_niches_are_never_classifier_results(niche):
    """A manual-only value arriving as the classifier result is ignored and
    resolution falls back, exactly as an unknown value would."""
    assert resolve_effective_profile(None, None, niche) == "podcast_news"


@pytest.mark.parametrize("niche", ["true_crime", "hindi_podcast", "music_artist"])
def test_new_auto_niches_resolve_and_load(niche):
    assert validate_niche_override(niche) == niche
    assert resolve_effective_profile(None, None, niche) == niche
    assert load_profile(niche)["niche"] == niche


def test_no_override_uses_the_classifier_profile():
    effective = resolve_effective_profile(None, None, "gaming")

    assert effective == "gaming"
    assert load_profile(effective)["niche"] == "gaming"


def test_travel_override_uses_the_travel_profile():
    effective = resolve_effective_profile("travel_vlog", None, "gaming")

    assert effective == "travel_vlog"
    assert load_profile(effective)["niche"] == "travel_vlog"


def test_precedence_clip_then_project_then_classifier():
    assert resolve_effective_profile("travel_vlog", "gaming", "sports") == "travel_vlog"
    assert resolve_effective_profile(None, "gaming", "sports") == "gaming"
    assert resolve_effective_profile(None, None, "sports") == "sports"


@pytest.mark.parametrize("value", ["cooking", "", "Travel_Vlog", 3, ["gaming"]])
def test_invalid_niche_override_raises_value_error(value):
    with pytest.raises(ValueError):
        validate_niche_override(value)


def _request(body):
    return SimpleNamespace(json=AsyncMock(return_value=body), headers={})


@pytest.mark.asyncio
async def test_create_task_rejects_invalid_niche_with_422(monkeypatch):
    monkeypatch.setattr(
        tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1")
    )
    monkeypatch.setattr(
        "src.source_submission.record_source_submission", AsyncMock()
    )

    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            _request(
                {
                    "source": {"url": "https://www.youtube.com/watch?v=abcdefghijk"},
                    "niche_override": "cooking",
                }
            ),
            db=None,
        )

    assert error.value.status_code == 422
    assert "niche_override" in error.value.detail


@pytest.mark.asyncio
async def test_render_completion_writes_edl_profile_and_image_sources(
    monkeypatch, tmp_path
):
    from src.services import video_service

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    edl = {
        "segments": [{"start": 0.0, "end": 10.0}],
        "overlays": [
            {
                "type": "reference_image",
                "source": "open_web",
                "url": "https://images.example/tokyo.jpg",
                "local_path": "",
            }
        ],
        "broll": [
            {
                "source": "pexels",
                "url": "https://video.example/city.mp4",
                "local_path": "",
                "query": "city street",
            }
        ],
    }
    segment = {
        "start_time": "00:00",
        "end_time": "00:10",
        "text": "hello",
        "edl": edl,
    }

    async def run_now(function, *args, **kwargs):
        if function is video_service.rendered_clip_duration:
            return 10.0
        result = function(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    def render(*args, **kwargs):
        Path(args[3]).write_bytes(b"rendered")
        return True

    monkeypatch.setattr(video_service, "run_in_thread", run_now)
    monkeypatch.setattr(video_service, "create_optimized_clip", render)
    monkeypatch.setattr(
        video_service, "build_clip_keep_ranges", lambda *args, **kwargs: [(0.0, 10.0)]
    )
    monkeypatch.setattr(
        video_service, "snap_clip_start_to_sentence", lambda *args, **kwargs: 0.0
    )
    monkeypatch.setattr(
        video_service,
        "extend_keep_ranges_to_sentence_boundary",
        lambda *args, **kwargs: args[1],
    )
    monkeypatch.setattr(
        video_service, "apply_segment_hook_teaser", lambda *args, **kwargs: args[1]
    )
    monkeypatch.setattr(
        video_service, "clamp_keep_ranges_to_max_duration", lambda *args, **kwargs: args[1]
    )
    monkeypatch.setattr(
        video_service, "keep_ranges_transcript_text", lambda *args, **kwargs: "hello"
    )

    from src import deliverable_bundle

    class Result:
        returncode = 0

    def fake_ffmpeg(command, timeout):
        Path(command[-1]).write_bytes(b"jpeg")
        return Result()

    monkeypatch.setattr(deliverable_bundle, "run_ffmpeg_command", fake_ffmpeg)

    clip = await VideoService.create_single_clip(
        source,
        segment,
        0,
        tmp_path,
        add_subtitles=False,
        edl=edl,
        niche_override="travel_vlog",
        project_niche_override="gaming",
        classifier_niche="sports",
    )

    assert clip is not None
    bundle = Path(clip["path"]).with_suffix("")
    assert json.loads((bundle / "edl.json").read_text()) == edl
    assert (bundle / "profile.txt").read_text() == "travel_vlog\n"
    sources = json.loads((bundle / "image_sources.json").read_text())
    assert [source["kind"] for source in sources] == ["reference_image", "broll"]
