"""Wave 4 separation, Stage 2: the render-side music strip.

Stage 1 fed the separated vocals stem to transcription. Stage 2 feeds
the same stem to the clip RENDER: for a task that opted in (and with
SEPARATION_ENABLED on), the export's voice chain is cut from the stem
instead of the raw source mix, so the source's own background music is
removed and the app's bed/ducking works over clean speech.

The contract pinned here:

- the stem reaches the render only as an EDL stamp (``vocals_stem``)
  resolved once per task in ``process_task`` — never for a task that
  did not opt in, never while the deployment flag is off, and a failed
  separation leaves the render on the original audio;
- in direct-from-source renders the stem rides as an extra input
  windowed with the same seek, and the ONLY difference from the
  historical command is that swap (proven token-for-token);
- the range stitch (materialised intermediates + the non-direct
  pre-stage) bakes the stem's audio under the source's picture, and a
  missing stem file is ignored rather than failing the stitch;
- a real-ffmpeg render of a synthetic speech+music source measures the
  strip: the music-only window drops by >= 15 dB with the stem while
  the speech window stays within 3 dB of the unstripped export. The
  "stem" in that test is the ground-truth voice track — the separator's
  own output quality is Stage 1's proven leg; what is measured here is
  the render seam.
"""

import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from fake_ffmpeg_shim import ffmpeg_calls, install_fake_ffmpeg
from src.media import edl_render
from src.media import ffmpeg as ffmpeg_module
from src.media.ffmpeg import render_source_ranges_ffmpeg


@pytest.fixture(autouse=True)
def _clean_probe_state():
    ffmpeg_module._reset_nvenc_probe_state()
    yield
    ffmpeg_module._reset_nvenc_probe_state()


# --------------------------------------------------------------------------
# Audio pre-stage fragments: which input the ranges are cut from
# --------------------------------------------------------------------------


def test_audio_prestage_default_reads_the_source_stream():
    fragments, label = edl_render._source_audio_prestage_fragments(
        [(0.0, 5.0), (5.0, 10.0)]
    )
    assert label == "[srca]"
    trims = [f for f in fragments if "atrim" in f]
    assert trims and all(f.startswith("[0:a]atrim") for f in trims)


def test_audio_prestage_can_read_the_stem_input():
    fragments, label = edl_render._source_audio_prestage_fragments(
        [(0.0, 5.0), (5.0, 10.0)], "[3:a]"
    )
    assert label == "[srca]"
    trims = [f for f in fragments if "atrim" in f]
    assert trims and all(f.startswith("[3:a]atrim") for f in trims)
    assert not any(f.startswith("[0:a]atrim") for f in fragments)


# --------------------------------------------------------------------------
# The EDL stamp: honoured only while the file exists
# --------------------------------------------------------------------------


def test_edl_vocals_stem_resolution(tmp_path):
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    assert edl_render._edl_vocals_stem({"vocals_stem": str(stem)}) == stem
    assert edl_render._edl_vocals_stem({}) is None
    assert edl_render._edl_vocals_stem({"vocals_stem": ""}) is None
    assert edl_render._edl_vocals_stem({"vocals_stem": None}) is None
    assert edl_render._edl_vocals_stem({"vocals_stem": 42}) is None
    # A stamp whose file was cleaned up renders the historical way.
    assert edl_render._edl_vocals_stem(
        {"vocals_stem": str(tmp_path / "gone.flac")}
    ) is None


# --------------------------------------------------------------------------
# _render_edl_with_encoder: the stem input swap, and parity without it
# --------------------------------------------------------------------------


def _capture_render(monkeypatch):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return True, ""

    monkeypatch.setattr(edl_render, "_run_render_command", fake_run)
    return commands


def _direct_edl():
    return {
        "duration": 10,
        "segments": [
            {"start": 0, "end": 10, "transition_in": "none", "transition_duration": 0}
        ],
        "source_ranges": [[10.0, 20.0]],
    }


def _render_once(tmp_path, edl, assets_dir=None):
    return edl_render._render_edl_with_encoder(
        tmp_path / "source.mp4",
        tmp_path / "out.mp4",
        edl,
        [],
        "[0:v]null[vout]",
        "[vout]",
        "libx264",
        ["-c:v", "libx264"],
        assets_dir,
    )


def _graph_of(command):
    return command[command.index("-filter_complex") + 1]


