-- Watch-folder autopilot gains the per-task music mode (auto, on, off)
-- that the create form already stores on tasks. NULL means the row
-- predates the control and behaves as Auto, matching tasks.music_mode.
ALTER TABLE watch_folder_settings ADD COLUMN IF NOT EXISTS music_mode VARCHAR(10);
