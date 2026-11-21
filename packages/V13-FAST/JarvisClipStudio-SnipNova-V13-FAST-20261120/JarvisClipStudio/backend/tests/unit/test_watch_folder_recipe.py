"""Watch-folder autopilot recipes (autopilot slice).

The create-path slice proved a recipe fills a task's choices; this
slice proves the watch-folder autopilot does the same for dropped
files, through the one merge (``merge_recipe_fragment``) and the
create path's own validators:

- a recipe set on the autopilot applies to created tasks (task fields,
  worker job and metadata), and the task records the recipe id,
- an explicit autopilot setting wins over the recipe; an unset one
  lets the recipe fill it,
- an unknown / cross-user / malformed recipe id, or a recipe deleted
  after the settings were saved, creates no task and is logged loudly
  (settings-save time refuses it first; the file handler refuses it
  again),
- with no recipe the autopilot passes byte-identical arguments to
  before this slice (the existing autopilot tests pass unmodified).

The pure-argument tests pin the merge; the production-creator tests
stub the billing / probe / task-service / queue / metadata calls the
same way the create-wiring tests stub the route. No live Postgres, no
render.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import watch_folder as routes
from src.clip_cleanup import new_task_cleanup_settings
from src.config import get_config
from src.services import watch_folder_service as service_module
from src.services.watch_folder_service import (
    WatchFolderService,
    autopilot_task_arguments,
    load_autopilot_recipe,
)
from tests.unit.test_watch_folder import FakeRepository, _Request, _config, _settings


def _seeded_recipe(recipe_id="r-1", settings=None, user_id="user-1"):
    return {
        recipe_id: {
            "id": recipe_id,
            "user_id": user_id,
            "name": "Trading Plain",
            "settings": settings
            if settings is not None
            else {
                "template_mode": "plain",
                "niche_override": "travel_vlog",
                "music_mode": "off",
                "clip_length": "short",
                "target_clip_count": 8,
                "caption_template": "hormozi",
                "font_options": {"font_family": "Montserrat"},
                "output_format": "original",
                "add_subtitles": False,
                "cut_long_pauses": True,
                "pause_threshold_ms": 900,
            },
        }
    }


class _FakeRecipeRepository:
    """In-memory recipe store; settings are returned exactly as
    seeded, so a test can plant a stored row the save-time validator
    would never have accepted."""

    def __init__(self, recipes=None):
        self.recipes = dict(recipes or {})

    async def get_recipe(self, db, user_id, recipe_id):
        recipe = self.recipes.get(recipe_id)
        if recipe is None or recipe["user_id"] != user_id:
            return None
        return copy.deepcopy(recipe)


def _bare_arguments(config):
    """Exactly what the autopilot passed for a dropped file before
    this slice: the byte-identical baseline the no-recipe tests pin."""
    return {
        "create_kwargs": {
            "caption_template": "default",
            "processing_mode": config.default_processing_mode,
            "niche_override": None,
            "music_mode": None,
            "music_override": None,
            "target_clip_count": None,
            "font_family": None,
            "font_size": None,
            "font_color": None,
            "include_broll": False,
            "template_mode": None,
            "broll_enabled": None,
            "separation_opt_in": False,
            "creation_mode": None,
            "brand_kit_id": None,
            "recipe_id": None,
        },
        "clip_length": "auto",
        "output_format": "vertical",
        "add_subtitles": True,
        "cleanup_settings": new_task_cleanup_settings({}),
        "recipe_id": None,
    }


# ---------------------------------------------------------------------------
# Pure merge: autopilot_task_arguments
# ---------------------------------------------------------------------------


def test_no_recipe_arguments_are_byte_identical(tmp_path):
    """No recipe: the merged arguments are exactly what the autopilot
    passed before this slice."""
    config = _config(tmp_path)
    settings = _settings(tmp_path / "inbox")
    assert autopilot_task_arguments(settings, None, {}, config) == (
        _bare_arguments(config)
    )


def test_no_recipe_invalid_processing_mode_still_falls_back(tmp_path):
    """The create path's lenient processing-mode rule survives: an
    unknown mode falls back to the default, recipe or not."""
    config = _config(tmp_path)
    settings = _settings(tmp_path / "inbox", processing_mode="ludicrous")
    arguments = autopilot_task_arguments(settings, None, {}, config)
    assert arguments["create_kwargs"]["processing_mode"] == "balanced"


def test_music_mode_is_normalised_like_the_autopilot_always_did(tmp_path):
    config = _config(tmp_path)
    settings = _settings(tmp_path / "inbox", music_mode=" OFF ")
    arguments = autopilot_task_arguments(settings, None, {}, config)
    assert arguments["create_kwargs"]["music_mode"] == "off"


def test_recipe_applies_fragment(tmp_path):
    config = _config(tmp_path)
    settings = _settings(tmp_path / "inbox", recipe_id="r-1")
    fragment = _seeded_recipe()["r-1"]["settings"]
    arguments = autopilot_task_arguments(settings, "r-1", fragment, config)

    kwargs = arguments["create_kwargs"]
    assert kwargs["recipe_id"] == "r-1"
    assert kwargs["template_mode"] == "plain"
    assert kwargs["niche_override"] == "travel_vlog"
    assert kwargs["music_mode"] == "off"
    assert kwargs["caption_template"] == "hormozi"
    assert kwargs["font_family"] == "Montserrat"
    assert kwargs["target_clip_count"] == 8
    # Render-affecting choices travel with the task, as on the create
    # path: the worker job and the metadata carry them.
    assert arguments["clip_length"] == "short"
    assert arguments["output_format"] == "original"
    assert arguments["add_subtitles"] is False
    assert arguments["cleanup_settings"]["cut_long_pauses"] is True
    assert arguments["cleanup_settings"]["pause_threshold_ms"] == 900


def test_explicit_autopilot_field_wins_over_recipe(tmp_path):
    config = _config(tmp_path)
    settings = _settings(
        tmp_path / "inbox",
        recipe_id="r-1",
        niche_override="finance_business",  # recipe says travel_vlog
        music_mode="auto",  # recipe says off
        target_clip_count=5,  # recipe says 8
    )
    fragment = _seeded_recipe()["r-1"]["settings"]
    arguments = autopilot_task_arguments(settings, "r-1", fragment, config)

    kwargs = arguments["create_kwargs"]
    assert kwargs["niche_override"] == "finance_business"
    assert kwargs["music_mode"] == "auto"
    assert kwargs["target_clip_count"] == 5
    # Untouched recipe values still apply.
    assert kwargs["template_mode"] == "plain"
    assert kwargs["caption_template"] == "hormozi"


def test_explicit_none_lets_recipe_fill(tmp_path):
    """None means "no choice" on the autopilot side too: an unset
    setting is filled by the recipe, never treated as a choice."""
    config = _config(tmp_path)
    settings = _settings(tmp_path / "inbox", recipe_id="r-1")
    fragment = _seeded_recipe()["r-1"]["settings"]
    arguments = autopilot_task_arguments(settings, "r-1", fragment, config)

    kwargs = arguments["create_kwargs"]
    assert kwargs["music_mode"] == "off"
    assert kwargs["target_clip_count"] == 8


def test_recipe_processing_mode_fills_when_settings_unset(tmp_path):
    config = _config(tmp_path)
    fragment = {"processing_mode": "fast"}
    settings = _settings(tmp_path / "inbox", recipe_id="r-1")
    arguments = autopilot_task_arguments(settings, "r-1", fragment, config)
    assert arguments["create_kwargs"]["processing_mode"] == "fast"

    settings = _settings(
        tmp_path / "inbox", recipe_id="r-1", processing_mode="quality"
    )
    arguments = autopilot_task_arguments(settings, "r-1", fragment, config)
    assert arguments["create_kwargs"]["processing_mode"] == "quality"


@pytest.mark.parametrize(
    "bad_fragment",
    [
        {"template_mode": "fancy"},
        {"music_mode": "loud"},
        {"clip_length": "huge"},
        {"output_format": "diagonal-3d"},
        {"creation_mode": "turbo"},
        {"creation_mode": "manual_cutter_ai"},
        {"add_subtitles": "yes"},
    ],
)
def test_invalid_merged_value_is_rejected(tmp_path, bad_fragment):
    """A merged value the create path would reject refuses the file:
    no arguments are produced at all."""
    config = _config(tmp_path)
    settings = _settings(tmp_path / "inbox", recipe_id="r-1")
    with pytest.raises(ValueError):
        autopilot_task_arguments(settings, "r-1", bad_fragment, config)


def test_explicit_invalid_value_is_not_rescued_by_recipe(tmp_path):
    """Precedence cuts both ways: an explicit bad value fails even
    when the recipe holds a good one for the same key."""
    config = _config(tmp_path)
    settings = _settings(
        tmp_path / "inbox", recipe_id="r-1", niche_override="not-a-niche"
    )
    fragment = _seeded_recipe()["r-1"]["settings"]
    with pytest.raises(ValueError):
        autopilot_task_arguments(settings, "r-1", fragment, config)


# ---------------------------------------------------------------------------
# Recipe load: load_autopilot_recipe
# ---------------------------------------------------------------------------


async def test_load_without_recipe_id_returns_empty(monkeypatch):
    monkeypatch.setattr(
        service_module, "RecipeRepository", _FakeRecipeRepository()
    )
    assert await load_autopilot_recipe(None, {"user_id": "user-1"}) == (
        None,
        {},
    )


async def test_load_known_recipe_returns_validated_fragment(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "RecipeRepository",
        _FakeRecipeRepository(_seeded_recipe()),
    )
    recipe_id, fragment = await load_autopilot_recipe(
        None, {"user_id": "user-1", "recipe_id": "r-1"}
    )
    assert recipe_id == "r-1"
    assert fragment == _seeded_recipe()["r-1"]["settings"]


async def test_load_unknown_recipe_raises(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "RecipeRepository",
        _FakeRecipeRepository(_seeded_recipe()),
    )
    with pytest.raises(ValueError, match="Recipe not found"):
        await load_autopilot_recipe(
            None, {"user_id": "user-1", "recipe_id": "nope"}
        )


async def test_load_other_users_recipe_raises(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "RecipeRepository",
        _FakeRecipeRepository(
            {"r-9": {"id": "r-9", "user_id": "user-2", "name": "Not mine",
                     "settings": {}}}
        ),
    )
    with pytest.raises(ValueError, match="Recipe not found"):
        await load_autopilot_recipe(
            None, {"user_id": "user-1", "recipe_id": "r-9"}
        )


@pytest.mark.parametrize("bad_id", ["", "   ", 7, ["r-1"]])
async def test_load_malformed_recipe_id_raises(monkeypatch, bad_id):
    monkeypatch.setattr(
        service_module,
        "RecipeRepository",
        _FakeRecipeRepository(_seeded_recipe()),
    )
    with pytest.raises(ValueError, match="Invalid recipe_id"):
        await load_autopilot_recipe(
            None, {"user_id": "user-1", "recipe_id": bad_id}
        )


async def test_load_stored_invalid_recipe_values_raise(monkeypatch):
    """A stored row the save-time validator would never have accepted
    is refused on load, before any file is ingested."""
    monkeypatch.setattr(
        service_module,
        "RecipeRepository",
        _FakeRecipeRepository(
            _seeded_recipe(settings={"template_mode": "fancy"})
        ),
    )
    with pytest.raises(ValueError):
        await load_autopilot_recipe(
            None, {"user_id": "user-1", "recipe_id": "r-1"}
        )


# ---------------------------------------------------------------------------
# Production creator: create_task_from_watch_file with stubbed deps
# ---------------------------------------------------------------------------


class _FakeBillingService:
    def __init__(self, db):
        pass

    async def assert_can_create_task(self, *args, **kwargs):
        return None


class _FakeTaskService:
    captured: dict = {}

    def __init__(self, db):
        self.task_repo = SimpleNamespace(update_task_status=None)

    async def create_task_with_source(self, **kwargs):
        _FakeTaskService.captured = kwargs
        return "task-1"


class _FakeVideoService:
    @staticmethod
    def probe_upload_duration(video_path, user_id):
        return 120.0


class _FakeQueue:
    enqueued: list = []

    @staticmethod
    async def enqueue_processing_job(*args, **kwargs):
        _FakeQueue.enqueued.append((args, kwargs))
        return "job-1"


class _FakeTaskRepository:
    saved_metadata: list = []

    @staticmethod
    async def save_source_metadata(db, task_id, payload):
        _FakeTaskRepository.saved_metadata.append((task_id, payload))


def _wire_production_creator(monkeypatch, tmp_path, recipes=None):
    """Stub everything create_task_from_watch_file touches except the
    merge itself: billing, probing, task service, queue, metadata and
    the upload placement."""
    monkeypatch.setattr(
        "src.services.billing_service.BillingService", _FakeBillingService
    )
    monkeypatch.setattr(
        "src.services.task_service.TaskService", _FakeTaskService
    )
    monkeypatch.setattr(
        "src.services.video_service.VideoService", _FakeVideoService
    )
    monkeypatch.setattr("src.workers.job_queue.JobQueue", _FakeQueue)
    monkeypatch.setattr(
        "src.repositories.task_repository.TaskRepository",
        _FakeTaskRepository,
    )
    monkeypatch.setattr(
        "src.media.upload_ingest.local_upload_path",
        lambda video_path: tmp_path / "placed.mp4",
    )
    monkeypatch.setattr(
        service_module,
        "_place_upload",
        lambda source, dest: dest.write_bytes(b"video-bytes"),
    )
    monkeypatch.setattr(
        service_module,
        "RecipeRepository",
        _FakeRecipeRepository(recipes),
    )
    _FakeTaskService.captured = {}
    _FakeQueue.enqueued = []
    _FakeTaskRepository.saved_metadata = []


async def test_creator_applies_recipe_to_dropped_file(
    monkeypatch, tmp_path
):
    _wire_production_creator(
        monkeypatch, tmp_path, _seeded_recipe()
    )
    repo = FakeRepository()
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video-bytes")
    settings = _settings(
        tmp_path / "inbox", recipe_id="r-1", niche_override="finance_business"
    )

    task_id = await service_module.create_task_from_watch_file(
        None, settings, source, 2048, repository=repo
    )

    assert task_id == "task-1"
    captured = _FakeTaskService.captured
    assert captured["recipe_id"] == "r-1"
    assert captured["template_mode"] == "plain"
    assert captured["niche_override"] == "finance_business"  # explicit wins
    assert captured["music_mode"] == "off"
    assert captured["caption_template"] == "hormozi"
    assert captured["font_family"] == "Montserrat"
    assert captured["target_clip_count"] == 8
    assert captured["processing_mode"] == get_config().default_processing_mode
    assert captured["source_duration_seconds"] == 120
    assert captured["url"].startswith("upload://autopilot-")
    assert len(repo.uploads) == 1

    # The task metadata and the worker job carry the recipe's
    # render-affecting choices, as on the create path.
    assert len(_FakeTaskRepository.saved_metadata) == 1
    _task_id, metadata = _FakeTaskRepository.saved_metadata[0]
    assert metadata["clip_length"] == "short"
    assert metadata["output_format"] == "original"
    assert metadata["add_subtitles"] is False
    assert metadata["cut_long_pauses"] is True
    assert metadata["pause_threshold_ms"] == 900
    assert metadata["watch_folder_file"] == "clip.mp4"

    assert len(_FakeQueue.enqueued) == 1
    args, kwargs = _FakeQueue.enqueued[0]
    assert args[0] == "process_video_task"
    assert args[3] == captured["url"]
    assert args[6] == "Montserrat"  # font_family
    assert args[9] == "hormozi"  # caption_template
    assert args[11] == "original"  # output_format
    assert args[12] is False  # add_subtitles
    assert args[13]["cut_long_pauses"] is True
    assert args[14] == "short"  # clip_length
    assert kwargs["_job_id"].startswith("task-1:")


async def test_creator_without_recipe_is_byte_identical(
    monkeypatch, tmp_path
):
    """No recipe: the service call, the metadata and the worker job
    carry exactly what they carried before this slice."""
    _wire_production_creator(monkeypatch, tmp_path)
    repo = FakeRepository()
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video-bytes")
    settings = _settings(tmp_path / "inbox")

    task_id = await service_module.create_task_from_watch_file(
        None, settings, source, 2048, repository=repo
    )

    assert task_id == "task-1"
    captured = _FakeTaskService.captured
    video_path = captured.pop("url")
    assert video_path.startswith("upload://autopilot-")
    assert captured == {
        "user_id": "user-1",
        "title": "clip",
        "source_duration_seconds": 120,
        **_bare_arguments(get_config())["create_kwargs"],
    }

    _task_id, metadata = _FakeTaskRepository.saved_metadata[0]
    job_id = metadata.pop("processing_job_id")
    assert job_id.startswith("task-1:")
    assert metadata == {
        "clip_length": "auto",
        "url": video_path,
        "source_type": "video_url",
        "output_format": "vertical",
        "add_subtitles": True,
        "watch_folder_file": "clip.mp4",
        **new_task_cleanup_settings({}),
    }

    args, kwargs = _FakeQueue.enqueued[0]
    assert args == (
        "process_video_task",
        get_config().default_processing_mode,
        "task-1",
        video_path,
        "video_url",
        "user-1",
        None,
        None,
        None,
        "default",
        get_config().default_processing_mode,
        "vertical",
        True,
        new_task_cleanup_settings({}),
        "auto",
    )
    assert kwargs["_job_id"] == job_id


async def test_creator_with_unknown_recipe_creates_nothing_and_logs(
    monkeypatch, tmp_path, caplog
):
    """A recipe deleted after the settings were saved: the file is
    refused loudly, no task is created, nothing is ingested."""
    _wire_production_creator(
        monkeypatch, tmp_path, _seeded_recipe()
    )
    repo = FakeRepository()
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video-bytes")
    settings = _settings(tmp_path / "inbox", recipe_id="nope")

    with pytest.raises(ValueError, match="Recipe not found"):
        await service_module.create_task_from_watch_file(
            None, settings, source, 2048, repository=repo
        )

    assert _FakeTaskService.captured == {}
    assert _FakeQueue.enqueued == []
    assert _FakeTaskRepository.saved_metadata == []
    assert repo.uploads == []  # refused before any ingest
    assert "Recipe not found" in caplog.text


async def test_scan_with_deleted_recipe_fails_the_claim_without_a_task(
    monkeypatch, tmp_path
):
    """End to end through the scanner: the refused file lands on the
    normal failed path (retried, then parked) with the reason
    recorded, and no task ever exists for it."""
    _wire_production_creator(
        monkeypatch, tmp_path, _seeded_recipe()
    )
    folder = tmp_path / "inbox"
    folder.mkdir()
    video = folder / "clip.mp4"
    video.write_bytes(b"v" * 500)
    repo = FakeRepository()
    service = WatchFolderService(
        repo,
        service_module.create_task_from_watch_file,
        config=_config(tmp_path),
    )
    settings = _settings(folder, recipe_id="nope")

    await service.scan_once(None, settings)  # stability sighting only
    summary = await service.scan_once(None, settings)

    assert summary["failed"] == 1
    assert _FakeTaskService.captured == {}
    row = next(iter(repo.files.values()))
    assert row["state"] == "failed" and row["task_id"] is None
    assert "Recipe not found" in row["last_error"]


# ---------------------------------------------------------------------------
# Settings route: PUT /watch-folder/settings with recipe_id
# ---------------------------------------------------------------------------


@pytest.fixture
def route_env(tmp_path, monkeypatch):
    repo = FakeRepository()
    monkeypatch.setattr(routes, "repository", repo)
    monkeypatch.setattr(routes, "get_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        routes, "RecipeRepository", _FakeRecipeRepository(_seeded_recipe())
    )

    async def fake_user_id(request, db, config):
        return "user-1"

    monkeypatch.setattr(routes, "resolve_authenticated_user_id", fake_user_id)
    return repo


async def test_settings_route_saves_and_returns_recipe_id(route_env):
    saved = await routes.update_watch_folder_settings(
        _Request({"enabled": True, "recipe_id": "r-1"}),
        None,
    )
    assert saved["settings"]["recipe_id"] == "r-1"
    assert route_env.settings["user-1"]["recipe_id"] == "r-1"

    result = await routes.get_watch_folder(_Request(), None)
    assert result["settings"]["recipe_id"] == "r-1"


async def test_settings_route_clears_recipe_id_when_omitted(route_env):
    await routes.update_watch_folder_settings(
        _Request({"enabled": True, "recipe_id": "r-1"}),
        None,
    )
    saved = await routes.update_watch_folder_settings(
        _Request({"enabled": True}),
        None,
    )
    assert saved["settings"]["recipe_id"] is None
    assert route_env.settings["user-1"]["recipe_id"] is None


async def test_settings_route_rejects_unknown_recipe(route_env):
    with pytest.raises(HTTPException) as error:
        await routes.update_watch_folder_settings(
            _Request({"enabled": True, "recipe_id": "nope"}),
            None,
        )
    assert error.value.status_code == 422
    assert error.value.detail == "Recipe not found"
    assert route_env.settings == {}  # nothing was persisted


async def test_settings_route_rejects_other_users_recipe(
    route_env, monkeypatch
):
    monkeypatch.setattr(
        routes,
        "RecipeRepository",
        _FakeRecipeRepository(
            {"r-9": {"id": "r-9", "user_id": "user-2", "name": "Not mine",
                     "settings": {}}}
        ),
    )
    with pytest.raises(HTTPException) as error:
        await routes.update_watch_folder_settings(
            _Request({"enabled": True, "recipe_id": "r-9"}),
            None,
        )
    assert error.value.status_code == 422
    assert route_env.settings == {}


@pytest.mark.parametrize("bad_id", ["", "   ", 7, ["r-1"]])
async def test_settings_route_rejects_malformed_recipe_id(
    route_env, bad_id
):
    with pytest.raises(HTTPException) as error:
        await routes.update_watch_folder_settings(
            _Request({"enabled": True, "recipe_id": bad_id}),
            None,
        )
    assert error.value.status_code == 422
    assert route_env.settings == {}
