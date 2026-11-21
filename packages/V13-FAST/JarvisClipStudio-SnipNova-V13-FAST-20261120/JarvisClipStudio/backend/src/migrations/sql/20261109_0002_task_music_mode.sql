-- Per-task music mode choice from the create form (auto, on, off).
-- NULL means the row predates the control and behaves as Auto.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS music_mode VARCHAR(10);
