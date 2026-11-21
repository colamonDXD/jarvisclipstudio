-- Per-task template mode choice from the create form (auto, plain).
-- NULL means the row predates the control and behaves as Auto.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS template_mode VARCHAR(10);
