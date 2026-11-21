-- Per-task B-roll switch from the create form (true, false).
-- NULL means the row predates the control and the template default decides.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS broll_enabled BOOLEAN;
