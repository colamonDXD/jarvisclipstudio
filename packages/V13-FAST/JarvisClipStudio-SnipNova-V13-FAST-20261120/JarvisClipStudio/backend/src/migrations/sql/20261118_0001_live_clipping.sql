-- Live clipping (LIVE_CLIPPING, off by default): a user starts a session
-- on a live stream URL and finished vertical clips appear while the stream
-- is still running.
--
-- live_sessions holds one row per session. The API inserts it as
-- requested and the live worker claims it (status starting, then
-- recording). A stop from the user sets stop_requested and the worker
-- ends the session (status stopped). A worker that dies leaves its
-- heartbeat stale and the next worker marks the row as error.
--
-- live_clips holds one row per detected moment: rejected (with the
-- reason, so the UI can say why no clip was made), queued (quick preview
-- ready), rendering, then ready (clip_path) or failed.

CREATE TABLE IF NOT EXISTS live_sessions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    url TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'requested',
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    stop_requested BOOLEAN NOT NULL DEFAULT FALSE,
    worker_id VARCHAR(120),
    heartbeat_at TIMESTAMP WITH TIME ZONE,
    recording BOOLEAN NOT NULL DEFAULT FALSE,
    buffered_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    restarts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    started_at TIMESTAMP WITH TIME ZONE,
    stopped_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_live_sessions_user ON live_sessions (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_live_sessions_status ON live_sessions (status);

CREATE TABLE IF NOT EXISTS live_clips (
    id VARCHAR(36) PRIMARY KEY,
    session_id VARCHAR(36) NOT NULL REFERENCES live_sessions (id) ON DELETE CASCADE,
    n INTEGER,
    status VARCHAR(20) NOT NULL,
    reason TEXT,
    onset_at TIMESTAMP WITH TIME ZONE,
    start_at TIMESTAMP WITH TIME ZONE,
    end_at TIMESTAMP WITH TIME ZONE,
    signals JSONB NOT NULL DEFAULT '[]'::jsonb,
    strength DOUBLE PRECISION,
    talk_share DOUBLE PRECISION,
    preview_path TEXT,
    clip_path TEXT,
    duration DOUBLE PRECISION,
    render_seconds DOUBLE PRECISION,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_live_clips_session ON live_clips (session_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_live_clips_session_n ON live_clips (session_id, n) WHERE n IS NOT NULL