def test_render_with_stem_cuts_voice_from_the_stem_input(tmp_path, monkeypatch):
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    edl = _direct_edl()
    edl["vocals_stem"] = str(stem)
    commands = _capture_render(monkeypatch)

    ok, _ = _render_once(tmp_path, edl)

    assert ok
    assert len(commands) == 1
    command = commands[0]
    # The stem is input 1, windowed with the same seek as the source
    # (origin 10 -> -ss 10.000 -t 11.000), and the audio pre-stage cuts
    # the shifted range from it.
    stem_at = command.index(str(stem))
    assert command[stem_at - 1] == "-i"
    assert command[stem_at - 5 : stem_at - 1] == ["-ss", "10.000", "-t", "11.000"]
    assert "[1:a]atrim=start=0.000:end=10.000" in _graph_of(command)


def test_render_without_stem_is_token_identical_after_unswapping(tmp_path, monkeypatch):
    """Parity: removing the swap from a stem render reproduces the
    historical command exactly — the swap is the ONLY delta."""
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    commands = _capture_render(monkeypatch)

    plain_edl = _direct_edl()
    ok, _ = _render_once(tmp_path, plain_edl)
    assert ok
    historical = commands[-1]
    assert "[0:a]atrim=start=0.000:end=10.000" in _graph_of(historical)
    assert str(stem) not in historical

    stem_edl = _direct_edl()
    stem_edl["vocals_stem"] = str(stem)
    ok, _ = _render_once(tmp_path, stem_edl)
    assert ok
    swapped = list(commands[-1])
    stem_at = swapped.index(str(stem))
    del swapped[stem_at - 5 : stem_at + 1]  # -ss X -t Y -i <stem>
    graph_at = swapped.index("-filter_complex") + 1
    swapped[graph_at] = swapped[graph_at].replace("[1:a]", "[0:a]")
    assert swapped == historical


def test_render_with_stem_shifts_music_input_index(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "music" / "bed.wav").write_bytes(b"bed")
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    commands = _capture_render(monkeypatch)

    def music_edl(with_stem):
        edl = _direct_edl()
        edl["audio"] = {
            "music_track": "bed",
            "music_volume_0_to_1": 0.2,
            "ducking_db": 8,
        }
        if with_stem:
            edl["vocals_stem"] = str(stem)
        return edl

    ok, _ = _render_once(tmp_path, music_edl(False), assets_dir=assets)
    assert ok
    assert "[1:a]atrim=0:10.000" in _graph_of(commands[-1])

    ok, _ = _render_once(tmp_path, music_edl(True), assets_dir=assets)
    assert ok
    graph = _graph_of(commands[-1])
    # The bed moved from input 1 to input 2 (the stem took index 1) and
    # the ducking chain still keys on the joined voice label.
    assert "[2:a]atrim=0:10.000" in graph
    assert "sidechaincompress" in graph
    assert "[1:a]atrim=start=0.000:end=10.000" in graph


def test_render_plain_template_shape_with_stem_stays_plain(tmp_path, monkeypatch):
    """A plainified EDL (Music Off shape, no audio plan) with a stem
    renders through the plain path — voice from the stem, nothing added."""
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    edl = _direct_edl()
    edl["template"] = "plain"
    edl["audio"] = {"music_track": "none", "music_volume_0_to_1": 0.0, "ducking_db": 0}
    edl["vocals_stem"] = str(stem)
    commands = _capture_render(monkeypatch)

    ok, _ = _render_once(tmp_path, edl)

    assert ok
    assert len(commands) == 1
    assert "[1:a]atrim=start=0.000:end=10.000" in _graph_of(commands[0])
    out_at = commands[0].index("[a_out]")
    assert commands[0][out_at - 1] == "-map"


# --------------------------------------------------------------------------
# Range materialisation: stem stitch, and the no-stem retry
# --------------------------------------------------------------------------


def test_materialise_retries_without_stem_when_stem_stitch_fails(
    tmp_path, monkeypatch
):
    calls = []

    def fake_stitch(input_path, ranges, output_path, audio_path=None):
        calls.append(audio_path)
        return audio_path is None

    monkeypatch.setattr(
        edl_render, "render_source_ranges_ffmpeg", fake_stitch
    )
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")

    result = edl_render._materialise_range_prestage(
        tmp_path / "source.mp4",
        tmp_path / "clip.mp4",
        [(0.0, 5.0), (6.0, 11.0)],
        audio_path=stem,
    )

    assert result is not None and result.is_file()
    assert calls == [stem, None]
    result.unlink(missing_ok=True)


