"""Recipe wiring into task creation (create-path slice).

The foundation slice proved the fragment equals the hand-built
payload for the same choices; this slice proves the create path
actually uses it, through the one create path (``_create_task`` ->
``TaskService.create_task_with_source``):

- merge precedence: an explicit per-choice field wins over the
  recipe; the recipe fills only what the request left unmade,
  including ``font_options`` per sub-key,
- the merged payload passes through the create path's OWN
  validators: a stored value the create path would reject (seeded
  here by writing the fake repository directly, as a stale or
  hand-edited row would hold) is a 422 and creates nothing,
- unknown / malformed ``recipe_id`` is a 422 and creates nothing,
- the created task records the applied recipe id on its source
  metadata (the creator-name provenance pattern),
- a create WITHOUT ``recipe_id`` reaches the service and the worker
  with byte-identical arguments to before this slice.

The route tests follow the test_create_task_source_validation.py
wiring (fake billing / task service / preflight / queue adapter);
the provenance tests follow the test_creator_name_titles.py stubbed
service. No live Postgres, no render.
"""

from __future__ import annotations

import contextlib
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src import recipes as recipe_domain
from src.api.routes import tasks
from src.clip_cleanup import new_task_cleanup_settings
from src.config import get_config

YOUTUBE_URL = "https://www.youtube.com/watch?v=abcdefghijk"


# ---------------------------------------------------------------------------
# merge_recipe_fragment: pure precedence
# ---------------------------------------------------------------------------


def test_merge_explicit_wins_and_recipe_fills_the_rest():
    merged = recipe_domain.merge_recipe_fragment(
        {"music_mode": "auto", "clip_length": "long"},
        {
            "template_mode": "plain",
            "music_mode": "off",
            "clip_length": "short",
            "target_clip_count": 8,
        },
    )
    assert merged == {
        "music_mode": "auto",  # explicit wins
        "clip_length": "long",  # explicit wins
        "template_mode": "plain",  # recipe fills
        "target_clip_count": 8,  # recipe fills
    }


def test_merge_explicit_none_means_no_choice_so_recipe_fills():
    merged = recipe_domain.merge_recipe_fragment(
        {"music_mode": None, "template_mode": None},
        {"music_mode": "off", "template_mode": "plain"},
    )
    assert merged == {"music_mode": "off", "template_mode": "plain"}


def test_merge_explicit_false_and_zero_still_win():
    """Only None means "no choice": an explicit False/0 is a choice
    the recipe must not override."""
    merged = recipe_domain.merge_recipe_fragment(
        {"include_broll": False, "target_clip_count": 0},
        {"include_broll": True, "target_clip_count": 8},
    )
    assert merged == {"include_broll": False, "target_clip_count": 0}


def test_merge_font_options_merge_per_sub_key():
    merged = recipe_domain.merge_recipe_fragment(
        {"font_options": {"font_color": "#FF0000"}},
        {
            "font_options": {
                "font_family": "Montserrat",
                "font_size": 36,
                "font_color": "#00FF00",
            }
        },
    )
    assert merged["font_options"] == {
        "font_family": "Montserrat",  # recipe fills
        "font_size": 36,  # recipe fills
        "font_color": "#FF0000",  # explicit sub-key wins
    }


def test_merge_ignores_keys_no_recipe_may_store():
    merged = recipe_domain.merge_recipe_fragment(
        {},
        {"template_mode": "plain", "source": "x", "campaign_id": "c-1"},
    )
    assert merged == {"template_mode": "plain"}


def test_merge_does_not_mutate_either_input():
    data = {"font_options": {"font_color": "#FF0000"}}
    fragment = {"font_options": {"font_family": "Montserrat"}, "music_mode": "off"}
    merged = recipe_domain.merge_recipe_fragment(data, fragment)
    merged["font_options"]["font_family"] = "Changed"
    merged["music_mode"] = "auto"
    assert data == {"font_options": {"font_color": "#FF0000"}}
    assert fragment == {
        "font_options": {"font_family": "Montserrat"},
        "music_mode": "off",
    }


