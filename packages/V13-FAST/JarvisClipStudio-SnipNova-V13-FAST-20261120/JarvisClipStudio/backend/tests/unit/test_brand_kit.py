"""Brand Kit (tracker 3.11): CRUD, validation, precedence, kit-off identity.

The kit is the USER's branding applied to their clips. These tests pin
the contract at every level that does not need a database or a render:

- payload validation (bad colour/template/style/etc. rejected, and the
  create route persists NOTHING for invalid input),
- kit CRUD through the API routes with a fake repository (the
  test_publishing_routes.py pattern),
- logo storage through the brand asset folder,
- render-input precedence: task-level explicit choice > kit default >
  profile default (resolve_brand_application + the EDL stamp),
- kit-off identity: no brand blocks on the EDL, no brand fragments in
  the filter graph, byte-identical ASS without a brand title block,
  and the deliverable cover graph exactly as it was before the kit.

The real-FFmpeg proof lives in test_brand_kit_render.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from src.api.routes import brand_kits as routes
from src.auto_director import brand_kit as bk
from src.caption_templates import get_template
from src.deliverable_bundle import _cover_filter_graph
from src.media import captions, edl_render, music_library
from src.media.edl_render import build_drawtext_filter, build_edl_filter_graph
from src.services.video_service import VideoService

KIT = {
    "id": "kit-1",
    "user_id": "user-1",
    "name": "My Channel",
    "primary_color": "#7C5CFF",
    "accent_color": "#38E1FF",
    "caption_template": "hormozi",
    "font_name": None,
    "title_style": "question",
    "music_track": None,
    "logo_filename": None,
    "logo_position": "bottom_right",
    "logo_opacity": 0.85,
    "intro_asset": None,
    "outro_asset": None,
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad", ["red", "#FFF", "#GGGGGG", "7C5CFF", "#7C5CFF00", 123]
)
def test_validate_color_rejects_non_hex(bad):
    with pytest.raises(ValueError):
        bk.validate_color(bad, "primary_color")


def test_validate_color_normalises_uppercase():
    assert bk.validate_color("#7c5cff", "primary_color") == "#7C5CFF"
    assert bk.validate_color(None, "primary_color") is None


def test_validate_kit_payload_normalises_and_defaults():
    fields = bk.validate_kit_payload(
        {"name": "  My Channel ", "primary_color": "#7c5cff",
         "accent_color": "#38e1ff", "title_style": "question"}
    )
    assert fields["name"] == "My Channel"
    assert fields["primary_color"] == "#7C5CFF"
    assert fields["accent_color"] == "#38E1FF"
    assert fields["logo_position"] == "bottom_right"
    assert fields["logo_opacity"] == 0.85


@pytest.mark.parametrize(
    "payload",
    [
        {},  # name is required on create
        {"name": "x", "primary_color": "red"},
        {"name": "x", "caption_template": "not-a-template"},
        {"name": "x", "title_style": "shouty"},
        {"name": "x", "logo_position": "middle"},
        {"name": "x", "logo_opacity": 0.05},
        {"name": "x", "logo_opacity": 1.5},
        {"name": "x", "logo_opacity": "high"},
        {"name": "x", "intro_asset": "intro.txt"},
        {"name": "x", "intro_asset": "folder/intro.mp4"},
        {"name": "x", "logo_filename": "logo.png"},  # upload endpoint only
        {"name": "x", "surprise_field": 1},
    ],
)
def test_validate_kit_payload_rejects_bad_input(payload):
    with pytest.raises(ValueError):
        bk.validate_kit_payload(payload)


def test_validate_kit_payload_accepts_real_template_and_font():
    fields = bk.validate_kit_payload(
        {"name": "x", "caption_template": "hormozi", "font_name": "Anton-Regular"}
    )
    assert fields["caption_template"] == "hormozi"
    assert fields["font_name"] == "Anton-Regular"


def test_validate_kit_payload_partial_skips_name_and_defaults():
    fields = bk.validate_kit_payload({"accent_color": "#38e1ff"}, partial=True)
    assert fields == {"accent_color": "#38E1FF"}


def test_validate_kit_payload_music_uses_live_library(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "music" / "kit-track.wav").touch()
    user_dir = tmp_path / "user_music"
    user_dir.mkdir()
    monkeypatch.setattr(edl_render, "ASSETS_DIR", assets)
    monkeypatch.setattr(music_library, "user_music_dir", lambda: user_dir)
    fields = bk.validate_kit_payload({"name": "x", "music_track": "kit-track.wav"})
    assert fields["music_track"] == "kit-track.wav"
    with pytest.raises(ValueError):
        bk.validate_kit_payload({"name": "x", "music_track": "missing.wav"})


# ---------------------------------------------------------------------------
# Routes with a fake repository
# ---------------------------------------------------------------------------


class FakeBrandKitRepository:
    """Mirrors BrandKitRepository's contract, in memory."""

    def __init__(self):
        self.kits = {}
        self.deleted = []

    async def list_kits(self, db, user_id):
        mine = [k for k in self.kits.values() if k["user_id"] == user_id]
        return list(reversed(mine))  # newest first, like ORDER BY created_at DESC

    async def get_kit(self, db, user_id, kit_id):
        kit = self.kits.get(kit_id)
        # Like the real repository: a fresh row dict per call, never the
        # live stored object (routes hold results across later updates).
        return dict(kit) if kit and kit["user_id"] == user_id else None

    async def create_kit(self, db, kit):
        self.kits[kit["id"]] = dict(kit)
        return kit

    async def update_kit(self, db, user_id, kit_id, fields):
        kit = await self.get_kit(db, user_id, kit_id)
        if kit is None:
            return None
        if not fields:
            return kit
        kit.update(fields)
        # Persist like the real repository's UPDATE ... RETURNING flow.
        self.kits[kit_id] = kit
        return dict(kit)  # routes may hold the result across later mutations

    async def delete_kit(self, db, user_id, kit_id):
        if await self.get_kit(db, user_id, kit_id) is None:
            return False
        del self.kits[kit_id]
        self.deleted.append(kit_id)
        return True


