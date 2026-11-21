-- Per-task framing mode choice from the create form (auto, fit, crop).
-- NULL means the row predates the control and the template default decides the framing.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS framing_mode VARCHAR(10);
