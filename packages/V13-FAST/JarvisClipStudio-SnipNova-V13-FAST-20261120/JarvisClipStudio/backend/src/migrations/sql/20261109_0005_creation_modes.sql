-- Four explicit creation modes (tracker 1.9): the workflow a task was
-- created with, chosen on the create form. NULL means no mode was
-- chosen and the task follows the historical pipeline exactly.
-- manual_ranges_json holds the validated Manual Cutter ranges as a
-- JSON array of start and end seconds and stays NULL for every other
-- mode because only the cutter contract accepts user ranges.
--
-- Reversible: drop both added columns.

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS creation_mode TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS manual_ranges_json TEXT;
