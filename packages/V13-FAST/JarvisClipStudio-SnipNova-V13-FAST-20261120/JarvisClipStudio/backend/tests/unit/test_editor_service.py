from contextlib import asynccontextmanager
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.editor_document import EditDocument, atomic_json, editor_dir
from src.services.editor_service import (
    combine_editor,
    generated_emphasis,
    initial_caption_style,
    prepare_editor,
    read_state,
    rebased_overlays,
)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
async def test_combine_worker_keeps_inputs_and_creates_editable_draft(
    tmp_path, monkeypatch
):
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    clips = [
        {"id": f"clip{i}", "filename": f"clip{i}.mp4", "clip_order": i} for i in (1, 2)
    ]
    entries = []
    for clip in clips:
        directory = editor_dir(str(tmp_path), "task", clip["id"], clip["filename"])
        directory.mkdir(parents=True)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=blue:size=320x180:rate=30:duration=1",
                "-c:v",
                "libx264",
                str(directory / "clean.mp4"),
            ],
            capture_output=True,
            check=True,
        )
        doc = EditDocument.model_validate(
            {
                "segments": [{"id": "a", "start": 0.1, "end": 0.9}],
                "words": [{"id": "w", "start": 0.2, "end": 0.8, "text": clip["id"]}],
                "framing": {"aspect": "original"},
            }
        )
        entries.append(
            {
                **clip,
                "document": doc.model_dump(),
                "state": {"width": 320, "height": 180, "hasAudio": False},
                "ranges": [[0, 1]],
            }
        )
    directory = editor_dir(str(tmp_path), "task", "clip1", "clip1.mp4")
    job = "b" * 32
    atomic_json(directory / f"request-{job}.json", {"inputs": entries})
    atomic_json(directory / f"job-{job}.json", {"status": "queued"})
    created = []

    async def create(_db, *args, **kwargs):
        created.append(args)
        return kwargs["clip_id"]

    repository = SimpleNamespace(
        get_clips_by_task=AsyncMock(return_value=clips),
        create_clip=create,
        reorder_task_clips=AsyncMock(),
    )
    service = SimpleNamespace(
        clip_repo=repository, _seconds_to_mmss=lambda s: f"00:{int(s):02d}"
    )

    @asynccontextmanager
    async def sessions():
        yield object()

    @asynccontextmanager
    async def transaction(*args):
        yield

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _db: service)
    monkeypatch.setattr(
        "src.repositories.edit_transaction.task_edit_transaction", transaction
    )
    await combine_editor({}, "task", "clip1", "clip1.mp4", job)
    state = json.loads((directory / f"job-{job}.json").read_text())
    assert state["status"] == "completed", state
    assert state["clip_id"]
    assert len(clips) == 2
    assert len(created) == 1
    filename = created[0][1]
    combined = read_state(editor_dir(str(tmp_path), "task", state["clip_id"], filename))
    assert combined["status"] == "ready"
    assert [w["text"] for w in combined["draft"]["document"]["words"]] == [
        "clip1",
        "clip2",
    ]
    assert combined["duration"] == pytest.approx(1.6, abs=0.1)
    assert combined["draft"]["document"]["hookTitle"] == ""
    repository.reorder_task_clips.assert_awaited_once()


OVERLAY_ASSET = "overlay-" + "b" * 32 + ".png"


def test_rebased_overlays_maps_through_cuts_and_shifts_by_cursor():
    document = EditDocument.model_validate(
        {
            "segments": [
                {"id": "a", "start": 1.0, "end": 3.0},
                {"id": "b", "start": 5.0, "end": 6.0},
            ],
            "overlays": [
                {
                    "id": "o1",
                    "start": 0.5,
                    "end": 5.5,
                    "asset": OVERLAY_ASSET,
                    "name": "Logo",
                    "x": 0.25,
                    "y": 0.75,
                    "scale": 0.4,
                }
            ],
        }
    )
    pieces = rebased_overlays(document, cursor=10.0)
    # Segment a keeps 1.0-3.0 of the item (output 0-2), segment b keeps
    # 5.0-5.5 (output 2-2.5); both shift by the combine cursor.
    assert [(p["start"], p["end"]) for p in pieces] == [(10.0, 12.0), (12.0, 12.5)]
    assert all(
        p["asset"] == OVERLAY_ASSET and p["name"] == "Logo" and p["x"] == 0.25
        for p in pieces
    )