class FakeRequest:
    def __init__(self, payload=None):
        self._payload = payload or {}

    async def json(self):
        return self._payload


class FakeUploadFile:
    def __init__(self, filename, data=b""):
        self.filename = filename
        self._data = data

    async def read(self, size=-1):
        data, self._data = self._data, b""
        return data


@pytest.fixture
def repo(monkeypatch):
    fake = FakeBrandKitRepository()
    monkeypatch.setattr(routes, "BrandKitRepository", fake)

    async def fake_user_id(request, db):
        return "user-1"

    monkeypatch.setattr(routes, "_get_authenticated_user_id", fake_user_id)
    return fake


async def test_create_with_bad_colour_is_422_and_persists_nothing(repo):
    with pytest.raises(HTTPException) as exc:
        await routes.create_brand_kit(
            FakeRequest({"name": "x", "primary_color": "red"}), db=None
        )
    assert exc.value.status_code == 422
    assert repo.kits == {}


async def test_kit_crud_round_trip(repo):
    created = await routes.create_brand_kit(
        FakeRequest({"name": "My Channel", "primary_color": "#7c5cff",
                     "accent_color": "#38E1FF", "title_style": "question"}),
        db=None,
    )
    assert created["name"] == "My Channel"
    assert created["primary_color"] == "#7C5CFF"
    assert created["logo_present"] is False
    kit_id = created["id"]

    fetched = await routes.get_brand_kit(kit_id, FakeRequest(), db=None)
    assert fetched["id"] == kit_id

    updated = await routes.update_brand_kit(
        kit_id, FakeRequest({"accent_color": "#00ff00"}), db=None
    )
    assert updated["accent_color"] == "#00FF00"
    assert updated["primary_color"] == "#7C5CFF"  # untouched field kept

    listing = await routes.list_brand_kits(FakeRequest(), db=None)
    assert [k["id"] for k in listing["kits"]] == [kit_id]

    assert await routes.delete_brand_kit(kit_id, FakeRequest(), db=None) == {
        "deleted": True, "id": kit_id,
    }
    with pytest.raises(HTTPException) as exc:
        await routes.get_brand_kit(kit_id, FakeRequest(), db=None)
    assert exc.value.status_code == 404


async def test_other_users_kit_is_404(repo, monkeypatch):
    created = await routes.create_brand_kit(FakeRequest({"name": "Mine"}), db=None)

    async def other_user(request, db):
        return "user-2"

    monkeypatch.setattr(routes, "_get_authenticated_user_id", other_user)
    with pytest.raises(HTTPException) as exc:
        await routes.get_brand_kit(created["id"], FakeRequest(), db=None)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await routes.update_brand_kit(
            created["id"], FakeRequest({"name": "stolen"}), db=None
        )
    assert exc.value.status_code == 404


