-- Watch-folder autopilot: per-user settings plus the durable record of
-- every file the watcher has claimed.
--
-- watch_folder_settings holds one row per user: whether the autopilot
-- is on, which folder it watches (NULL means the configured default),
-- and the task defaults applied to dropped files. A NULL niche or
-- music override means the normal pipeline decides, exactly like a
-- manual upload with no override picked.
--
-- watch_folder_files is the restart-proof memory of the watcher. The
-- fingerprint is path plus size plus mtime, and the unique constraint
-- is what stops a restart or a second backend process from queueing
-- the same file twice. State moves detected -> processing -> queued,
-- or processing -> failed with an attempt counter, and failed rows
-- park after the attempt cap instead of retrying forever. The source
-- file itself is never moved or deleted by the watcher.
--
-- Reversible: DROP TABLE watch_folder_files and watch_folder_settings.

CREATE TABLE IF NOT EXISTS watch_folder_settings (
    user_id TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT false,
    folder_path TEXT,
    niche_override TEXT,
    music_override TEXT,
    target_clip_count INTEGER,
    processing_mode TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS watch_folder_files (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    mtime_ns BIGINT NOT NULL,
    state TEXT NOT NULL DEFAULT 'processing',
    task_id TEXT,
    upload_path TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_watch_folder_files_user_state
    ON watch_folder_files (user_id, state, updated_at DESC);