def test_merge_malformed_explicit_font_options_stay_for_route_to_reject():
    """A non-dict explicit font_options is not papered over by the
    recipe's dict: the merged payload still carries it, so the create
    path's own 400 for it fires."""
    merged = recipe_domain.merge_recipe_fragment(
        {"font_options": "Montserrat"},
        {"font_options": {"font_family": "Montserrat"}},
    )
    assert merged["font_options"] == "Montserrat"


# ---------------------------------------------------------------------------
# Route wiring: fakes (the test_create_task_source_validation.py pattern)
# ---------------------------------------------------------------------------


def _request(body, queue_adapter=None):
    return SimpleNamespace(
        json=AsyncMock(return_value=body),
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(queue_adapter=queue_adapter or _QueueAdapter())
        ),
    )


class _QueueAdapter:
    def __init__(self):
        self.enqueued = []

    async def enqueue_processing_job(self, *args, **kwargs):
        self.enqueued.append((args, kwargs))
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


@contextlib.asynccontextmanager
async def _no_guard(*args, **kwargs):
    yield


def _wire_common(monkeypatch, recipes=None):
    monkeypatch.setattr(
        tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1")
    )
    monkeypatch.setattr(
        "src.source_submission.record_source_submission", AsyncMock()
    )
    monkeypatch.setattr(tasks, "BillingService", _FakeBillingService)
    monkeypatch.setattr(tasks, "TaskService", _FakeTaskService)
    monkeypatch.setattr(tasks, "generation_allowance_guard", _no_guard)
    monkeypatch.setattr(tasks, "_save_task_source_metadata", AsyncMock())
    monkeypatch.setattr(
        tasks,
        "_preflight_youtube_source",
        AsyncMock(return_value={"duration": 600.0, "title": "Source"}),
    )
    monkeypatch.setattr(
        tasks, "RecipeRepository", _FakeRecipeRepository(recipes)
    )
    _FakeTaskService.captured = {}


def _seeded_recipe(recipe_id="r-1", settings=None):
    return {
        recipe_id: {
            "id": recipe_id,
            "user_id": "user-1",
            "name": "Trading Plain",
            "settings": settings
            if settings is not None
            else {
                "template_mode": "plain",
                "music_mode": "off",
                "clip_length": "short",
                "caption_template": "hormozi",
                "font_options": {"font_family": "Montserrat"},
                "output_format": "original",
                "add_subtitles": False,
                "cut_long_pauses": True,
                "pause_threshold_ms": 900,
            },
        }
    }


def _bare_create_kwargs():
    """Exactly what the route passes for a bare YouTube create (no
    recipe): the byte-identical baseline the no-recipe test pins."""
    return {
        "user_id": "user-1",
        "url": YOUTUBE_URL,
        "title": "Source",
        "font_family": None,
        "font_size": None,
        "font_color": None,
        "caption_template": "default",
        "include_broll": False,
        "processing_mode": get_config().default_processing_mode,
        "niche_override": None,
        "music_override": None,
        "music_mode": None,
        "template_mode": None,
        "framing_mode": None,
        "layout_template": None,
        "layout_credit": None,
        "hook_title_enabled": None,
        "broll_enabled": None,
        "separation_opt_in": False,
        "target_clip_count": None,
        "creation_mode": None,
        "manual_ranges": None,
        "source_duration_seconds": 600,
        "upload_request_key": None,
        "content_brief": None,
        "campaign_id": None,
        "brand_kit_id": None,
        "creator_name": None,
        "recipe_id": None,
    }


async def test_create_without_recipe_is_byte_identical(monkeypatch):
    """No recipe_id: the service call and the worker job carry exactly
    what they carried before this slice (plus recipe_id=None)."""
    _wire_common(monkeypatch)
    queue = _QueueAdapter()
    result = await tasks.create_task(
        _request({"source": {"url": YOUTUBE_URL}}, queue_adapter=queue),
        db=None,
    )
    assert result["task_id"] == "task-1"
    assert _FakeTaskService.captured == _bare_create_kwargs()
    assert len(queue.enqueued) == 1
    args, kwargs = queue.enqueued[0]
    assert args[0] == "process_video_task"
    assert args[1] == get_config().default_processing_mode  # processing_mode
    assert args[4] == "youtube"  # source_type
    assert args[11] == "vertical"  # output_format
    assert args[12] is True  # add_subtitles
    assert args[13] == new_task_cleanup_settings({})  # cleanup defaults
    assert args[14] == "auto"  # clip_length
    assert kwargs["_job_id"].startswith("task-1:")