async def test_logo_upload_rejects_bad_extension(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    created = await routes.create_brand_kit(FakeRequest({"name": "Mine"}), db=None)
    with pytest.raises(HTTPException) as exc:
        await routes.upload_brand_logo(
            created["id"], FakeRequest(), db=None,
            file=FakeUploadFile("logo.gif", b"GIF89a"),
        )
    assert exc.value.status_code == 400
    assert repo.kits[created["id"]]["logo_filename"] is None


async def test_logo_upload_stores_file_and_marks_kit(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    created = await routes.create_brand_kit(FakeRequest({"name": "Mine"}), db=None)
    result = await routes.upload_brand_logo(
        created["id"], FakeRequest(), db=None,
        file=FakeUploadFile("My Logo.PNG", b"\x89PNG-fake-bytes"),
    )
    assert result["logo_filename"] == "My Logo.png"  # spaces kept, ext lowercased
    assert result["logo_present"] is True
    stored = bk.logo_path("user-1", "My Logo.png")
    assert stored is not None and stored.read_bytes() == b"\x89PNG-fake-bytes"

    cleared = await routes.delete_brand_logo(created["id"], FakeRequest(), db=None)
    assert cleared["logo_filename"] is None
    assert cleared["logo_present"] is False
    assert bk.logo_path("user-1", "My Logo.png") is None


# ---------------------------------------------------------------------------
# Logo storage + watermark block
# ---------------------------------------------------------------------------


def test_store_logo_and_watermark_block(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    source = tmp_path / "upload.png"
    source.write_bytes(b"png-bytes")
    filename = bk.store_logo("user-1", source, "logo.png")
    assert filename == "logo.png"
    # A second upload of the same name gets a fresh name, never an overwrite.
    assert bk.store_logo("user-1", source, "logo.png") == "logo-2.png"

    kit = {**KIT, "logo_filename": "logo.png", "logo_opacity": 0.5}
    block = bk.brand_watermark(kit, "user-1")
    assert block["path"].endswith("logo.png")
    assert block["position"] == "bottom_right"
    assert block["opacity"] == 0.5
    # A kit whose logo file is gone produces no block (renders unbranded).
    assert bk.brand_watermark({**KIT, "logo_filename": "gone.png"}, "user-1") is None
    assert bk.brand_watermark(KIT, "user-1") is None


# ---------------------------------------------------------------------------
# Precedence: task explicit > kit default > profile default
# ---------------------------------------------------------------------------


def test_resolve_full_kit():
    app = bk.resolve_brand_application(KIT, user_id="user-1")
    assert app["brand_template_override"] == {
        "font_color": "#7C5CFF",
        "highlight_color": "#38E1FF",
        "emphasis_color": "#38E1FF",
    }
    assert app["brand_title"] == {"mode": "band", "color": "#7C5CFF"}
    assert app["title_style"] == "question"
    assert app["font_color"] == "#7C5CFF"
    assert app["brand_watermark"] is None  # no logo stored


def test_resolve_task_font_color_wins_over_kit_primary():
    app = bk.resolve_brand_application(
        KIT, user_id="user-1", task_font_color="#112233"
    )
    override = app["brand_template_override"]
    assert "font_color" not in override  # the task's colour is kept
    assert override["highlight_color"] == "#38E1FF"  # accent still applies
    assert app["font_color"] is None  # plain path keeps the task colour too


def test_resolve_accent_only_kit_uses_accent_text_title():
    kit = {**KIT, "primary_color": None}
    app = bk.resolve_brand_application(kit, user_id="user-1")
    assert app["brand_title"] == {"mode": "accent_text", "color": "#38E1FF"}
    assert app["brand_template_override"] == {
        "highlight_color": "#38E1FF", "emphasis_color": "#38E1FF",
    }
    assert app["font_color"] is None


def test_resolve_no_kit_is_all_none():
    app = bk.resolve_brand_application(None, user_id="user-1")
    assert app == {
        "brand_template_override": None,
        "brand_title": None,
        "brand_watermark": None,
        "brand_bumpers": {},
        "title_style": None,
        "font_color": None,
    }


# ---------------------------------------------------------------------------
# EDL stamping + kit-off identity
# ---------------------------------------------------------------------------


def _segment():
    return {"start_time": "00:00", "end_time": "00:12", "text": "a podcast segment"}


def _words():
    return [
        {"text": "hello", "start": 0.0, "end": 0.4},
        {"text": "there", "start": 0.5, "end": 0.9},
    ]


def _library(tmp_path, monkeypatch, track="kit-track.wav"):
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "music" / track).touch()
    (assets / "music" / "calm.wav").touch()
    user_dir = tmp_path / "user_music"
    user_dir.mkdir()
    monkeypatch.setattr(edl_render, "ASSETS_DIR", assets)
    monkeypatch.setattr(music_library, "user_music_dir", lambda: user_dir)
    return assets


def test_pipeline_edl_stamps_brand_blocks():
    app = bk.resolve_brand_application(KIT, user_id="user-1")
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0,
        brand_application=app,
    )
    assert edl is not None
    assert edl["brand_template_override"]["font_color"] == "#7C5CFF"
    assert edl["brand_title"] == {"mode": "band", "color": "#7C5CFF"}
    assert edl["hook"]["title_style"] == "question"
    assert "brand_watermark" not in edl  # kit has no logo


def test_pipeline_edl_without_kit_has_no_brand_blocks():
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0
    )
    assert edl is not None
    assert not any(key.startswith("brand_") for key in edl)
    assert edl["hook"]["title_style"] != "question"  # profile default kept