def _combine_clip(tmp_path, clip, *, color, audio, doc_extra, files=None):
    directory = editor_dir(str(tmp_path), "task", clip["id"], clip["filename"])
    directory.mkdir(parents=True)
    command = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color={color}:size=320x180:rate=30:duration=1",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac"]
    command += [str(directory / "clean.mp4")]
    subprocess.run(command, capture_output=True, check=True)
    for name, content in (files or {}).items():
        (directory / name).write_bytes(content)
    doc = EditDocument.model_validate(
        {
            "segments": [{"id": "a", "start": 0.1, "end": 0.9}],
            "words": [{"id": "w", "start": 0.2, "end": 0.8, "text": clip["id"]}],
            **doc_extra,
        }
    )
    return {
        **clip,
        "document": doc.model_dump(),
        "state": {"width": 320, "height": 180, "hasAudio": audio},
        "ranges": [[0, 1]],
    }


def _png(color="lime", size="40x40"):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}",
         "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"],
        capture_output=True, check=True,
    )
    return result.stdout


def _frame_pixel(path, at, x, y, width=320):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    offset = (y * width + x) * 3
    return raw[offset], raw[offset + 1], raw[offset + 2]


def _mean_volume(path, start, duration):
    result = subprocess.run(
        # No -v error: volumedetect reports at info level.
        ["ffmpeg", "-ss", f"{start}", "-t", f"{duration}", "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    for line in result.stderr.splitlines():
        if "mean_volume" in line:
            return float(line.split("mean_volume:")[1].strip().split()[0])
    raise AssertionError("volumedetect reported no mean_volume")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
async def test_combine_carries_each_clips_framing_grade_volume_and_overlays(
    tmp_path, monkeypatch
):
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    clips = [
        {"id": f"clip{i}", "filename": f"clip{i}.mp4", "clip_order": i} for i in (1, 2)
    ]
    entries = [
        _combine_clip(
            tmp_path, clips[0], color="blue", audio=True,
            doc_extra={
                "framing": {"aspect": "original"},
                "overlays": [
                    {
                        "id": "o1", "start": 0.2, "end": 0.8,
                        "asset": OVERLAY_ASSET, "name": "Logo",
                        "x": 0.5, "y": 0.5, "scale": 0.5,
                    }
                ],
            },
            files={OVERLAY_ASSET: _png()},
        ),
        _combine_clip(
            tmp_path, clips[1], color="red", audio=True,
            doc_extra={
                # Square framing pillarboxes this clip on the canvas, the
                # grade desaturates it, and it plays 12 dB quieter.
                "framing": {"aspect": "square"},
                "effects": {"saturation": 0},
                "volume": 0.25,
            },
        ),
    ]
    directory = editor_dir(str(tmp_path), "task", "clip1", "clip1.mp4")
    job = "c" * 32
    atomic_json(directory / f"request-{job}.json", {"inputs": entries})
    atomic_json(directory / f"job-{job}.json", {"status": "queued"})
    created = []

    async def create(_db, *args, **kwargs):
        created.append(args)
        return kwargs["clip_id"]

    repository = SimpleNamespace(
        get_clips_by_task=AsyncMock(return_value=clips),
        create_clip=create,
        reorder_task_clips=AsyncMock(),
    )
    service = SimpleNamespace(
        clip_repo=repository, _seconds_to_mmss=lambda s: f"00:{int(s):02d}"
    )

    @asynccontextmanager
    async def sessions():
        yield object()

    @asynccontextmanager
    async def transaction(*args):
        yield

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _db: service)
    monkeypatch.setattr(
        "src.repositories.edit_transaction.task_edit_transaction", transaction
    )
    await combine_editor({}, "task", "clip1", "clip1.mp4", job)
    state = json.loads((directory / f"job-{job}.json").read_text())
    assert state["status"] == "completed", state

    combined_dir = editor_dir(str(tmp_path), "task", state["clip_id"], created[0][1])
    combined = read_state(combined_dir)
    document = combined["draft"]["document"]
    # The overlay carried, rebased through clip 1's cut (0.2-0.8 inside
    # the kept 0.1-0.9 becomes 0.1-0.7 on the combined timeline), and its
    # file moved into the combined clip's editor directory.
    assert len(document["overlays"]) == 1
    overlay = document["overlays"][0]
    assert overlay["asset"] == OVERLAY_ASSET
    assert overlay["start"] == pytest.approx(0.1) and overlay["end"] == pytest.approx(0.7)
    assert (combined_dir / OVERLAY_ASSET).is_file()
    # Per-clip grade/volume were baked into the render; the draft itself
    # starts neutral so its own exports don't apply them twice.
    assert document["effects"]["saturation"] == 100
    assert document["volume"] == 1.0 and document["muted"] is False

    export = directory / f"export-{job}.mp4"
    # Clip 1's half: the lime overlay sits at the centre of the blue frame.
    r, g, b = _frame_pixel(export, 0.4, 160, 90)
    assert g > 150 and r < 120 and b < 120
    # Clip 2's half: square framing leaves black pillarbox bars...
    r, g, b = _frame_pixel(export, 1.2, 10, 90)
    assert r < 40 and g < 40 and b < 40
    # ...and its red is desaturated to grey by its own grade.
    r, g, b = _frame_pixel(export, 1.2, 160, 90)
    assert max(r, g, b) - min(r, g, b) < 40 and 40 < r < 140
    # Clip 2 plays at its own volume: ~12 dB below clip 1's half.
    first = _mean_volume(export, 0.05, 0.7)
    second = _mean_volume(export, 0.85, 0.7)
    assert first - second > 8


async def _run_combine(tmp_path, monkeypatch, entries, job):
    """Run combine_editor on prepared entries; return the combined state and export."""
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    directory = editor_dir(str(tmp_path), "task", "clip1", "clip1.mp4")
    atomic_json(directory / f"request-{job}.json", {"inputs": entries})
    atomic_json(directory / f"job-{job}.json", {"status": "queued"})
    created = []

    async def create(_db, *args, **kwargs):
        created.append(args)
        return kwargs["clip_id"]

    clips = [
        {"id": f"clip{i}", "filename": f"clip{i}.mp4", "clip_order": i} for i in (1, 2)
    ]
    repository = SimpleNamespace(
        get_clips_by_task=AsyncMock(return_value=clips),
        create_clip=create,
        reorder_task_clips=AsyncMock(),
    )
    service = SimpleNamespace(
        clip_repo=repository, _seconds_to_mmss=lambda s: f"00:{int(s):02d}"
    )

    @asynccontextmanager
    async def sessions():
        yield object()

    @asynccontextmanager
    async def transaction(*args):
        yield

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _db: service)
    monkeypatch.setattr(
        "src.repositories.edit_transaction.task_edit_transaction", transaction
    )
    await combine_editor({}, "task", "clip1", "clip1.mp4", job)
    state = json.loads((directory / f"job-{job}.json").read_text())
    assert state["status"] == "completed", state
    combined_dir = editor_dir(str(tmp_path), "task", state["clip_id"], created[0][1])
    return read_state(combined_dir), directory / f"export-{job}.mp4"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
