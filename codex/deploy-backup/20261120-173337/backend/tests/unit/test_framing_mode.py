"""Framing mode: the create-time framing choice (auto / fit / crop).

Luffy (2026-11-16), comparing a podcast clip that kept the whole
16:9 frame inside the 9:16 output with blurred bands against a
finance clip rendered full-bleed cropped: he wants the FIRST one,
and a way to choose. ``framing_mode`` is that choice — an independent
per-task setting next to ``template_mode``:

- ``auto`` (and NULL, every task that predates the control): today's
  behaviour exactly — Plain tasks crop, Auto tasks let the layout
  planner decide per shot.
- ``fit``: the whole source frame inside the output for every shot,
  blurred bands filling the rest; nothing is ever cropped away.
- ``crop``: the full-bleed face-tracked crop for every shot, as a
  framing choice only — Plain's other restrictions do NOT come with
  it (an Auto task with crop framing keeps its music, B-roll and
  effects; a Plain task with fit framing letterboxes).

The suite pins the whole setting lifecycle the P0 readback lesson
demands: validation (create route 422 on garbage), storage, read-back
through BOTH serializers against real SQL, validate+warn at the
render-loop reads, precedence over the template default, planner
forcing at the attach seam, and a rendered proof on a wide
podcast-style fixture.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest
from fastapi import HTTPException

from sql_exec_support import ExecSession

from src.api.routes import tasks as tasks_route
from src.auto_director.framing_mode import (
    FRAMING_AUTO,
    FRAMING_CROP,
    FRAMING_FIT,
    FRAMING_MODES,
    resolve_framing_mode,
    validate_framing_mode,
)
from src.media import edl_render, reframe_plan, reframing
from src.repositories.task_repository import TaskRepository


# ---------------------------------------------------------------------------
# Validator + precedence resolution (pure)
# ---------------------------------------------------------------------------


def test_validate_framing_mode_absent_is_none():
    # Mirrors validate_template_mode exactly: only a missing value is
    # absent; an empty string is a present, invalid value.
    assert validate_framing_mode(None) is None


def test_validate_framing_mode_accepts_the_three_values():
    assert validate_framing_mode("auto") == FRAMING_AUTO
    assert validate_framing_mode("fit") == FRAMING_FIT
    assert validate_framing_mode("crop") == FRAMING_CROP
    assert validate_framing_mode(" Fit ") == FRAMING_FIT
    assert FRAMING_MODES == ("auto", "fit", "crop")


def test_validate_framing_mode_rejects_garbage():
    for bad in ("wide", "letterbox", "plain", "full", "", 7, True):
        with pytest.raises(ValueError):
            validate_framing_mode(bad)


@pytest.mark.parametrize(
    "framing_mode,template_mode,expected",
    [
        # Unset: the template default decides, exactly as today.
        (None, None, "auto"),
        (None, "auto", "auto"),
        (None, "plain", "crop"),
        # An explicit choice always wins, including explicit auto on a
        # Plain task (the planner decides; the rest of Plain applies).
        ("auto", None, "auto"),
        ("auto", "auto", "auto"),
        ("auto", "plain", "auto"),
        ("fit", None, "fit"),
        ("fit", "auto", "fit"),
        ("fit", "plain", "fit"),
        ("crop", None, "crop"),
        ("crop", "auto", "crop"),
        ("crop", "plain", "crop"),
    ],
)
def test_resolve_framing_mode_precedence_matrix(framing_mode, template_mode, expected):
    assert resolve_framing_mode(framing_mode, template_mode) == expected


# ---------------------------------------------------------------------------
# Repository round-trip through real SQL (both serializers)
# ---------------------------------------------------------------------------

_TASKS_DDL = """
CREATE TABLE tasks (
    id TEXT, user_id TEXT, source_id TEXT, status TEXT,
    font_family TEXT, font_size INTEGER, font_color TEXT,
    caption_template TEXT, include_broll INTEGER, processing_mode TEXT,
    niche_override TEXT, music_override TEXT, music_mode TEXT,
    template_mode TEXT, framing_mode TEXT, layout_template TEXT, layout_credit TEXT, hook_title_enabled INTEGER, broll_enabled INTEGER,
    separation_opt_in INTEGER,
    target_clip_count INTEGER, source_duration_seconds INTEGER,
    content_brief TEXT, campaign_id TEXT, brand_kit_id TEXT,
    intro_asset TEXT, outro_asset TEXT, creation_mode TEXT,
    manual_ranges_json TEXT, generated_clips_ids TEXT,
    progress INTEGER, progress_message TEXT, brief_report TEXT,
    classification TEXT, cache_hit INTEGER, error_code TEXT,
    stage_timings_json TEXT, started_at TEXT, completed_at TEXT,
    completion_notification_sent_at TEXT,
    created_at TEXT, updated_at TEXT
)
"""

_SOURCES_DDL = """
CREATE TABLE sources (id TEXT, title TEXT, type TEXT, url TEXT)
"""

_CLIPS_DDL = """
CREATE TABLE generated_clips (
    id TEXT, task_id TEXT, virality_score REAL, clip_order INTEGER
)
"""


def _session() -> ExecSession:
    session = ExecSession(_TASKS_DDL, _SOURCES_DDL, _CLIPS_DDL)
    session.conn.execute(
        "INSERT INTO sources (id, title, type, url) VALUES "
        "('src-1', 'A video', 'youtube', 'https://example.invalid/v')"
    )
    return session


@pytest.mark.parametrize("value", ["auto", "fit", "crop", None])
async def test_framing_mode_survives_both_serializers(value):
    session = _session()
    task_id = await TaskRepository.create_task(
        session,
        user_id="user-1",
        source_id="src-1",
        source_duration_seconds=600,
        framing_mode=value,
    )
    by_id = await TaskRepository.get_task_by_id(session, task_id)
    assert by_id["framing_mode"] == value
    listed = await TaskRepository.get_user_tasks(session, "user-1")
    assert listed[0]["framing_mode"] == value


# ---------------------------------------------------------------------------
# Create route: 422 on garbage, the value reaches the service
# ---------------------------------------------------------------------------

YOUTUBE_URL = "https://www.youtube.com/watch?v=abcdefghijk"


def _request(body, queue_adapter=None):
    return SimpleNamespace(
        json=AsyncMock(return_value=body),
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(queue_adapter=queue_adapter or _QueueAdapter())
        ),
    )


class _QueueAdapter:
    async def enqueue_processing_job(self, *args, **kwargs):
        return "job-1"


class _FakeBillingService:
    def __init__(self, db):
        pass

    async def assert_can_create_task(self, *args, **kwargs):
        return {
            "max_youtube_duration_seconds": 100000,
            "max_upload_duration_seconds": 100000,
        }


class _FakeTaskService:
    captured: dict = {}
    reused_upload_task = False

    def __init__(self, db):
        self.video_service = SimpleNamespace(
            determine_source_type=lambda url: "youtube"
        )
        self.task_repo = SimpleNamespace(update_task_status=AsyncMock())

    async def create_task_with_source(self, **kwargs):
        _FakeTaskService.captured = kwargs
        return "task-1"


@contextlib.asynccontextmanager
async def _no_guard(*args, **kwargs):
    yield


def _wire_create_route(monkeypatch):
    monkeypatch.setattr(
        tasks_route, "_get_user_id_from_headers", AsyncMock(return_value="user-1")
    )
    monkeypatch.setattr(
        "src.source_submission.record_source_submission", AsyncMock()
    )
    monkeypatch.setattr(tasks_route, "BillingService", _FakeBillingService)
    monkeypatch.setattr(tasks_route, "TaskService", _FakeTaskService)
    monkeypatch.setattr(tasks_route, "generation_allowance_guard", _no_guard)
    monkeypatch.setattr(tasks_route, "_save_task_source_metadata", AsyncMock())
    monkeypatch.setattr(
        tasks_route,
        "_preflight_youtube_source",
        AsyncMock(return_value={"duration": 600.0, "title": "Source"}),
    )
    _FakeTaskService.captured = {}


async def test_create_task_rejects_invalid_framing_mode_with_422(monkeypatch):
    _wire_create_route(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await tasks_route.create_task(
            _request(
                {"source": {"url": YOUTUBE_URL}, "framing_mode": "letterbox"}
            ),
            db=None,
        )
    assert error.value.status_code == 422


@pytest.mark.parametrize("value", ["auto", "fit", "crop"])
async def test_create_task_forwards_a_valid_framing_mode(monkeypatch, value):
    _wire_create_route(monkeypatch)
    result = await tasks_route.create_task(
        _request({"source": {"url": YOUTUBE_URL}, "framing_mode": value}),
        db=None,
    )
    assert result["task_id"] == "task-1"
    assert _FakeTaskService.captured["framing_mode"] == value


async def test_create_task_omitted_framing_mode_stays_none(monkeypatch):
    _wire_create_route(monkeypatch)
    result = await tasks_route.create_task(
        _request({"source": {"url": YOUTUBE_URL}}),
        db=None,
    )
    assert result["task_id"] == "task-1"
    assert _FakeTaskService.captured["framing_mode"] is None


# ---------------------------------------------------------------------------
# Render-loop read: process_task re-validates the stored value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_task_warns_and_defaults_on_invalid_stored_framing(
    monkeypatch, caplog
):
    from test_task_service import _FakeBillingService, build_task_service

    import src.services.task_service as task_service_module

    service = build_task_service()
    service.task_repo.get_task_by_id = AsyncMock(
        return_value={"status": "queued", "framing_mode": "bogus"}
    )
    monkeypatch.setattr(task_service_module, "BillingService", _FakeBillingService)

    with caplog.at_level(logging.WARNING, logger="src.services.task_service"):
        await service.process_task(
            task_id="task-1",
            url="https://www.youtube.com/watch?v=demo",
            source_type="youtube",
            user_id="user-1",
        )

    kwargs = service.video_service.create_single_clip.await_args.kwargs
    assert kwargs["framing_mode"] is None
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "Ignoring invalid stored framing_mode 'bogus' on task task-1" in m
        for m in messages
    )


@pytest.mark.asyncio
async def test_process_task_forwards_a_valid_stored_framing_mode(monkeypatch):
    from test_task_service import _FakeBillingService, build_task_service

    import src.services.task_service as task_service_module

    service = build_task_service()
    service.task_repo.get_task_by_id = AsyncMock(
        return_value={"status": "queued", "framing_mode": "fit"}
    )
    monkeypatch.setattr(task_service_module, "BillingService", _FakeBillingService)

    await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
        user_id="user-1",
    )

    kwargs = service.video_service.create_single_clip.await_args.kwargs
    assert kwargs["framing_mode"] == "fit"


# ---------------------------------------------------------------------------
# Planner forcing at the attach seam
# ---------------------------------------------------------------------------

W, H = 1920, 1080
FACE = (200.0, 300.0, 150.0, 200.0)
DURATION = 6.0


def _analysis():
    samples = []
    t = 0.0
    while t < DURATION:
        samples.append((t, [FACE]))
        t += 1.0 / 3.0
    return {
        "width": W,
        "height": H,
        "samples": samples,
        "context": [],
        "cuts": [],
    }


def _edl():
    return {
        "clip_id": "framingproof",
        "duration": DURATION,
        "source_ranges": [[0.0, DURATION]],
        "segments": [
            {
                "index": 0,
                "start": 0.0,
                "end": DURATION,
                "transition_in": "none",
                "transition_duration": 0,
            }
        ],
    }


def _attach(monkeypatch, tmp_path, plain=False, framing=None):
    monkeypatch.setattr(
        reframe_plan, "get_source_analysis", lambda path, spans: _analysis()
    )
    edl = _edl()
    source = tmp_path / "source.mp4"
    source.touch()
    assert (
        reframe_plan.attach_reframe_plan(
            edl, source, [(0.0, DURATION)], plain, framing=framing
        )
        is True
    )
    return edl


def test_attach_fit_forces_every_shot_to_fit(monkeypatch, tmp_path):
    edl = _attach(monkeypatch, tmp_path, framing="fit")
    section = edl["reframe"]
    assert section["framing"] == "fit"
    assert "plain" not in section
    assert section["shots"]
    assert all(shot["mode"] == "fit" for shot in section["shots"])
    assert all(shot["reason"] == "user_fit" for shot in section["shots"])
    reframe = edl["segments"][0]["reframe"]
    assert "fit_layers" in reframe
    assert "crop" not in reframe
    # The face envelope rides through the letterbox transform, so the
    # caption/title guards keep a box to avoid: the fitted face lands
    # inside the 1080x608 foreground centred in the output. Entries
    # are {"start", "end", "boxes"} with [x, y, w, h] output boxes.
    entries = reframe["face_boxes"]
    assert entries
    boxes = [box for entry in entries for box in entry["boxes"]]
    assert boxes
    for x, y, w, h in boxes:
        assert 0 <= x < x + w <= 1080
        assert 656 <= y < y + h <= 1264


def test_attach_crop_forces_plain_geometry_without_the_plain_stamp(
    monkeypatch, tmp_path
):
    edl = _attach(monkeypatch, tmp_path, framing="crop")
    section = edl["reframe"]
    assert section["framing"] == "crop"
    assert "plain" not in section
    assert all(shot["mode"] == "plain" for shot in section["shots"])
    reframe = edl["segments"][0]["reframe"]
    assert "fit_layers" not in reframe
    assert "split_layers" not in reframe
    crop = reframe["crop"]
    # The same tight window the Plain planner sizes from this face.
    assert (crop["w"], crop["h"]) == (374, 664)


def test_attach_fit_wins_over_a_plain_task(monkeypatch, tmp_path):
    edl = _attach(monkeypatch, tmp_path, plain=True, framing="fit")
    section = edl["reframe"]
    assert section["framing"] == "fit"
    assert "plain" not in section
    assert all(shot["mode"] == "fit" for shot in section["shots"])
    assert "fit_layers" in edl["segments"][0]["reframe"]


def test_attach_crop_on_a_plain_task_stamps_framing_not_plain(
    monkeypatch, tmp_path
):
    edl = _attach(monkeypatch, tmp_path, plain=True, framing="crop")
    section = edl["reframe"]
    assert section["framing"] == "crop"
    assert "plain" not in section
    assert "crop" in edl["segments"][0]["reframe"]


def test_attach_unset_framing_keeps_todays_plain_behaviour(monkeypatch, tmp_path):
    edl = _attach(monkeypatch, tmp_path, plain=True)
    section = edl["reframe"]
    assert section["plain"] is True
    assert "framing" not in section
    assert "crop" in edl["segments"][0]["reframe"]


def test_attach_explicit_auto_matches_unset_exactly(monkeypatch, tmp_path):
    base = _attach(monkeypatch, tmp_path)
    explicit = _attach(monkeypatch, tmp_path, framing="auto")
    assert explicit["reframe"] == base["reframe"]
    assert explicit["segments"][0]["reframe"] == base["segments"][0]["reframe"]


def test_attach_explicit_auto_on_plain_uses_the_planner(monkeypatch, tmp_path):
    edl = _attach(monkeypatch, tmp_path, plain=True, framing="auto")
    section = edl["reframe"]
    assert "plain" not in section
    assert "framing" not in section
    # On this fixture the planner itself fits the wide shot — the point
    # is the decision is the planner's again, not Plain's crop rule.
    assert all(shot["mode"] == "fit" for shot in section["shots"])


def test_build_fit_plan_carries_the_face_envelope():
    plan = reframe_plan.build_fit_reframe_plan(
        _analysis(), [(0.0, DURATION)], DURATION
    )
    assert plan is not None
    assert plan["framing"] == "fit"
    assert plan["delegate"] is None
    (shot,) = plan["shots"]
    assert shot["mode"] == "fit"
    assert shot["reason"] == "user_fit"
    assert shot["face_src"] == [[200.0, 300.0, 150.0, 200.0]]


# ---------------------------------------------------------------------------
# Legacy batch stack (describe_vertical_layout)
# ---------------------------------------------------------------------------


def _tiny_source(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i",
            "color=c=0x202020:size=1920x1080:rate=30:duration=2",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_legacy_layout_fit_framing_is_the_plain_letterbox(tmp_path):
    source = _tiny_source(tmp_path / "tiny.mp4")
    described = reframing.describe_vertical_layout(
        source, W, H, framing="fit"
    )
    assert described["kind"] == "fit"
    assert described["video_filter"] == reframing.build_full_frame_filter()
    assert described["caption_y"] == 0.80


# ---------------------------------------------------------------------------
# Rendered proof: one wide podcast fixture, three framings
# ---------------------------------------------------------------------------

RED = (204, 32, 32)
BLUE = (32, 64, 204)


def _make_podcast_source(path: Path) -> Path:
    """A wide studio shot: dark room, a red panel at the far left edge,
    a blue panel at the far right edge, and a white marker exactly at
    the fabricated face centre — the speaker is small in the frame."""
    boxes = [
        "drawbox=x=0:y=140:w=820:h=620:color=0xCC2020:t=fill",
        "drawbox=x=1100:y=140:w=820:h=620:color=0x2040CC:t=fill",
        "drawbox=x=245:y=380:w=60:h=60:color=white:t=fill",
    ]
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i",
            "color=c=0x202020:size=1920x1080:rate=30:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-vf", ",".join(boxes),
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _render(monkeypatch, tmp_path, name, framing):
    source = _make_podcast_source(tmp_path / f"{name}_source.mp4")
    edl = _attach(monkeypatch, tmp_path, framing=framing)
    output = tmp_path / f"{name}.mp4"
    assert edl_render.render_edl_clip_ffmpeg(source, output, edl), name
    return output


def _frame(path: Path, t: float = 3.0) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080, 3).astype(np.int32)


def _colour_share(img: np.ndarray, rgb, rows=None) -> float:
    view = img if rows is None else img[rows]
    r, g, b = (view[:, :, i] for i in range(3))
    if rgb == RED:
        mask = (r > 150) & (r > g + 60) & (r > b + 60)
    else:
        mask = (b > 150) & (b > r + 60) & (b > g + 40)
    return float(mask.mean())


def _edge_share(img: np.ndarray, rows, threshold: float = 40.0) -> float:
    """Share of pixels on a sharp edge (horizontal gradient above the
    threshold) — the fitted band keeps the source's crisp panel edges;
    the blurred backdrop has none at all."""
    view = img[rows].astype(np.float64)
    grad = np.abs(np.diff(view, axis=1)).max(axis=2)
    return float((grad > threshold).mean())


def _marker_in(img: np.ndarray, rows):
    """Centroid (full-frame coords) + count of near-white pixels in rows."""
    view = img[rows]
    white = np.abs(view - 255).sum(axis=2) < 90
    ys, xs = np.nonzero(white)
    if len(xs) == 0:
        return None
    return float(xs.mean()), float(ys.mean()) + rows.start, len(xs)


def test_rendered_proof_fit_keeps_the_whole_frame_crop_crops_it_away(
    monkeypatch, tmp_path
):
    fit_frame = _frame(_render(monkeypatch, tmp_path, "fit", "fit"))
    crop_frame = _frame(_render(monkeypatch, tmp_path, "crop", "crop"))
    auto_frame = _frame(_render(monkeypatch, tmp_path, "auto", None))

    # FIT: the ENTIRE source width is inside the output — both edge
    # panels survive in the fitted band (rows 656..1264 of the output).
    band = slice(656, 1264)
    assert _colour_share(fit_frame, RED, band) > 0.05
    assert _colour_share(fit_frame, BLUE, band) > 0.05
    # The bands above/below are the BLURRED backdrop: the panel hues
    # are there (blur keeps colour) but no sharp edge survives — the
    # measured edge-pixel share is zero, against real edges in the
    # fitted band where the panel borders stay crisp.
    assert _edge_share(fit_frame, slice(60, 600)) < 0.0005
    assert _edge_share(fit_frame, slice(700, 1200)) > 0.002
    # The face marker lands exactly where the letterbox transform puts
    # it: source centre (275, 410) -> (275*0.5625, 656 + 410*0.5625).
    marker = _marker_in(fit_frame, band)
    assert marker is not None
    assert abs(marker[0] - 155) < 45
    assert abs(marker[1] - 887) < 45

    # CROP: full-bleed — the tight window around the left-side speaker
    # never reaches the right-edge panel, and content fills the whole
    # output height (no letterbox band at the top).
    assert _colour_share(crop_frame, BLUE) < 0.005
    assert _colour_share(crop_frame, RED, slice(0, 300)) > 0.05
    # The speaker's marker is framed, large: the 60px source marker
    # through a 374px-wide window scaled to 1080 is ~170px across.
    crop_marker = _marker_in(crop_frame, slice(0, 1920))
    assert crop_marker is not None
    assert crop_marker[2] > 4000

    # AUTO on this fixture is the base behaviour, byte-for-byte the
    # plan the unset path produces (pinned at the plan level above);
    # its render keeps the whole frame just as the planner decided.
    assert _colour_share(auto_frame, BLUE, band) > 0.05