def test_pipeline_edl_kit_music_arrives_as_override_and_wins(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    # Creation-time fill hands the kit's track to the pipeline AS the
    # task's music_override, so it beats the profile/auto bed.
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0,
        music_override="kit-track.wav",
    )
    assert edl is not None
    assert edl["audio"]["music_track"] == "kit-track.wav"
    assert "music_source" not in edl["audio"]


# ---------------------------------------------------------------------------
# Captions: brand colours in the ASS, identity without a kit
# ---------------------------------------------------------------------------


def _build_ass(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    output = tmp_path / "captions.ass"
    words = [
        {"text": text, "start": i * 0.4, "end": i * 0.4 + 0.35}
        for i, text in enumerate("hello brave new world".split())
    ]
    assert captions.build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4", 0.0, 10.0, 1080, 1920, output,
        caption_words=words, **kwargs,
    )
    return output.read_text(encoding="utf-8")


def test_ass_carries_kit_colours_and_title_band(tmp_path, monkeypatch):
    override = {**get_template("default"), "font_color": "#7C5CFF",
                "highlight_color": "#38E1FF", "emphasis_color": "#38E1FF"}
    content = _build_ass(
        tmp_path, monkeypatch, template_override=override,
        hook_title="A Big Moment", brand_title={"mode": "band", "color": "#7C5CFF"},
    )
    # ASS colours are &HAABBGGRR: primary violet + accent cyan present.
    assert "&H00FF5C7C&" in content
    assert "&H00FFE138&" in content
    hook_line = next(l for l in content.splitlines() if l.startswith("Style: Hook"))
    assert ",3," in hook_line  # BorderStyle 3: the coloured band behind the title


def test_ass_without_brand_is_identical(tmp_path, monkeypatch):
    plain = _build_ass(tmp_path, monkeypatch, hook_title="A Big Moment")
    also_plain = _build_ass(
        tmp_path, monkeypatch, hook_title="A Big Moment", brand_title=None
    )
    assert plain == also_plain
    assert "&H00FFFFFF&" in plain  # stock template primary


# ---------------------------------------------------------------------------
# Filter graph: watermark fragments only with a brand block
# ---------------------------------------------------------------------------


def _edl(logo: Path | None):
    edl = {
        "duration": 12,
        "segments": [
            {"start": 0, "end": 12, "transition_in": "none", "transition_duration": 0}
        ],
    }
    if logo is not None:
        edl["brand_watermark"] = {
            "path": str(logo), "position": "bottom_right", "opacity": 1.0,
            "width_frac": 0.16, "margin_frac": 0.03,
        }
    return edl


