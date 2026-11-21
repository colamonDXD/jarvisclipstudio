-- Content Requirements and Clip Targeting: the task-level campaign
-- brief and its search outcome, plus the per-clip brief compliance
-- verdict. JSON payloads live in TEXT columns, the same shape as
-- qc_report and clip_plan. The same migration also gives Publishing
-- campaigns a reusable brief and an example link, and records which
-- campaign a task was created from. Rollback is
--   ALTER TABLE tasks DROP COLUMN IF EXISTS campaign_id
--   ALTER TABLE tasks DROP COLUMN IF EXISTS brief_report
--   ALTER TABLE tasks DROP COLUMN IF EXISTS content_brief
--   ALTER TABLE generated_clips DROP COLUMN IF EXISTS compliance_report
--   ALTER TABLE generated_clips DROP COLUMN IF EXISTS compliance_status
--   ALTER TABLE campaigns DROP COLUMN IF EXISTS example_url
--   ALTER TABLE campaigns DROP COLUMN IF EXISTS brief
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS content_brief TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS brief_report TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS campaign_id TEXT;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS compliance_status VARCHAR(20);
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS compliance_report TEXT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS brief TEXT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS example_url TEXT;
