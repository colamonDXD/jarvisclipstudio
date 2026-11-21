"""Recipes (foundation slice): validation, fragment equivalence,
migration-on-pristine and CRUD at every level that does not need a
live Postgres or a render.

The contract being pinned:

- a recipe stores ONLY keys the task create path already accepts,
  each validated by the create path's own validators (unknown keys —
  including per-video keys like ``source`` and per-campaign keys like
  ``campaign_id`` — are rejected, never stored),
- the apply fragment equals the hand-built create payload for the
  same choices (normalisation included),
- the migration applies on a pristine database through the app's own
  statement-splitting rule, and the repository's real SQL round-trips
  a recipe through it (the sql_exec_support pattern),
- the API routes persist nothing for invalid input and scope every
  read to the owning user (the test_brand_kit.py fake-repo pattern).

The live-Postgres tier remains the gate's SQL-exec family; nothing
here claims it.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from fastapi import HTTPException

from sql_exec_support import ExecSession, to_sqlite

from src import recipes as recipe_domain
from src.api.routes import recipes as routes
from src.media import music_library
from src.repositories.recipe_repository import RecipeRepository

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "src" / "migrations" / "sql" / "20261115_0001_recipes.sql"
)


@pytest.fixture
def music_track(monkeypatch):
    """One track in the live library, so music_override can validate."""
    monkeypatch.setattr(
        music_library,
        "list_music_tracks",
        lambda probe_durations=False: [{"filename": "lofi-beat.mp3"}],
    )


# ---------------------------------------------------------------------------
# Validation: names and payload shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   ", None, 42, "x" * 121])
def test_validate_recipe_name_rejects(bad):
    with pytest.raises(ValueError):
        recipe_domain.validate_recipe_name(bad)


def test_validate_recipe_name_strips():
    assert recipe_domain.validate_recipe_name("  My Recipe ") == "My Recipe"


def test_payload_rejects_unknown_top_level_field():
    with pytest.raises(ValueError):
        recipe_domain.validate_recipe_payload(
            {"name": "x", "surprise": 1}
        )


def test_payload_create_defaults_empty_settings():
    fields = recipe_domain.validate_recipe_payload({"name": "Bare"})
    assert fields == {"name": "Bare", "settings": {}}


def test_payload_update_keeps_only_present_fields():
    fields = recipe_domain.validate_recipe_payload(
        {"settings": {"template_mode": "plain"}}, partial=True
    )
    assert fields == {"settings": {"template_mode": "plain"}}


# ---------------------------------------------------------------------------
# Validation: settings keys — the allowlist is the create path's key set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "excluded",
    ["source", "manual_ranges", "content_brief", "campaign_id", "bogus_key"],
)
def test_settings_reject_keys_the_create_path_does_not_accept(excluded):
    with pytest.raises(ValueError):
        recipe_domain.validate_recipe_settings({excluded: "anything"})


@pytest.mark.parametrize(
    "settings",
    [
        {"template_mode": "fancy"},
        {"niche_override": "not-a-niche"},
        {"music_mode": "loud"},
        {"creation_mode": "turbo"},
        {"clip_length": "huge"},
        {"target_clip_count": 0},
        {"target_clip_count": True},
        {"target_clip_count": "8"},
        {"output_format": "diagonal-3d"},
        {"add_subtitles": "yes"},
        {"processing_mode": "ludicrous"},
        {"caption_template": "no-such-template"},
        {"brand_kit_id": "   "},
        {"brand_kit_id": 7},
        {"include_broll": "yes"},
        {"separation_opt_in": "yes"},
        {"broll_enabled": "perhaps"},
        {"font_options": "Montserrat"},
        {"font_options": {"font_weight": "bold"}},
        {"font_options": {"font_family": "  "}},
        {"font_options": {"font_size": "big"}},
        {"font_options": {"font_size": True}},
        {"font_options": {"font_color": "red"}},
        {"font_options": {"font_color": "#FFF"}},
        {"cut_long_pauses": "yes"},
        {"remove_filler_words": 1},
        {"pause_threshold_ms": "900"},
        {"filtered_words": "um, uh"},
        {"filtered_words": ["um", 3]},
    ],
)
def test_settings_reject_invalid_values(settings):
    with pytest.raises(ValueError):
        recipe_domain.validate_recipe_settings(settings)


def test_settings_reject_music_override_outside_library(music_track):
    with pytest.raises(ValueError):
        recipe_domain.validate_recipe_settings(
            {"music_override": "not-in-the-library.mp3"}
        )


def test_settings_must_be_an_object():
    with pytest.raises(ValueError):
        recipe_domain.validate_recipe_settings(["template_mode"])


def test_settings_none_values_record_no_choice():
    assert recipe_domain.validate_recipe_settings(
        {"template_mode": None, "music_mode": None, "font_options": None}
    ) == {}


def test_settings_normalisation_matches_create_validators():
    cleaned = recipe_domain.validate_recipe_settings(
        {
            "template_mode": "PLAIN",
            "music_mode": "OFF",
            "broll_enabled": "off",
            "niche_override": "  finance_business ",
            "brand_kit_id": " kit-9 ",
            "font_options": {"font_color": "#ffcc00"},
        }
    )
    assert cleaned == {
        "template_mode": "plain",
        "music_mode": "off",
        "broll_enabled": False,
        "niche_override": "finance_business",
        "brand_kit_id": "kit-9",
        "font_options": {"font_color": "#FFCC00"},
    }


# ---------------------------------------------------------------------------
# Apply fragment: equivalence with the hand-built create payload
# ---------------------------------------------------------------------------

FULL_HOUSE_RAW = {
    "template_mode": "PLAIN",
    "niche_override": "finance_business",
    "music_mode": "OFF",
    "music_override": "lofi-beat.mp3",
    "broll_enabled": "off",
    "include_broll": True,
    "separation_opt_in": False,
    "creation_mode": "full_auto",
    "clip_length": "short",
    "target_clip_count": 8,
    "output_format": "vertical",
    "add_subtitles": True,
    "processing_mode": "balanced",
    "caption_template": "hormozi",
    "font_options": {
        "font_family": " Montserrat ",
        "font_size": 36,
        "font_color": "#ffcc00",
    },
    "brand_kit_id": "kit-9",
    "cut_long_pauses": True,
    "pause_threshold_ms": 900,
    "remove_filler_words": True,
    "filtered_words": ["um", "uh"],
}

# What the create path itself would make of those same hand-picked
# values: every validator's normalisation applied, nothing else.
FULL_HOUSE_FRAGMENT = {
    "template_mode": "plain",
    "niche_override": "finance_business",
    "music_mode": "off",
    "music_override": "lofi-beat.mp3",
    "broll_enabled": False,
    "include_broll": True,
    "separation_opt_in": False,
    "creation_mode": "full_auto",
    "clip_length": "short",
    "target_clip_count": 8,
    "output_format": "vertical",
    "add_subtitles": True,
    "processing_mode": "balanced",
    "caption_template": "hormozi",
    "font_options": {
        "font_family": "Montserrat",
        "font_size": 36,
        "font_color": "#FFCC00",
    },
    "brand_kit_id": "kit-9",
    "cut_long_pauses": True,
    "pause_threshold_ms": 900,
    "remove_filler_words": True,
    "filtered_words": ["um", "uh"],
}


def test_fragment_equals_hand_built_payload(music_track):
    recipe = {
        "id": "r-1",
        "user_id": "user-1",
        "name": "Full house",
        "settings": recipe_domain.validate_recipe_settings(FULL_HOUSE_RAW),
    }
    fragment = recipe_domain.create_fragment(recipe)
    assert fragment == FULL_HOUSE_FRAGMENT
    assert set(fragment) <= set(recipe_domain.RECIPE_SETTING_KEYS)


def test_fragment_values_pass_create_validation_unchanged(music_track):
    """Idempotence: re-validating a stored bundle changes nothing, so
    the fragment a recipe contributes is stable under the create
    path's own validation pass."""
    once = recipe_domain.validate_recipe_settings(FULL_HOUSE_RAW)
    assert recipe_domain.validate_recipe_settings(once) == once


