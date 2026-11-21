-- YouTube channel monitoring, stage 2: premiere waiting, the yt-dlp
-- fallback stamp, and per-channel campaign and brand kit defaults.
--
-- channel_monitor_videos gains waiting_since. A video whose
-- preflight says it is an upcoming premiere or a stream that has not
-- finished yet cannot be downloaded, and that is not a failure: the
-- claim moves to a waiting state and is re-checked on the normal
-- poll cadence without spending any of its bounded retry attempts.
-- waiting_since records when the current wait began, and the wait is
-- bounded: a video still unavailable after the waiting limit parks
-- with the reason on the row instead of waiting forever.
--
-- channel_monitors gains last_fallback_at, the stamp of the most
-- recent yt-dlp fallback detection pass. The RSS feed stays the
-- primary detector: the fallback runs at most once an hour for a
-- channel whose feed is erroring or answering empty, and the stamp
-- is what makes that rate limit durable across restarts.
--
-- channel_monitors also gains campaign_id and brand_kit_id, the
-- per-channel defaults the manual create flow already supports: a
-- campaign snapshots its stored brief onto every task the channel
-- creates, and a brand kit applies its look, exactly as a manual
-- task carrying the same ids would.
--
-- Reversible: DROP COLUMN campaign_id, brand_kit_id and
-- last_fallback_at from channel_monitors, and DROP COLUMN
-- waiting_since from channel_monitor_videos.

ALTER TABLE channel_monitors
    ADD COLUMN IF NOT EXISTS campaign_id TEXT;

ALTER TABLE channel_monitors
    ADD COLUMN IF NOT EXISTS brand_kit_id TEXT;

ALTER TABLE channel_monitors
    ADD COLUMN IF NOT EXISTS last_fallback_at TIMESTAMPTZ;

ALTER TABLE channel_monitor_videos
    ADD COLUMN IF NOT EXISTS waiting_since TIMESTAMPTZ;
