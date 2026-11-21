-- Watch-folder autopilot gains an optional recipe: when set, dropped
-- files are created with the recipe's saved choices (see the recipes
-- table in 20261115_0001_recipes.sql), exactly as a task created from
-- a recipe through the create path. NULL means no recipe: the
-- autopilot's own defaults apply, exactly as before.
--
-- Reversible: ALTER TABLE watch_folder_settings DROP COLUMN recipe_id.
ALTER TABLE watch_folder_settings ADD COLUMN IF NOT EXISTS recipe_id TEXT;