def test_fragment_is_a_detached_copy():
    recipe = {"settings": {"font_options": {"font_size": 36}}}
    fragment = recipe_domain.create_fragment(recipe)
    fragment["font_options"]["font_size"] = 99
    assert recipe["settings"]["font_options"]["font_size"] == 36


# ---------------------------------------------------------------------------
# Migration on a pristine database + repository CRUD (real SQL)
# ---------------------------------------------------------------------------


def _apply_migration(session: ExecSession) -> None:
    """Replay the migration exactly as src.database's runner does:
    split the file on every ';' and execute each fragment."""
    for fragment in MIGRATION.read_text().split(";"):
        if fragment.strip():
            session.conn.execute(to_sqlite(fragment))
    session.conn.commit()


def test_migration_applies_on_pristine_db_and_is_idempotent():
    session = ExecSession()
    _apply_migration(session)
    _apply_migration(session)  # IF NOT EXISTS: a second pass is a no-op
    columns = {
        row[1] for row in session.conn.execute("PRAGMA table_info(recipes)")
    }
    assert columns == {
        "id", "user_id", "name", "settings", "created_at", "updated_at",
    }
    session.conn.close()


async def test_repository_crud_round_trip():
    session = ExecSession()
    _apply_migration(session)
    recipe = {
        "id": "r-1",
        "user_id": "user-1",
        "name": "Trading Plain",
        "settings": {"template_mode": "plain", "broll_enabled": False},
    }
    await RecipeRepository.create_recipe(session, recipe)
    await RecipeRepository.create_recipe(
        session,
        {"id": "r-2", "user_id": "user-2", "name": "Other", "settings": {}},
    )

    stored = await RecipeRepository.get_recipe(session, "user-1", "r-1")
    assert stored["name"] == "Trading Plain"
    assert stored["settings"] == {"template_mode": "plain", "broll_enabled": False}
    assert stored["created_at"] is not None
    assert await RecipeRepository.get_recipe(session, "user-2", "r-1") is None

    mine = await RecipeRepository.list_recipes(session, "user-1")
    assert [r["id"] for r in mine] == ["r-1"]

    renamed = await RecipeRepository.update_recipe(
        session, "user-1", "r-1", {"name": "Trading Plain v2"}
    )
    assert renamed["name"] == "Trading Plain v2"
    assert renamed["settings"] == {"template_mode": "plain", "broll_enabled": False}

    re_set = await RecipeRepository.update_recipe(
        session, "user-1", "r-1", {"settings": {"music_mode": "off"}}
    )
    assert re_set["settings"] == {"music_mode": "off"}  # wholesale replace

    assert (
        await RecipeRepository.update_recipe(
            session, "user-2", "r-1", {"name": "stolen"}
        )
        is None
    )

    assert await RecipeRepository.delete_recipe(session, "user-1", "r-1") is True
    assert await RecipeRepository.get_recipe(session, "user-1", "r-1") is None
    assert await RecipeRepository.delete_recipe(session, "user-1", "r-1") is False
    session.conn.close()


