-- Recipes: saved, named bundles of task creation settings.
--
-- A recipe stores one JSON payload holding only keys the task create
-- path already accepts: template mode, niche override, music mode and
-- override, the B-roll switches, separation opt-in, creation mode,
-- clip length, target clip count, output format, subtitles, processing
-- mode, caption template, font options, a brand kit pick and the
-- cleanup toggles. Validation lives in src/recipes.py and reuses the
-- create path's own validators, so a stored recipe can never name a
-- setting the create path would reject or silently replace.
--
-- Recipes are inert in this slice: nothing reads them during task
-- creation yet. A later slice wires the apply step into the create
-- form, and applying a recipe must stay exactly equivalent to picking
-- the same controls by hand.
--
-- Reversible: DROP TABLE recipes.

CREATE TABLE IF NOT EXISTS recipes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    settings JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_recipes_user
    ON recipes (user_id, created_at DESC);
