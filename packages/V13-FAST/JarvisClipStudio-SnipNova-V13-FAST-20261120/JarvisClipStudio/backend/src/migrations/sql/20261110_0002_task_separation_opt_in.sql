-- Per-task opt-in for voice/music separation before transcription.
-- FALSE means the row predates the control or the user never opted in
-- and transcription uses the original audio exactly as before. The
-- opt-in is honoured only while SEPARATION_ENABLED is on for the
-- deployment.
-- Rollback is
--   ALTER TABLE tasks DROP COLUMN IF EXISTS separation_opt_in
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS separation_opt_in BOOLEAN NOT NULL DEFAULT FALSE;