def test_materialise_without_stem_keeps_single_attempt(tmp_path, monkeypatch):
    calls = []

    def fake_stitch(input_path, ranges, output_path, audio_path=None):
        calls.append(audio_path)
        return False

    monkeypatch.setattr(
        edl_render, "render_source_ranges_ffmpeg", fake_stitch
    )

    result = edl_render._materialise_range_prestage(
        tmp_path / "source.mp4", tmp_path / "clip.mp4", [(0.0, 5.0), (6.0, 11.0)]
    )

    assert result is None
    assert calls == [None]


# --------------------------------------------------------------------------
# render_source_ranges_ffmpeg command shapes (fake-ffmpeg PATH shim)
# --------------------------------------------------------------------------


def _calls_for_output(state, output: Path):
    # ffmpeg_calls, not render_calls: the single-range shape is
    # stream-mapped and carries no -filter_complex for render_calls
    # to key on. The output path is the call's last token either way.
    return [c for c in ffmpeg_calls(state) if c[-1] == str(output)]


def test_stitch_single_range_maps_stem_audio(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    output = tmp_path / "out.mp4"

    assert render_source_ranges_ffmpeg(
        Path("source.mp4"), [(2.0, 7.0)], output, audio_path=stem
    )

    (call,) = _calls_for_output(state, output)
    stem_at = call.index(str(stem))
    assert call[stem_at - 3 : stem_at] == ["-ss", "2.000", "-i"]
    maps = [call[i + 1] for i, tok in enumerate(call) if tok == "-map"]
    assert maps == ["0:v:0", "1:a:0"]


def test_stitch_single_range_without_stem_is_historical(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)
    output = tmp_path / "out.mp4"

    assert render_source_ranges_ffmpeg(Path("source.mp4"), [(2.0, 7.0)], output)

    (call,) = _calls_for_output(state, output)
    maps = [call[i + 1] for i, tok in enumerate(call) if tok == "-map"]
    assert maps == ["0:v:0", "0:a:0"]


def test_stitch_crossfade_reads_stem_audio(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    output = tmp_path / "out.mp4"

    assert render_source_ranges_ffmpeg(
        Path("source.mp4"), [(0.0, 5.0), (6.0, 11.0)], output, audio_path=stem
    )

    (call,) = _calls_for_output(state, output)
    graph = call[call.index("-filter_complex") + 1]
    assert "[1:a]atrim" in graph
    assert "[0:a:0]atrim" not in graph
    assert str(stem) in call


def test_stitch_concat_reads_stem_audio(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    output = tmp_path / "out.mp4"
    # Nine ranges disable the crossfade decision -> hard-concat shape.
    ranges = [(float(i * 2), float(i * 2) + 1.0) for i in range(9)]

    assert render_source_ranges_ffmpeg(
        Path("source.mp4"), ranges, output, audio_path=stem
    )

    (call,) = _calls_for_output(state, output)
    graph = call[call.index("-filter_complex") + 1]
    assert "[1:a]atrim" in graph
    assert "[0:a:0]atrim" not in graph


def test_stitch_missing_stem_file_falls_back_to_source_audio(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)
    output = tmp_path / "out.mp4"

    assert render_source_ranges_ffmpeg(
        Path("source.mp4"),
        [(2.0, 7.0)],
        output,
        audio_path=tmp_path / "cleaned-up.flac",
    )

    (call,) = _calls_for_output(state, output)
    maps = [call[i + 1] for i, tok in enumerate(call) if tok == "-map"]
    assert maps == ["0:v:0", "0:a:0"]


# --------------------------------------------------------------------------
# create_single_clip: the stamp lands on the EDL and the kwarg goes down
# --------------------------------------------------------------------------


def _fake_optimized_clip(record):
    def fake(*args, **kwargs):
        record.append((args, kwargs))
        return True

    return fake


@pytest.mark.asyncio
async def test_create_single_clip_stamps_and_forwards_the_stem(
    tmp_path, monkeypatch
):
    from src.services import video_service as video_service_module

    record = []
    monkeypatch.setattr(
        video_service_module,
        "create_optimized_clip",
        _fake_optimized_clip(record),
    )
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    (tmp_path / "clips").mkdir(exist_ok=True)
    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    edl = {
        "duration": 10,
        "segments": [
            {"start": 0, "end": 10, "transition_in": "none", "transition_duration": 0}
        ],
        "reframe": {},
    }
    segment = {
        "start_time": "00:00",
        "end_time": "00:10",
        "keep_ranges": [[0.0, 10.0]],
        "text": "hello world",
    }

    result = await video_service_module.VideoService.create_single_clip(
        source,
        segment,
        0,
        tmp_path / "clips",
        edl=edl,
        vocals_stem=stem,
    )

    assert result is not None
    assert edl["vocals_stem"] == str(stem)
    (_args, kwargs), = record
    assert kwargs["vocals_stem"] == stem


@pytest.mark.asyncio
async def test_create_single_clip_without_stem_keeps_historical_call(
    tmp_path, monkeypatch
):
    from src.services import video_service as video_service_module

    record = []
    monkeypatch.setattr(
        video_service_module,
        "create_optimized_clip",
        _fake_optimized_clip(record),
    )
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    (tmp_path / "clips").mkdir(exist_ok=True)
    edl = {
        "duration": 10,
        "segments": [
            {"start": 0, "end": 10, "transition_in": "none", "transition_duration": 0}
        ],
        "reframe": {},
    }
    segment = {
        "start_time": "00:00",
        "end_time": "00:10",
        "keep_ranges": [[0.0, 10.0]],
        "text": "hello world",
    }

    result = await video_service_module.VideoService.create_single_clip(
        source, segment, 0, tmp_path / "clips", edl=edl
    )

    assert result is not None
    assert "vocals_stem" not in edl
    (_args, kwargs), = record
    assert "vocals_stem" not in kwargs


# --------------------------------------------------------------------------
# process_task wiring: resolve once, pass only when a stem exists
# --------------------------------------------------------------------------


def _opted_in_service(task_row):
    from test_task_service import build_task_service

    service = build_task_service()
    service.task_repo.get_task_by_id = AsyncMock(return_value=task_row)
    service.task_repo.get_task_notification_context = AsyncMock(
        return_value={
            "notify_on_completion": False,
            "completion_notification_sent_at": None,
            "source_title": "Demo video",
            "user_email": "user@example.com",
            "user_name": "Demo User",
            "user_first_name": "Demo",
        }
    )
    return service


@pytest.mark.asyncio
async def test_process_task_passes_resolved_stem_to_renders(
    tmp_path, monkeypatch
):
    from src.media import separation as separation_module

    stem = tmp_path / "source.vocals.flac"
    stem.write_bytes(b"flac")
    seen_sources = []

    def fake_separate(source_path):
        seen_sources.append(source_path)
        return stem

    monkeypatch.setattr(separation_module, "separate_vocals", fake_separate)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    service = _opted_in_service({"status": "queued", "separation_opt_in": True})

    result = await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    assert result["clips_count"] == 1
    assert seen_sources == [Path("/tmp/source.mp4")]
    assert (
        service.video_service.create_single_clip.await_args.kwargs["vocals_stem"]
        == stem
    )


@pytest.mark.asyncio
async def test_process_task_without_opt_in_never_separates(monkeypatch):
    from src.media import separation as separation_module

    def forbidden_separate(_source_path):  # pragma: no cover - guard
        raise AssertionError("separator must not run for a non-opted task")

    monkeypatch.setattr(separation_module, "separate_vocals", forbidden_separate)
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    service = _opted_in_service({"status": "queued"})

    result = await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    assert result["clips_count"] == 1
    assert (
        "vocals_stem"
        not in service.video_service.create_single_clip.await_args.kwargs
    )


@pytest.mark.asyncio
async def test_process_task_opt_in_separation_failure_renders_original(
    monkeypatch,
):
    """Failure drill: the separator returns nothing -> renders proceed
    with the original audio (no kwarg) and the task completes."""
    from src.media import separation as separation_module

    monkeypatch.setattr(
        separation_module, "separate_vocals", lambda _path: None
    )
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    service = _opted_in_service({"status": "queued", "separation_opt_in": True})

    result = await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    assert result["clips_count"] == 1
    assert (
        "vocals_stem"
        not in service.video_service.create_single_clip.await_args.kwargs
    )


@pytest.mark.asyncio
async def test_process_task_opt_in_flag_off_never_separates(monkeypatch):
    from src.media import separation as separation_module

    def forbidden_separate(_source_path):  # pragma: no cover - guard
        raise AssertionError("separator must not run while the flag is off")

    monkeypatch.setattr(separation_module, "separate_vocals", forbidden_separate)
    monkeypatch.delenv("SEPARATION_ENABLED", raising=False)
    service = _opted_in_service({"status": "queued", "separation_opt_in": True})

    result = await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )

    assert result["clips_count"] == 1
    assert (
        "vocals_stem"
        not in service.video_service.create_single_clip.await_args.kwargs
    )


# --------------------------------------------------------------------------
# Real-ffmpeg render proof (synthetic ground truth)
# --------------------------------------------------------------------------


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    return result


def _mean_volume_db(path: Path, start: float, duration: float) -> float:
    result = subprocess.run(
        [
            "ffmpeg", "-ss", str(start), "-t", str(duration), "-i", str(path),
            "-vn", "-af", "volumedetect", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    match = re.search(r"mean_volume:\s*(-?[\d.]+) dB", result.stderr)
    assert match, result.stderr[-1000:]
    return float(match.group(1))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_render_strip_measures_on_synthetic_source(tmp_path):
    """16 s source: voice bursts at 2-6 s and 12-15 s over a continuous
    chord bed. The stem file is the ground-truth voice track. Both the
    direct in-graph path (single range) and the materialised path
    (crossfaded ranges) must show >= 15 dB less music-window energy
    with the stem, with speech within 3 dB of the unstripped export."""
    import numpy as np

    sample_rate = 44100
    seconds = 16
    t = np.arange(sample_rate * seconds) / sample_rate
    voice = np.zeros_like(t)
    for start, end in ((2.0, 6.0), (12.0, 15.0)):
        window = (t >= start) & (t < end)
        voice[window] = 0.6 * np.sin(2 * np.pi * 880 * t[window])
    music = 0.45 * np.sin(2 * np.pi * 220 * t) + 0.45 * np.sin(
        2 * np.pi * 294 * t
    )
    mix = np.clip(voice + music, -1.0, 1.0)

    def write_wav(path, samples):
        pcm = (samples * 32767).astype("<i2")
        stereo = np.repeat(pcm[:, None], 2, axis=1)
        # Write through ffmpeg's raw pipe so no extra deps are needed.
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "s16le", "-ar", str(sample_rate),
                "-ac", "2", "-i", "-",
                "-c:a", "pcm_s16le", str(path),
            ],
            input=stereo.tobytes(),
            capture_output=True,
        )
        assert proc.returncode == 0, proc.stderr.decode()[-1000:]

    voice_wav = tmp_path / "voice.wav"
    mix_wav = tmp_path / "mix.wav"
    write_wav(voice_wav, voice)
    write_wav(mix_wav, mix)

    source = tmp_path / "source.mp4"
    _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=duration=16:size=540x960:rate=30",
            "-i", str(mix_wav),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-shortest", str(source),
        ]
    )

    def render(name, ranges, with_stem):
        edl = {
            "duration": float(sum(e - s for s, e in ranges)),
            "segments": [
                {
                    "start": 0,
                    "end": float(sum(e - s for s, e in ranges)),
                    "transition_in": "none",
                    "transition_duration": 0,
                }
            ],
            "source_ranges": [[s, e] for s, e in ranges],
        }
        if with_stem:
            edl["vocals_stem"] = str(voice_wav)
        output = tmp_path / name
        assert edl_render.render_edl_clip_ffmpeg(source, output, edl)
        return output

    for label, ranges in (
        ("direct", [(0.0, 16.0)]),
        ("materialised", [(0.0, 8.0), (8.0, 16.0)]),
    ):
        off = render(f"off_{label}.mp4", ranges, with_stem=False)
        on = render(f"on_{label}.mp4", ranges, with_stem=True)
        # Speech window 3-5.5 s; music-only window 8.5-11 s (the second
        # range's opening on the materialised timeline shifts by the
        # crossfade, well inside the window's tolerance).
        speech_off = _mean_volume_db(off, 3.0, 2.5)
        speech_on = _mean_volume_db(on, 3.0, 2.5)
        music_off = _mean_volume_db(off, 8.5, 2.5)
        music_on = _mean_volume_db(on, 8.5, 2.5)
        attenuation = music_off - music_on
        assert attenuation >= 15.0, (
            f"{label}: music window attenuation {attenuation:.1f} dB "
            f"(off {music_off:.1f}, on {music_on:.1f})"
        )
        assert abs(speech_on - speech_off) <= 3.0, (
            f"{label}: speech window moved {speech_on - speech_off:+.1f} dB "
            f"(off {speech_off:.1f}, on {speech_on:.1f})"
        )
