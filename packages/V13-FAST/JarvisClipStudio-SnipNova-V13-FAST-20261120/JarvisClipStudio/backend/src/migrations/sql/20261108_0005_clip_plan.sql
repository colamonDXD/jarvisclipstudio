-- Per-clip Viral Intelligence Engine plan (JSON): the hook call, opening
-- treatment, planned SFX accents and music decision for the clip, each
-- with its reason, written by the pipeline plan seam before rendering.
-- Additive and reversible: rollback is
--   ALTER TABLE generated_clips DROP COLUMN IF EXISTS clip_plan
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS clip_plan TEXT;