@pytest.mark.parametrize("hidden_index", [0, 1])
async def test_combine_respects_each_clips_hidden_lanes(
    tmp_path, monkeypatch, hidden_index
):
    """D-C1: a clip's hidden captions/audio/B-roll must not leak into, or
    silence, the combination — in either input order.

    The hidden clip's words, footage and sound stay out of the combined
    draft and export; the visible clip's content survives; and the
    combined lanes must not re-hide a lane whose state was baked per
    clip, or pass 2 would drop the visible clip's content too.
    """
    footage = tmp_path / "footage.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=lime:s=320x180:r=30:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(footage),
        ],
        capture_output=True, check=True,
    )
    monkeypatch.setattr(
        "src.broll.ensure_footage_file", AsyncMock(return_value=footage)
    )
    clips = [
        {"id": f"clip{i}", "filename": f"clip{i}.mp4", "clip_order": i} for i in (1, 2)
    ]
    entries = []
    for i, clip in enumerate(clips):
        hidden = i == hidden_index
        entries.append(
            _combine_clip(
                tmp_path, clip, color=["blue", "red"][i], audio=True,
                doc_extra={
                    "framing": {"aspect": "original"},
                    "lanes": (
                        {
                            "captions": {"hidden": True},
                            "audio": {"hidden": True},
                            "broll": {"hidden": True},
                        }
                        if hidden
                        else {}
                    ),
                    "broll": [
                        {
                            "id": f"b{i + 1}", "start": 0.2, "end": 0.8,
                            "query": f"{clip['id']} broll",
                            "footage": {
                                "id": 42 + i, "width": 320, "height": 180,
                                "duration": 6,
                                "fileUrl": f"https://videos.pexels.com/{42 + i}.mp4",
                            },
                        }
                    ],
                },
            )
        )
    combined, export = await _run_combine(tmp_path, monkeypatch, entries, "d" * 32)
    document = combined["draft"]["document"]
    visible = "clip2" if hidden_index == 0 else "clip1"
    # Captions: only the visible clip's words survive the combine.
    assert [w["text"] for w in document["words"]] == [visible]
    # B-roll: only the visible clip's footage survives the combine.
    assert [b["query"] for b in document["broll"]] == [f"{visible} broll"]
    # The combined lanes must not re-hide a lane whose hidden state was
    # baked per clip, or the second pass would drop the visible clip's
    # captions, audio or B-roll as well.
    for lane in ("captions", "audio", "broll"):
        assert document["lanes"].get(lane, {}).get("hidden", False) is False
    # Rendered export, first half (clip 1) then second half (clip 2):
    # the hidden clip's half is silent and shows its own base colour;
    # the visible clip's half is audible and shows its B-roll footage.
    first = _mean_volume(export, 0.05, 0.7)
    second = _mean_volume(export, 0.85, 0.7)
    first_rgb = _frame_pixel(export, 0.4, 160, 90)
    second_rgb = _frame_pixel(export, 1.2, 160, 90)
    if hidden_index == 0:
        assert first < -60, first
        assert second > -40, second
        assert first_rgb[2] > 150 and first_rgb[0] < 120  # blue base, no B-roll
        assert second_rgb[1] > 150 and second_rgb[0] < 120  # lime B-roll
    else:
        assert first > -40, first
        assert second < -60, second
        assert first_rgb[1] > 150 and first_rgb[0] < 120  # lime B-roll
        assert second_rgb[0] > 150 and second_rgb[1] < 120  # red base, no B-roll