async def test_create_with_recipe_applies_fragment(monkeypatch):
    _wire_common(monkeypatch, _seeded_recipe())
    queue = _QueueAdapter()
    result = await tasks.create_task(
        _request(
            {"source": {"url": YOUTUBE_URL}, "recipe_id": "r-1"},
            queue_adapter=queue,
        ),
        db=None,
    )
    assert result["task_id"] == "task-1"
    captured = _FakeTaskService.captured
    assert captured["recipe_id"] == "r-1"
    assert captured["template_mode"] == "plain"
    assert captured["music_mode"] == "off"
    assert captured["caption_template"] == "hormozi"
    assert captured["font_family"] == "Montserrat"
    # Everything the recipe did not set keeps the bare-create value.
    for key in ("niche_override", "music_override", "brand_kit_id"):
        assert captured[key] == _bare_create_kwargs()[key]
    # The worker job sees the recipe's render-affecting choices too.
    args, _kwargs = queue.enqueued[0]
    assert args[11] == "original"  # output_format
    assert args[12] is False  # add_subtitles
    assert args[13]["cut_long_pauses"] is True
    assert args[13]["pause_threshold_ms"] == 900
    assert args[14] == "short"  # clip_length


async def test_create_with_recipe_explicit_choice_wins(monkeypatch):
    _wire_common(monkeypatch, _seeded_recipe())
    await tasks.create_task(
        _request(
            {
                "source": {"url": YOUTUBE_URL},
                "recipe_id": "r-1",
                "music_mode": "auto",  # recipe says off
                "caption_template": "neon",  # recipe says hormozi
                "font_options": {"font_color": "#FF0000"},  # recipe: family only
            }
        ),
        db=None,
    )
    captured = _FakeTaskService.captured
    assert captured["music_mode"] == "auto"
    assert captured["caption_template"] == "neon"
    assert captured["font_family"] == "Montserrat"  # recipe fills
    assert captured["font_color"] == "#FF0000"  # explicit fills
    # Untouched recipe values still apply.
    assert captured["template_mode"] == "plain"


async def test_create_with_recipe_explicit_template_mode_overrides(monkeypatch):
    """The template-mode combination, both directions: the seeded
    recipe is Plain, so the fragment test above proves a Plain recipe
    creates a Plain task; here the request's explicit ``auto`` must
    win over the recipe's ``plain``."""
    _wire_common(monkeypatch, _seeded_recipe())
    await tasks.create_task(
        _request(
            {
                "source": {"url": YOUTUBE_URL},
                "recipe_id": "r-1",
                "template_mode": "auto",  # recipe says plain
            }
        ),
        db=None,
    )
    captured = _FakeTaskService.captured
    assert captured["template_mode"] == "auto"
    # The rest of the recipe still applies.
    assert captured["music_mode"] == "off"
    assert captured["recipe_id"] == "r-1"


async def test_create_with_unknown_recipe_is_422_and_creates_nothing(monkeypatch):
    _wire_common(monkeypatch, _seeded_recipe())
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            _request(
                {"source": {"url": YOUTUBE_URL}, "recipe_id": "nope"},
            ),
            db=None,
        )
    assert error.value.status_code == 422
    assert error.value.detail == "Recipe not found"
    assert _FakeTaskService.captured == {}


@pytest.mark.parametrize("bad_id", ["", "   ", 7, ["r-1"]])
async def test_create_with_malformed_recipe_id_is_422(monkeypatch, bad_id):
    _wire_common(monkeypatch, _seeded_recipe())
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            _request(
                {"source": {"url": YOUTUBE_URL}, "recipe_id": bad_id},
            ),
            db=None,
        )
    assert error.value.status_code == 422
    assert _FakeTaskService.captured == {}


