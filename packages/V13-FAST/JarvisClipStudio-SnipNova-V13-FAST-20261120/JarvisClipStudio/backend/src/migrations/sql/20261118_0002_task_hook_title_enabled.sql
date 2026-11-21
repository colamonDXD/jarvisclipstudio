-- Title on/off from the create form (owner, 2026-11-18: "sometimes there is
-- no need of titles, give an option"). NULL = the title is drawn, as on every
-- task before the control. FALSE = no hook title is burned into the clips.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS hook_title_enabled BOOLEAN
