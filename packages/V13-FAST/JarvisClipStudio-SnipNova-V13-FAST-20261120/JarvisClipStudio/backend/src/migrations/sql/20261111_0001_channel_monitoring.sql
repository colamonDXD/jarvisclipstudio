-- YouTube channel monitoring: per-user settings, one row per watched
-- channel, and the durable claim record for every video a channel has
-- published since monitoring started.
--
-- channel_monitor_settings holds one row per user with the master
-- toggle for the whole feature.
--
-- channel_monitors holds the channel identity resolved at add time,
-- its poll health, and the task defaults applied to new uploads. The
-- template mode is stored explicitly on every row and never NULL:
-- plain is the standing default for unattended tasks, and auto stays
-- one edit away per channel. The creation mode is a full workflow the
-- watcher can run unattended, so the manual cutter is refused by the
-- API before a row is written.
--
-- channel_monitor_videos is the restart-proof memory of the watcher.
-- The unique constraint on user plus video is the duplicate guard:
-- claiming is an insert that does nothing on conflict, and only the
-- caller that actually inserted the row may create a task for the
-- video, so a restart or a second backend process cannot queue it
-- twice. Videos already on the channel when it is added are written
-- as baseline rows and never turned into tasks. A video that cannot
-- be clipped is recorded as skipped with a plain reason, or parked
-- after the bounded retry count, and stays visible in the activity
-- list either way.
--
-- Reversible: DROP TABLE channel_monitor_videos, channel_monitors
-- and channel_monitor_settings.

CREATE TABLE IF NOT EXISTS channel_monitor_settings (
    user_id TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS channel_monitors (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    channel_title TEXT,
    source_input TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    niche_override TEXT,
    music_mode TEXT,
    music_override TEXT,
    template_mode TEXT NOT NULL DEFAULT 'plain',
    creation_mode TEXT NOT NULL DEFAULT 'full_auto',
    target_clip_count INTEGER,
    processing_mode TEXT,
    include_shorts BOOLEAN NOT NULL DEFAULT false,
    max_tasks_per_day INTEGER DEFAULT 3,
    last_polled_at TIMESTAMPTZ,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, channel_id)
);

CREATE TABLE IF NOT EXISTS channel_monitor_videos (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    monitor_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    video_url TEXT NOT NULL,
    title TEXT,
    published_at TIMESTAMPTZ,
    state TEXT NOT NULL DEFAULT 'processing',
    skip_reason TEXT,
    task_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, video_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_monitor_videos_monitor_state
    ON channel_monitor_videos (monitor_id, state, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_channel_monitor_videos_user_created
    ON channel_monitor_videos (user_id, created_at DESC);