async def test_create_with_other_users_recipe_is_422(monkeypatch):
    _wire_common(monkeypatch)
    # A recipe stored under another user is not found for this user.
    repo = _FakeRecipeRepository(
        {"r-9": {"id": "r-9", "user_id": "user-2", "name": "Not mine", "settings": {}}}
    )
    monkeypatch.setattr(tasks, "RecipeRepository", repo)
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            _request({"source": {"url": YOUTUBE_URL}, "recipe_id": "r-9"}),
            db=None,
        )
    assert error.value.status_code == 422
    assert _FakeTaskService.captured == {}


@pytest.mark.parametrize(
    "bad_settings",
    [
        {"template_mode": "fancy"},
        {"music_mode": "loud"},
        {"clip_length": "huge"},
        {"output_format": "diagonal-3d"},
        {"creation_mode": "turbo"},
    ],
)
async def test_stored_invalid_recipe_values_cannot_smuggle_through(
    monkeypatch, bad_settings
):
    """A stored row the save-time validator would never have accepted
    (stale row, hand-edited DB) still hits the create path's own
    validators once merged: 422, nothing created."""
    _wire_common(monkeypatch, _seeded_recipe(settings=bad_settings))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            _request({"source": {"url": YOUTUBE_URL}, "recipe_id": "r-1"}),
            db=None,
        )
    assert error.value.status_code == 422
    assert _FakeTaskService.captured == {}


async def test_explicit_invalid_value_is_not_rescued_by_recipe(monkeypatch):
    """Precedence cuts both ways: an explicit bad value fails even
    when the recipe holds a good one for the same key."""
    _wire_common(monkeypatch, _seeded_recipe())
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            _request(
                {
                    "source": {"url": YOUTUBE_URL},
                    "recipe_id": "r-1",
                    "template_mode": "fancy",
                }
            ),
            db=None,
        )
    assert error.value.status_code == 422
    assert _FakeTaskService.captured == {}


# ---------------------------------------------------------------------------
# Provenance: create_task_with_source stamps the recipe id
# (the test_creator_name_titles.py stubbed-service pattern)
# ---------------------------------------------------------------------------


def _stubbed_task_service():
    from src.services.task_service import TaskService

    service = TaskService.__new__(TaskService)
    service.db = object()
    service.reused_upload_task = False
    service.reused_upload_job_id = None
    task_repo = SimpleNamespace(
        user_exists=AsyncMock(return_value=True),
        create_task=AsyncMock(return_value="task-1"),
        save_source_metadata=AsyncMock(),
        find_recent_upload_task=AsyncMock(return_value=None),
    )
    service.task_repo = task_repo
    service.brand_kit_repo = SimpleNamespace(get_kit=AsyncMock())
    service.source_repo = SimpleNamespace(
        create_source=AsyncMock(return_value="src-1")
    )
    service.video_service = SimpleNamespace(
        determine_source_type=lambda url: "youtube"
    )
    service.config = SimpleNamespace(monetization_enabled=False)
    return service, task_repo


async def _create_task(service, **overrides):
    kwargs = {
        "user_id": "user-1",
        "url": YOUTUBE_URL,
        "title": "Source",
        "source_duration_seconds": 600,
    }
    kwargs.update(overrides)
    return await service.create_task_with_source(**kwargs)


async def test_create_task_stamps_recipe_id_on_task_metadata():
    service, task_repo = _stubbed_task_service()
    task_id = await _create_task(service, recipe_id="r-1")
    assert task_id == "task-1"
    task_repo.save_source_metadata.assert_awaited_once()
    args = task_repo.save_source_metadata.await_args.args
    assert args[1] == "task-1"
    assert args[2] == {"recipe_id": "r-1"}


async def test_create_task_without_recipe_stamps_nothing():
    service, task_repo = _stubbed_task_service()
    await _create_task(service)
    task_repo.save_source_metadata.assert_not_awaited()


async def test_create_task_stamps_recipe_id_alongside_creator_name():
    service, task_repo = _stubbed_task_service()
    await _create_task(service, recipe_id="r-1", creator_name="TJR")
    stamps = [
        call.args[2] for call in task_repo.save_source_metadata.await_args_list
    ]
    assert {"recipe_id": "r-1"} in stamps
    assert {"creator_name": "TJR"} in stamps
