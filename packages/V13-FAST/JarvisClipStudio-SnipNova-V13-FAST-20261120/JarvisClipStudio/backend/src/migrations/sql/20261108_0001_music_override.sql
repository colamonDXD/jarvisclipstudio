-- Per-task background-music choice from the music library (a filename).
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS music_override VARCHAR(255);
