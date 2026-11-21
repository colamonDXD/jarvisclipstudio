-- Per-task clip-count target chosen on the create form (a whole number of
-- clips). NULL means Auto, where the duration-based formula decides.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS target_clip_count INTEGER;