async def test_prepare_failure_is_recoverable(tmp_path, monkeypatch):
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)

    @asynccontextmanager
    async def sessions():
        yield object()

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=None)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=None)),
    )
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _: service)
    await prepare_editor({}, "task", "missing", "missing.mp4")
    state = read_state(editor_dir(str(tmp_path), "task", "missing", "missing.mp4"))
    assert state["status"] == "failed"


async def test_storage_failure_during_prepare_does_not_leave_preparing(tmp_path, monkeypatch):
    from src.config import Config
    from src.media_storage import MediaStorage
    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    atomic_json(directory / "asset.json", {"status": "preparing"})
    monkeypatch.setattr(MediaStorage, "restore_editor", lambda *a, **k: (_ for _ in ()).throw(OSError("S3 unavailable")))
    monkeypatch.setattr(MediaStorage, "save_editor", lambda *a, **k: None)
    await prepare_editor({}, "task", "clip", "clip.mp4")
    assert read_state(directory)["status"] == "failed"


def test_editor_starts_from_the_generated_caption_template():
    from src.media.captions import get_safe_vertical_position

    style = initial_caption_style(
        {"caption_template": "hormozi", "font_family": None, "font_color": None},
        {},
        True,
        1080,
        1920,
    )
    assert style["karaoke"] is True
    assert style["template"] == "hormozi"
    assert style["uppercase"] is True
    assert style["accent"] == "#00FF66"
    assert style["background"] is False
    assert style["wordsPerLine"] == 3
    assert style["font"] == "Montserrat-Black"
    # Same pixel size and centre row as the generated 1080x1920 clip.
    assert style["size"] == round(32 * 1080 / 560)
    assert style["y"] * 1920 == get_safe_vertical_position(1920, int(62 * 1.5), 0.74)
    EditDocument.model_validate(
        {"segments": [{"id": "s", "start": 0, "end": 1}], "captions": style}
    )

    minimal = initial_caption_style(
        {"caption_template": "minimal", "font_size": 24}, {"position_y": 0.5}, False, 1080, 1920
    )
    assert minimal["karaoke"] is False
    assert minimal["background"] is True
    assert minimal["enabled"] is False
    assert minimal["y"] < 0.5  # centred, not top-aligned, like the generated clip


