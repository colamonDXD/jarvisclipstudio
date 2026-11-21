-- Hook-variant previews: short alternative openings rendered for a clip
-- through the normal EDL render path, plus the record of which opening
-- the user picked for the full clip.
--
-- clip_hook_previews holds one row per rendered preview. params keeps
-- the full segment layout and opening treatment for that variant so
-- picking it later re-renders the full clip from the exact snapshot,
-- even after newer preview batches exist. Rows are replaced per clip
-- when a fresh batch is generated.
--
-- generated_clips.hook_variant names the variant kind currently applied
-- to the full clip (NULL means the original opening) and
-- hook_variant_batch records which preview batch it was picked from.
--
-- Reversible: DROP TABLE clip_hook_previews and drop the two columns
-- from generated_clips.

CREATE TABLE IF NOT EXISTS clip_hook_previews (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    clip_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    variant TEXT NOT NULL,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    params TEXT NOT NULL DEFAULT '{}',
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    duration DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_clip_hook_previews_clip
    ON clip_hook_previews (clip_id, created_at DESC);

ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS hook_variant TEXT;

ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS hook_variant_batch TEXT;
