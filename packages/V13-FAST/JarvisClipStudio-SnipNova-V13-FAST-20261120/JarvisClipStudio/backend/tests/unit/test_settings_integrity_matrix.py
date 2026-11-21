"""Settings-integrity audit matrix (Day-1 lane, 2026-11-12).

Companion to ``test_task_settings_readback.py``. That suite proved the
four dropped fields (template_mode, music_mode, separation_opt_in,
source_duration_seconds) survive the real repository round-trip after
the fix. This suite covers the rest of the audit matrix on the SAME
real path — repository ``create_task`` -> ``get_task_by_id`` through
``ExecSession``, then the render loop's exact reads into
``VideoService.build_pipeline_edl``:

- every remaining create-written task field survives ``get_task_by_id``
  (the serializer carries the full create surface, not just the four
  fields the fix lane repaired);
- Plain x Music {on, auto} still builds the silent Plain EDL (the
  template contract beats the music control);
- Auto x B-roll {explicit off, NULL, explicit on}: off means zero cues
  AND zero stock-provider calls, NULL means the Auto default (cues
  planned), on means cues planned;
- Plain x B-roll stored True still plans nothing (Plain's identity
  wins over the stored switch);
- an unrecognised STORED value (unreachable through the API, whose
  validators 422 it — only direct DB writes can plant one) is carried
  faithfully by the serializer; the render loop's reads then
  re-validate it with the same try/warn pattern process_task uses for
  target_clip_count (SETTINGS_INTEGRITY_AUDIT.md finding F-2, fixed):
  a warning names the field and the bad value, and the documented
  default (Auto template / Auto music) applies — never a silent
  fallback. The production-path proof lives in test_task_service.py
  and test_clip_rerender.py; this suite pins the round-trip + EDL
  outcome through the same verbatim resolution.
"""

import logging
from pathlib import Path

from sql_exec_support import ExecSession

from src.auto_director.broll_setting import resolve_broll_enabled
from src.auto_director.template_mode import validate_template_mode
from src.media import edl_render, music_library, pixabay_broll
from src.media.music_library import validate_music_mode
from src.repositories.task_repository import TaskRepository
from src.services.video_service import VideoService

# The render loop logs from task_service's module logger; the verbatim
# resolution below warns through the same channel.
_render_loop_logger = logging.getLogger("src.services.task_service")