def test_editor_keeps_generated_font_and_size_extremes():
    tiktok = initial_caption_style({"caption_template": "tiktok"}, {}, True, 1080, 1920)
    assert tiktok["font"] == "TikTokSans-Bold"

    largest = initial_caption_style({"font_size": 72}, {}, True, 1080, 1920)
    assert largest["size"] == 132

    # "original" clips keep the source size; the editor re-expresses it per 1080 px.
    landscape = initial_caption_style({"font_size": 24}, {}, True, 1920, 1080)
    assert landscape["size"] == round(round(24 * 1920 / 560) * 1080 / 1920)
    for style in (tiktok, largest, landscape):
        EditDocument.model_validate(
            {"segments": [{"id": "s", "start": 0, "end": 1}], "captions": style}
        )


def test_editor_seeds_words_per_line_from_the_clips_profile_not_the_template():
    # F-C: a football_edit clip (profile WPL 2) rendered with the hormozi
    # template (WPL 3) must open seeded at 2, not 3 — otherwise exporting
    # untouched re-groups the captions to the template value.
    style = initial_caption_style(
        {"caption_template": "hormozi"},
        {},
        True,
        1080,
        1920,
        niche="football_edit",
    )
    assert style["wordsPerLine"] == 2
    EditDocument.model_validate(
        {"segments": [{"id": "s", "start": 0, "end": 1}], "captions": style}
    )


def test_editor_sidecar_words_per_line_beats_the_profile():
    # An explicit words-per-line the user set (sidecar) still wins over the
    # profile default; the profile only supplies the default.
    style = initial_caption_style(
        {"caption_template": "hormozi"},
        {"words_per_line": 5},
        True,
        1080,
        1920,
        niche="football_edit",
    )
    assert style["wordsPerLine"] == 5


def test_editor_unknown_niche_falls_back_to_the_template():
    # Fail-safe: a niche that cannot be resolved never raises and never
    # changes the seed — the template value is used, as before.
    style = initial_caption_style(
        {"caption_template": "hormozi"},
        {},
        True,
        1080,
        1920,
        niche="no-such-niche",
    )
    assert style["wordsPerLine"] == 3


def test_editor_unusable_profile_words_per_line_falls_back_to_the_template(monkeypatch):
    # A profile whose words_per_line is missing or unusable is ignored the
    # same way — the template value seeds the editor, never an exception.
    import src.auto_director.edl as edl_module

    monkeypatch.setattr(edl_module, "load_profile", lambda niche: {"captions": {}})
    missing = initial_caption_style(
        {"caption_template": "hormozi"}, {}, True, 1080, 1920, niche="football_edit"
    )
    assert missing["wordsPerLine"] == 3

    monkeypatch.setattr(
        edl_module, "load_profile", lambda niche: {"captions": {"words_per_line": "many"}}
    )
    unusable = initial_caption_style(
        {"caption_template": "hormozi"}, {}, True, 1080, 1920, niche="football_edit"
    )
    assert unusable["wordsPerLine"] == 3


def test_resolve_clip_profile_niche_precedence():
    from src.services.editor_service import resolve_clip_profile_niche

    # Clip override beats task override beats classifier beats fallback,
    # the same order generation resolves the profile in.
    assert (
        resolve_clip_profile_niche(
            {"niche_override": "football_edit"},
            {"niche_override": "podcast_news", "classification": {"niche": "gaming"}},
        )
        == "football_edit"
    )
    assert (
        resolve_clip_profile_niche(
            {},
            {"niche_override": "podcast_news", "classification": {"niche": "gaming"}},
        )
        == "podcast_news"
    )
    assert (
        resolve_clip_profile_niche({}, {"classification": {"niche": "gaming"}})
        == "gaming"
    )
    # No override and no classification: the generation fallback.
    assert resolve_clip_profile_niche({}, {}) == "podcast_news"
    # An unknown classifier value never resolves to a bogus profile.
    assert (
        resolve_clip_profile_niche({}, {"classification": {"niche": "no-such-niche"}})
        == "podcast_news"
    )
    assert resolve_clip_profile_niche(None, {"classification": "not-a-dict"}) == (
        "podcast_news"
    )


def test_editor_seeds_the_generated_emphasis_as_highlights():
    words = [{"text": t, "start": i, "end": i + 0.5} for i, t in enumerate("this changed everything for 90% of people".split())]
    emphasis = generated_emphasis(words, {"caption_template": "default"})
    assert {words[i]["text"] for i in emphasis} >= {"90%"}
    assert "of" not in {words[i]["text"] for i in emphasis}
