-- Brand Kit: per-user reusable branding applied to generated clips.
--
-- brand_kits holds one kit per row: the caption colours and template,
-- the caption font, the hook-title treatment, the default music track
-- and the logo watermark choice. A kit changes exported clips only when
-- a task opts in through tasks.brand_kit_id, so kits are inert until
-- picked. Deleting a kit clears the opt-in on its tasks instead of
-- breaking them.
--
-- tasks gains brand_kit_id (the per-task opt-in) and tasks plus brand
-- kits gain intro_asset and outro_asset name columns. Those two columns
-- only record which library video files the user picked: the current
-- render pipeline does not stitch intro or outro bumpers yet, so the
-- names are carried through task creation and task reads honestly
-- without pretending the render applies them.
--
-- Reversible: DROP TABLE brand_kits and drop the added columns.

CREATE TABLE IF NOT EXISTS brand_kits (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    primary_color TEXT,
    accent_color TEXT,
    caption_template TEXT,
    font_name TEXT,
    title_style TEXT,
    music_track TEXT,
    logo_filename TEXT,
    logo_position TEXT NOT NULL DEFAULT 'bottom_right',
    logo_opacity DOUBLE PRECISION NOT NULL DEFAULT 0.85,
    intro_asset TEXT,
    outro_asset TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_brand_kits_user
    ON brand_kits (user_id, created_at DESC);

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS brand_kit_id TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS intro_asset TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS outro_asset TEXT;
