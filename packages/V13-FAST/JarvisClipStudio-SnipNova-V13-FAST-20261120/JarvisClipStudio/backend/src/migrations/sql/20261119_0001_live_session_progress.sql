-- Live clipping of finished streams (VODs): a session can be a long video,
-- not only a live stream. progress_message is the step the worker is on
-- ("Listening for reactions: 3/22 parts"), shown on the Live page.
ALTER TABLE live_sessions ADD COLUMN IF NOT EXISTS progress_message TEXT