_TASKS_DDL = """
CREATE TABLE tasks (
    id TEXT, user_id TEXT, source_id TEXT, status TEXT,
    font_family TEXT, font_size INTEGER, font_color TEXT,
    caption_template TEXT, include_broll INTEGER, processing_mode TEXT,
    niche_override TEXT, music_override TEXT, music_mode TEXT,
    template_mode TEXT, framing_mode TEXT, layout_template TEXT, layout_credit TEXT, hook_title_enabled INTEGER, broll_enabled INTEGER, separation_opt_in INTEGER,
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


async def _create(session, **overrides):
    kwargs = dict(
        user_id="user-1",
        source_id="src-1",
        source_duration_seconds=2365,
    )
    kwargs.update(overrides)
    return await TaskRepository.create_task(session, **kwargs)


# ---------------------------------------------------------------------------
# The full create surface survives get_task_by_id
# ---------------------------------------------------------------------------

async def test_full_create_surface_survives_get_task_by_id():
    session = _session()
    task_id = await _create(
        session,
        status="queued",
        font_family="TikTokSans-Regular",
        font_size=28,
        font_color="#FFDD00",
        caption_template="karaoke",
        include_broll=True,
        processing_mode="quality",
        niche_override="travel_vlog",
        music_override="calm.wav",
        music_mode="on",
        template_mode="auto",
        broll_enabled=True,
        separation_opt_in=True,
        target_clip_count=7,
        creation_mode="find_moments",
        manual_ranges_json='[{"start": 1.0, "end": 9.0}]',
        content_brief='{"must_include": ["harbour"]}',
        campaign_id="camp-1",
        brand_kit_id="kit-1",
        intro_asset="intro.mp4",
        outro_asset="outro.mp4",
    )
    task = await TaskRepository.get_task_by_id(session, task_id)
    assert task is not None
    assert task.get("font_family") == "TikTokSans-Regular"
    assert task.get("font_size") == 28
    assert task.get("font_color") == "#FFDD00"
    assert task.get("caption_template") == "karaoke"
    assert task.get("include_broll")
    assert task.get("processing_mode") == "quality"
    assert task.get("niche_override") == "travel_vlog"
    assert task.get("music_override") == "calm.wav"
    assert task.get("music_mode") == "on"
    assert task.get("template_mode") == "auto"
    assert task.get("broll_enabled")
    assert bool(task.get("separation_opt_in")) is True
    assert task.get("target_clip_count") == 7
    assert task.get("creation_mode") == "find_moments"
    assert task.get("manual_ranges_json") == '[{"start": 1.0, "end": 9.0}]'
    assert task.get("campaign_id") == "camp-1"
    assert task.get("brand_kit_id") == "kit-1"
    assert task.get("intro_asset") == "intro.mp4"
    assert task.get("outro_asset") == "outro.mp4"


async def test_get_user_tasks_projection_carries_pipeline_fields():
    """The list view is a projection. Its ONLY consumer is the task
    list route (tasks.py:561); every pipeline reader — both resume
    paths included (tasks.py:2790, task_service.py:642) — loads the
    full dict via get_task_by_id. The projection must still carry the
    four repaired fields (the fix lane added them here too, and the
    list displays mode state), and — pinned deliberately — it does NOT
    carry the styling/brand fields, which nothing reads from this
    dict. See SETTINGS_INTEGRITY_AUDIT.md finding F-3."""
    session = _session()
    task_id = await _create(
        session,
        template_mode="plain",
        niche_override="travel_vlog",
        target_clip_count=3,
        creation_mode="full_auto",
        font_family="TikTokSans-Regular",
        caption_template="karaoke",
        brand_kit_id="kit-1",
    )
    tasks = await TaskRepository.get_user_tasks(session, "user-1")
    mine = [t for t in tasks if t["id"] == task_id]
    assert len(mine) == 1
    task = mine[0]
    assert task.get("template_mode") == "plain"
    assert task.get("niche_override") == "travel_vlog"
    assert task.get("target_clip_count") == 3
    assert task.get("creation_mode") == "full_auto"
    # Pinned projection gap (audit finding F-3, verified consumer-free):
    assert "font_family" not in task
    assert "caption_template" not in task
    assert "brand_kit_id" not in task


# ---------------------------------------------------------------------------
# Pipeline matrix: the render loop's own reads, fed by the round-trip
# ---------------------------------------------------------------------------

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
    return {"start_time": "00:00", "end_time": "01:00",
            "text": "we saw the mountain then the beach and the city"}


def _pipeline_words():
    return [
        {"word": word, "start": float(i), "end": float(i) + 0.5}
        for i, word in enumerate(
            ["we", "saw", "the", "mountain", "then", "the", "beach",
             "and", "the", "city"]
        )
    ]


def _spy_resolve(monkeypatch):
    calls = []

    def _spy(edl):
        calls.append(edl)
        return edl

    monkeypatch.setattr(pixabay_broll, "resolve_edl_broll", _spy)
    return calls


def _render_loop_edl(task_row, monkeypatch, tmp_path):
    """Reproduce task_service's render-loop resolution verbatim
    (task_service.py project_* reads -> create_single_clip kwargs),
    including the F-2 re-validation: stored template/music values go
    through the same validators the API uses, with the process_task
    warn-and-default treatment for anything they reject."""
    _library(tmp_path, monkeypatch)
    calls = _spy_resolve(monkeypatch)
    try:
        project_music_mode = validate_music_mode(task_row.get("music_mode"))
    except ValueError:
        _render_loop_logger.warning(
            "Ignoring invalid stored music_mode %r on task %s",
            task_row.get("music_mode"),
            task_row.get("id"),
        )
        project_music_mode = None
    try:
        project_template_mode = validate_template_mode(
            task_row.get("template_mode")
        )
    except ValueError:
        _render_loop_logger.warning(
            "Ignoring invalid stored template_mode %r on task %s",
            task_row.get("template_mode"),
            task_row.get("id"),
        )
        project_template_mode = None
    project_broll_enabled = task_row.get("broll_enabled")
    edl = VideoService.build_pipeline_edl(
        _segment(), _pipeline_words(),
        niche_override=task_row.get("niche_override") or "travel_vlog",
        duration=60.0,
        music_override=task_row.get("music_override"),
        music_mode=project_music_mode,
        template_mode=project_template_mode,
        broll_enabled=resolve_broll_enabled(
            project_template_mode, project_broll_enabled
        ),
    )
    return edl, calls


def _assert_silent(edl):
    assert edl["audio"].get("music_track") == ""
    assert edl["audio"].get("music_source") == "user_none"
    assert edl["audio"].get("music_mood") == "none"
    assert edl["audio"].get("music_volume") == 0.0


async def test_plain_with_music_on_still_builds_silent_plain_edl(
    tmp_path, monkeypatch
):
    """Plain's contract beats the music control: even Music On stored
    on a Plain task yields no bed (template_mode module docstring)."""
    session = _session()
    task_id = await _create(
        session, template_mode="plain", music_mode="on",
        broll_enabled=True,
    )
    task_row = await TaskRepository.get_task_by_id(session, task_id)
    edl, calls = _render_loop_edl(task_row, monkeypatch, tmp_path)
    assert edl.get("template") == "plain"
    _assert_silent(edl)
    # Stored broll_enabled=True cannot switch Plain's B-roll on either.
    assert edl["broll"] == []
    assert calls == []


async def test_plain_with_music_auto_still_builds_silent_plain_edl(
    tmp_path, monkeypatch
):
    session = _session()
    task_id = await _create(
        session, template_mode="plain", music_mode=None,
        broll_enabled=None,
    )
    task_row = await TaskRepository.get_task_by_id(session, task_id)
    edl, calls = _render_loop_edl(task_row, monkeypatch, tmp_path)
    assert edl.get("template") == "plain"
    _assert_silent(edl)
    assert edl["broll"] == []
    assert calls == []


async def test_auto_with_music_on_builds_audible_edl(tmp_path, monkeypatch):
    session = _session()
    task_id = await _create(
        session, template_mode="auto", music_mode="on",
        broll_enabled=False,
    )
    task_row = await TaskRepository.get_task_by_id(session, task_id)
    edl, _calls = _render_loop_edl(task_row, monkeypatch, tmp_path)
    assert edl.get("template") != "plain"
    assert edl["audio"].get("music_source") != "user_none"
    assert edl["audio"].get("music_volume", 0) > 0


async def test_auto_broll_explicit_off_plans_no_cues_and_no_stock_calls(
    tmp_path, monkeypatch
):
    session = _session()
    task_id = await _create(
        session, template_mode="auto", music_mode="auto",
        broll_enabled=False,
    )
    task_row = await TaskRepository.get_task_by_id(session, task_id)
    edl, calls = _render_loop_edl(task_row, monkeypatch, tmp_path)
    assert edl["broll"] == []
    assert calls == [], "B-roll Off must never reach the stock resolver"


async def test_auto_broll_null_uses_template_default_on(
    tmp_path, monkeypatch
):
    session = _session()
    task_id = await _create(
        session, template_mode="auto", music_mode="auto",
        broll_enabled=None,
    )
    task_row = await TaskRepository.get_task_by_id(session, task_id)
    edl, calls = _render_loop_edl(task_row, monkeypatch, tmp_path)
    assert edl["broll"] != []
    assert len(calls) == 1


async def test_auto_broll_explicit_on_plans_cues(tmp_path, monkeypatch):
    session = _session()
    task_id = await _create(
        session, template_mode="auto", music_mode="auto",
        broll_enabled=True,
    )
    task_row = await TaskRepository.get_task_by_id(session, task_id)
    edl, calls = _render_loop_edl(task_row, monkeypatch, tmp_path)
    assert edl["broll"] != []
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Unrecognised stored values (finding F-2 — fixed: warn + documented
# default, never a silent fallback)
# ---------------------------------------------------------------------------

async def test_unrecognised_stored_values_resolve_as_auto(
    tmp_path, monkeypatch, caplog
):
    """Only a direct DB write can plant these (the API validators 422
    them). The serializer carries them faithfully; the render loop's
    reads then reject them loudly — a warning names each field and the
    bad value — and the documented defaults apply (unknown template is
    not Plain, unknown music mode lands in the music chain's Auto
    branch). See SETTINGS_INTEGRITY_AUDIT.md finding F-2."""
    session = _session()
    task_id = await _create(
        session, template_mode="auto", music_mode="auto",
        broll_enabled=None,
    )
    session.conn.execute(
        "UPDATE tasks SET template_mode = 'bogus', music_mode = 'bogus' "
        "WHERE id = ?",
        (task_id,),
    )
    session.conn.commit()
    task_row = await TaskRepository.get_task_by_id(session, task_id)
    assert task_row.get("template_mode") == "bogus"
    assert task_row.get("music_mode") == "bogus"

    with caplog.at_level(logging.WARNING, logger="src.services.task_service"):
        edl, _calls = _render_loop_edl(task_row, monkeypatch, tmp_path)
    assert edl.get("template") != "plain"
    assert edl["audio"].get("music_source") != "user_none"
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "Ignoring invalid stored template_mode 'bogus'" in m for m in messages
    )
    assert any(
        "Ignoring invalid stored music_mode 'bogus'" in m for m in messages
    )
