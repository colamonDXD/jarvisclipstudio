"""Voiceover (Kokoro): the opt-in gate, spec validation, cache and mixing.

The synthesis test is skipped unless the kokoro package AND the
downloaded model are both present in the test environment — the lane's
proof script exercises synthesis for real; here it must never make the
suite depend on a 315 MB download.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from src.media import edl_render, voiceover


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("VOICEOVER_ENABLED", "true")


# ------------------------------------------------------------- the gate


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VOICEOVER_ENABLED", raising=False)
    assert voiceover.voiceover_enabled() is False
    with pytest.raises(ValueError, match="disabled"):
        voiceover.normalize_voiceover_request(
            {"script": "Hello", "voice": "af_heart", "mode": "hook"}
        )
    # Clearing is always allowed — it can only remove a stored spec.
    assert voiceover.normalize_voiceover_request({"clear": True}) == {
        "clear": True
    }
    assert voiceover.normalize_voiceover_request(None) is None


def test_normalize_spec(enabled):
    assert voiceover.normalize_voiceover_request({"script": "  Hello world  "}) == {
        "script": "Hello world",
        "voice": "af_heart",
        "mode": "hook",
    }
    assert voiceover.normalize_voiceover_request(
        {"script": "Hi", "voice": "bf_emma", "mode": "replace"}
    ) == {"script": "Hi", "voice": "bf_emma", "mode": "replace"}


@pytest.mark.parametrize(
    "raw",
    [
        {"script": ""},
        {"script": "   "},
        {"script": "x" * (voiceover.MAX_SCRIPT_CHARS + 1)},
        {"script": "Hi", "voice": "xx_nobody"},
        {"script": "Hi", "mode": "overlay"},
        {"script": "Hi", "speed": 2},
        "narrate this",
    ],
)
def test_normalize_rejects_bad_specs(enabled, raw):
    with pytest.raises(ValueError):
        voiceover.normalize_voiceover_request(raw)


# ------------------------------------------------------------- cache + plan spec


def test_cache_filename_is_content_addressed():
    name = voiceover.cache_filename("Hello", "af_heart")
    assert name == voiceover.cache_filename("Hello", "af_heart")
    assert name != voiceover.cache_filename("Hello", "am_michael")
    assert name != voiceover.cache_filename("Hello!", "af_heart")
    assert name.startswith("vo_") and name.endswith(".wav")
    assert "/" not in name


def test_spec_from_plan_round_trip_and_guards():
    plan = {
        "voiceover": {
            "script": "Hi",
            "voice": "af_heart",
            "mode": "hook",
            "file": "vo_abc.wav",
        }
    }
    assert voiceover.voiceover_spec_from_plan(plan) == plan["voiceover"]
    assert voiceover.voiceover_spec_from_plan(None) is None
    assert voiceover.voiceover_spec_from_plan({}) is None
    # A stored spec whose file escapes the cache dir is not a spec.
    traversal = {
        "voiceover": {
            "script": "Hi",
            "voice": "af_heart",
            "mode": "hook",
            "file": "../elsewhere.wav",
        }
    }
    assert voiceover.voiceover_spec_from_plan(traversal) is None


# ------------------------------------------------------------- render graph


def _plain_edl():
    return {
        "duration": 3,
        "segments": [{"start": 0, "end": 3, "transition_in": "none", "transition_duration": 0}],
    }


def _make_wav(path: Path, frequency: int = 880, duration: float = 2.0) -> None:
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
            "-ac", "2", "-c:a", "pcm_s16le", str(path),
        ],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_hook_voiceover_graph_ducks_the_mix(tmp_path):
    vo = tmp_path / "vo.wav"
    _make_wav(vo)
    edl = _plain_edl()
    edl["voiceover"] = {"path": str(vo), "mode": "hook"}
    plan = edl_render.build_edl_audio_graph(edl, 1, tmp_path / "assets")
    assert plan is not None
    inputs, graph, label = plan
    assert str(vo) in inputs
    assert label == "[a_out]"
    assert "sidechaincompress" in graph and "ratio=12.00" in graph
    # The ducking key is silence-padded: sidechaincompress would
    # otherwise end the whole mix when the short narration ends.
    assert "apad,atrim=0:3.000[a_vo_key]" in graph
    assert "[a_mix_vo]" in graph


def test_replace_voiceover_graph_mutes_the_original_voice(tmp_path):
    vo = tmp_path / "vo.wav"
    _make_wav(vo)
    edl = _plain_edl()
    edl["voiceover"] = {"path": str(vo), "mode": "replace"}
    plan = edl_render.build_edl_audio_graph(edl, 1, tmp_path / "assets")
    assert plan is not None
    _inputs, graph, _label = plan
    assert ",volume=0" in graph
    assert "[a_mix_vo]" in graph


def test_missing_voiceover_file_is_ignored(tmp_path):
    edl = _plain_edl()
    edl["voiceover"] = {"path": str(tmp_path / "gone.wav"), "mode": "hook"}
    # No music/SFX/studio either: no plan at all, exactly like today.
    assert edl_render.build_edl_audio_graph(edl, 1, tmp_path / "assets") is None


# ------------------------------------------------------------- synthesis (opt-in)


def _kokoro_ready() -> bool:
    """True only when synthesis can run WITHOUT any download.

    The probe must look where production looks: voiceover_models_dir()
    ($VOICEOVER_MODELS_DIR, else <temp_dir>/models/huggingface) — that
    is the directory _pipeline points HF_HOME at before importing
    kokoro. Probing the ambient HF_HOME / ~/.cache instead both misses
    the model where the app actually caches it and can report "ready"
    for a stray HF cache the app will never use.
    """
    try:
        import kokoro  # noqa: F401
    except Exception:
        return False
    from src.media.voiceover import voiceover_models_dir

    return any(voiceover_models_dir().rglob("kokoro-v1_0.pth"))


@pytest.mark.requires_kokoro_model
@pytest.mark.skipif(
    not _kokoro_ready(),
    reason="kokoro package/model not present in the production model cache",
)
def test_synthesis_writes_a_cached_wav(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEOVER_DIR", str(tmp_path))
    path = voiceover.synthesize_voiceover("A short test line.", "af_heart")
    assert path.is_file() and path.stat().st_size > 1000
    again = voiceover.synthesize_voiceover("A short test line.", "af_heart")
    assert again == path


# ------------------------------------------------------------- real render

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")
def test_real_render_replace_mode_swaps_the_voice(tmp_path):
    """440 Hz 'speech' + 880 Hz narration: after a replace render the
    opening seconds carry the narration tone, not the original."""
    source = tmp_path / "source.mp4"
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
            "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(source),
        ],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    vo = tmp_path / "vo.wav"
    _make_wav(vo, frequency=880, duration=2.0)

    edl = {
        "duration": 4,
        "segments": [{"start": 0, "end": 4, "transition_in": "none", "transition_duration": 0}],
        "voiceover": {"path": str(vo), "mode": "replace"},
    }
    output = tmp_path / "out.mp4"
    ok = edl_render.render_edl_clip_ffmpeg(
        source, output, edl, assets_dir=tmp_path / "assets"
    )
    assert ok is True

    def band_mean_db(freq: int) -> float:
        result = subprocess.run(
            [
                "ffmpeg", "-t", "1.5", "-i", str(output),
                "-af", f"bandpass=f={freq}:width_type=h:width=120,volumedetect",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=300,
        )
        for line in result.stderr.splitlines():
            if "mean_volume:" in line:
                return float(line.rsplit("mean_volume:", 1)[1].strip().replace(" dB", ""))
        raise AssertionError("volumedetect output missing")

    # Narration band is clearly present; the original voice band is not.
    assert band_mean_db(880) > band_mean_db(440) + 10


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")
def test_real_render_hook_mode_keeps_the_full_clip_length(tmp_path):
    """Regression: sidechaincompress ends with its key input — an
    unpadded narration key truncated an 8 s clip to the 4.3 s
    narration. The hook render must keep the source's full duration."""
    source = tmp_path / "source.mp4"
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
            "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(source),
        ],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    vo = tmp_path / "vo.wav"
    _make_wav(vo, frequency=880, duration=2.0)

    edl = {
        "duration": 6,
        "segments": [{"start": 0, "end": 6, "transition_in": "none", "transition_duration": 0}],
        "voiceover": {"path": str(vo), "mode": "hook"},
    }
    output = tmp_path / "out.mp4"
    assert edl_render.render_edl_clip_ffmpeg(
        source, output, edl, assets_dir=tmp_path / "assets"
    ) is True
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(output),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert abs(float(probe.stdout.strip()) - 6.0) < 0.35
