-- Auto-niche classification outcome: persisted per task so a failed
-- detection is visible instead of silently dressing as a real one.
-- niche, confidence, is_fallback, the model that answered, the last
-- error and the per-candidate attempt record live in one JSON TEXT
-- column, the same shape as brief_report and qc_report.
-- Rollback is
--   ALTER TABLE tasks DROP COLUMN IF EXISTS classification
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS classification TEXT;
