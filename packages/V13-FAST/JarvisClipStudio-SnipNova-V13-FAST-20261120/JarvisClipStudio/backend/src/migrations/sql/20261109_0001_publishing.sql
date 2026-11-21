-- Publishing workspace: campaigns, per-clip post records and the
-- performance numbers the user types in after posting.
--
-- campaigns groups clips the user posted under one push (for example a
-- Whop Content Rewards campaign). Nothing is scraped or fetched: every
-- row exists only because the user created it in the workspace.
--
-- clip_posts records one manual post of one clip on one platform (the
-- user posts by hand in the platform app, then notes the URL here).
-- submission_url is the separate link-submission the Whop Content
-- Rewards flow requires shortly after posting, and submission_dismissed
-- records that the user waved the reminder away for that post.
--
-- clip_post_stats holds ONLY numbers the user typed in (views, likes,
-- comments, earnings). No row is ever estimated or filled by the app.
--
-- Reversible: DROP TABLE clip_post_stats, clip_posts and campaigns.

CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    campaign_url TEXT,
    budget DOUBLE PRECISION,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_campaigns_user
    ON campaigns (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS clip_posts (
    id TEXT PRIMARY KEY,
    clip_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    post_url TEXT NOT NULL DEFAULT '',
    posted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    campaign_id TEXT,
    submission_url TEXT,
    submission_dismissed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (clip_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_clip_posts_user
    ON clip_posts (user_id, posted_at DESC);

CREATE INDEX IF NOT EXISTS idx_clip_posts_campaign
    ON clip_posts (campaign_id);

CREATE TABLE IF NOT EXISTS clip_post_stats (
    post_id TEXT PRIMARY KEY,
    views BIGINT,
    likes BIGINT,
    comments BIGINT,
    earnings DOUBLE PRECISION,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