def test_filter_graph_watermark_fragments(tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    _, graph, _, _ = build_edl_filter_graph(_edl(logo), tmp_path / "in.mp4")
    assert "brandlogo" in graph and "brandwm" in graph

    _, plain_graph, _, _ = build_edl_filter_graph(_edl(None), tmp_path / "in.mp4")
    assert "brandlogo" not in plain_graph

    # A watermark block whose file vanished leaves the graph untouched.
    ghost = {**_edl(logo), "brand_watermark": {
        "path": str(tmp_path / "gone.png"), "position": "bottom_right",
        "opacity": 1.0, "width_frac": 0.16, "margin_frac": 0.03,
    }}
    _, ghost_graph, _, _ = build_edl_filter_graph(ghost, tmp_path / "in.mp4")
    assert "brandlogo" not in ghost_graph


# ---------------------------------------------------------------------------
# Cover graph: kit treatment on the thumbnail, identity without a kit
# ---------------------------------------------------------------------------


def test_cover_graph_without_brand_is_the_historical_graph(tmp_path):
    graph, extra = _cover_filter_graph("Title", None)
    reference = build_drawtext_filter("Title", 0, 1, "[0:v]", "[cover]", sidecar_dir=tmp_path)
    # Identity modulo the per-call sidecar filename (each call writes its
    # own textfile; the graph shape and styling must be identical).
    import re

    def _mask(value: str) -> str:
        return re.sub(r"textfile='[^']+'", "textfile='X'", value)

    assert _mask(graph) == _mask(reference)
    assert "boxcolor=0x000000AA" in graph and "fontcolor=white" in graph
    assert extra == []


def test_cover_graph_band_mode_recolours_the_band():
    edl = {"brand_title": {"mode": "band", "color": "#7C5CFF"}}
    graph, extra = _cover_filter_graph("Title", edl)
    assert "boxcolor=0x7C5CFF" in graph
    assert extra == []


def test_cover_graph_accent_mode_recolours_the_text():
    edl = {"brand_title": {"mode": "accent_text", "color": "#38E1FF"}}
    graph, _ = _cover_filter_graph("Title", edl)
    assert "fontcolor=0x38E1FF" in graph


def test_cover_graph_adds_logo_overlay(tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    edl = {
        "brand_title": {"mode": "band", "color": "#7C5CFF"},
        "brand_watermark": {
            "path": str(logo), "position": "bottom_right", "opacity": 1.0,
            "width_frac": 0.16, "margin_frac": 0.03,
        },
    }
    graph, extra = _cover_filter_graph("Title", edl)
    assert extra == ["-loop", "1", "-i", str(logo)]
    assert "[branded]" in graph and "brandlogo" in graph


# ---------------------------------------------------------------------------
# Creation-time fill: kit fills only what the task left unchosen
# ---------------------------------------------------------------------------


class _FakeTaskRepo:
    def __init__(self):
        self.created = None

    async def user_exists(self, db, user_id):
        return True

    async def find_recent_upload_task(self, db, user_id, key):
        return None

    async def create_task(self, db, **kwargs):
        self.created = kwargs
        return "task-1"


class _FakeSourceRepo:
    async def create_source(self, db, **kwargs):
        return "source-1"


class _FakeVideoService:
    def determine_source_type(self, url):
        return "youtube"


def _make_service(kit):
    from src.services.task_service import TaskService

    service = TaskService.__new__(TaskService)
    service.db = None
    service.task_repo = _FakeTaskRepo()
    service.source_repo = _FakeSourceRepo()
    service.video_service = _FakeVideoService()
    service.reused_upload_task = False
    service.reused_upload_job_id = None

    class _Config:
        monetization_enabled = False

    service.config = _Config()

    class _BrandRepo:
        async def get_kit(self, db, user_id, kit_id):
            return kit if kit and kit["id"] == kit_id else None

    service.brand_kit_repo = _BrandRepo()
    return service


async def test_creation_fill_kit_supplies_unset_choices():
    service = _make_service({**KIT, "music_track": "kit-track.wav",
                             "intro_asset": "intro.mp4"})
    task_id = await service.create_task_with_source(
        "user-1", "https://youtube.com/watch?v=abc", title="T",
        source_duration_seconds=60, brand_kit_id="kit-1",
    )
    assert task_id == "task-1"
    created = service.task_repo.created
    assert created["caption_template"] == "hormozi"  # kit template filled
    assert created["music_override"] == "kit-track.wav"
    assert created["brand_kit_id"] == "kit-1"
    assert created["intro_asset"] == "intro.mp4"
    assert created["outro_asset"] is None


async def test_creation_fill_explicit_task_choices_win():
    service = _make_service({**KIT, "music_track": "kit-track.wav"})
    await service.create_task_with_source(
        "user-1", "https://youtube.com/watch?v=abc", title="T",
        caption_template="neon", music_override="mine.wav",
        source_duration_seconds=60, brand_kit_id="kit-1",
    )
    created = service.task_repo.created
    assert created["caption_template"] == "neon"
    assert created["music_override"] == "mine.wav"


async def test_creation_unknown_kit_is_refused():
    service = _make_service(None)
    with pytest.raises(ValueError, match="Brand kit not found"):
        await service.create_task_with_source(
            "user-1", "https://youtube.com/watch?v=abc", title="T",
            source_duration_seconds=60, brand_kit_id="nope",
        )
    assert service.task_repo.created is None
