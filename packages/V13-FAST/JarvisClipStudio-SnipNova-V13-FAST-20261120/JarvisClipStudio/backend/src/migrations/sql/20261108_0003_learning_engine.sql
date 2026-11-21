-- Learning engine: feedback events + versioned adjustment documents.
--
-- learning_feedback_events is an append-only log of how the user reacted to
-- clips (explicit accept/reject verdicts plus weaker passive signals the
-- app already produces: downloads, exports, deletions, re-render requests).
-- Each row snapshots the clip's features at signal time — virality
-- sub-scores, hook type, resolved niche, caption preset, music/SFX
-- settings — so the learning engine can aggregate evidence without joining
-- back into task/clip rows that may later be edited or deleted.
--
-- learning_versions stores one immutable adjustment document per
-- recomputation (JSON: per-niche bounded adjustments — selection sub-score
-- weights, SFX density multiplier, music volume multiplier, preferred
-- caption preset — plus the event count it was computed from and a
-- human-readable change summary). Rollback inserts a NEW version whose
-- adjustments copy a prior version's — history is never rewritten.
--
-- No foreign keys into tasks/clips on purpose: deleting a clip or a task
-- must never erase the preference history learned from it (the same
-- pattern as 20261105_0001_ai_edit_usage.sql).
--
-- Reversible: DROP TABLE learning_versions, learning_feedback_events.

CREATE TABLE IF NOT EXISTS learning_feedback_events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    clip_id TEXT NOT NULL,
    niche TEXT,
    verdict TEXT NOT NULL,
    reason TEXT,
    source TEXT NOT NULL DEFAULT 'explicit',
    weight REAL NOT NULL DEFAULT 1.0,
    features TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS learning_feedback_events_user_created_idx
    ON learning_feedback_events (user_id, created_at);

CREATE INDEX IF NOT EXISTS learning_feedback_events_user_niche_idx
    ON learning_feedback_events (user_id, niche);

CREATE TABLE IF NOT EXISTS learning_versions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    adjustments TEXT NOT NULL DEFAULT '{}',
    event_count INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, version)
);

CREATE INDEX IF NOT EXISTS learning_versions_user_version_idx
    ON learning_versions (user_id, version);