# ---------------------------------------------------------------------------
# Routes with a fake repository (the test_brand_kit.py pattern)
# ---------------------------------------------------------------------------


class FakeRecipeRepository:
    """Mirrors RecipeRepository's contract, in memory."""

    def __init__(self):
        self.recipes = {}

    async def list_recipes(self, db, user_id):
        mine = [r for r in self.recipes.values() if r["user_id"] == user_id]
        return [dict(r) for r in reversed(mine)]

    async def get_recipe(self, db, user_id, recipe_id):
        recipe = self.recipes.get(recipe_id)
        return (
            dict(recipe)
            if recipe and recipe["user_id"] == user_id
            else None
        )

    async def create_recipe(self, db, recipe):
        self.recipes[recipe["id"]] = {
            **copy.deepcopy(recipe),
            "created_at": None,
            "updated_at": None,
        }
        return recipe

    async def update_recipe(self, db, user_id, recipe_id, fields):
        recipe = await self.get_recipe(db, user_id, recipe_id)
        if recipe is None:
            return None
        if not fields:
            return recipe
        recipe.update(copy.deepcopy(fields))
        self.recipes[recipe_id] = recipe
        return dict(recipe)

    async def delete_recipe(self, db, user_id, recipe_id):
        if await self.get_recipe(db, user_id, recipe_id) is None:
            return False
        del self.recipes[recipe_id]
        return True


