-- Explicit auto-director niche choices for tasks and individual clips.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS niche_override VARCHAR(40);

ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS niche_override VARCHAR(40);