class FakeRequest:
    def __init__(self, payload=None):
        self._payload = payload or {}

    async def json(self):
        return self._payload


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRecipeRepository()
    monkeypatch.setattr(routes, "RecipeRepository", fake)

    async def fake_user_id(request, db):
        return "user-1"

    monkeypatch.setattr(routes, "_get_authenticated_user_id", fake_user_id)
    return fake


async def test_route_create_with_bad_setting_is_422_and_persists_nothing(repo):
    with pytest.raises(HTTPException) as exc:
        await routes.create_recipe(
            FakeRequest(
                {"name": "x", "settings": {"template_mode": "fancy"}}
            ),
            db=None,
        )
    assert exc.value.status_code == 422
    assert repo.recipes == {}


async def test_route_crud_round_trip(repo, music_track):
    created = await routes.create_recipe(
        FakeRequest(
            {
                "name": "Trading Plain",
                "settings": {"template_mode": "PLAIN", "music_override": "lofi-beat.mp3"},
            }
        ),
        db=None,
    )
    assert created["name"] == "Trading Plain"
    assert created["settings"] == {
        "template_mode": "plain",
        "music_override": "lofi-beat.mp3",
    }
    recipe_id = created["id"]

    fetched = await routes.get_recipe(recipe_id, FakeRequest(), db=None)
    assert fetched["id"] == recipe_id

    fragment = await routes.get_recipe_fragment(recipe_id, FakeRequest(), db=None)
    assert fragment == {
        "recipe_id": recipe_id,
        "fragment": {"template_mode": "plain", "music_override": "lofi-beat.mp3"},
    }

    renamed = await routes.update_recipe(
        recipe_id, FakeRequest({"name": "Trading Plain v2"}), db=None
    )
    assert renamed["name"] == "Trading Plain v2"
    assert renamed["settings"] == created["settings"]  # settings kept

    replaced = await routes.update_recipe(
        recipe_id, FakeRequest({"settings": {"music_mode": "off"}}), db=None
    )
    assert replaced["settings"] == {"music_mode": "off"}  # wholesale

    listing = await routes.list_recipes(FakeRequest(), db=None)
    assert [r["id"] for r in listing["recipes"]] == [recipe_id]

    assert await routes.delete_recipe(recipe_id, FakeRequest(), db=None) == {
        "deleted": True,
        "id": recipe_id,
    }
    with pytest.raises(HTTPException) as exc:
        await routes.get_recipe(recipe_id, FakeRequest(), db=None)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await routes.get_recipe_fragment(recipe_id, FakeRequest(), db=None)
    assert exc.value.status_code == 404


async def test_route_other_users_recipe_is_404(repo, monkeypatch):
    created = await routes.create_recipe(
        FakeRequest({"name": "Mine"}), db=None
    )

    async def other_user(request, db):
        return "user-2"

    monkeypatch.setattr(routes, "_get_authenticated_user_id", other_user)
    with pytest.raises(HTTPException) as exc:
        await routes.get_recipe(created["id"], FakeRequest(), db=None)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await routes.update_recipe(
            created["id"], FakeRequest({"name": "stolen"}), db=None
        )
    assert exc.value.status_code == 404
