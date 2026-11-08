-- Per-clip AI quality control (Final Directive Part I): the QC verdict and
-- its evidence report, stored on the clip after rendering. Additive and
-- reversible: rollback is
--   ALTER TABLE generated_clips DROP COLUMN IF EXISTS qc_report;
--   ALTER TABLE generated_clips DROP COLUMN IF EXISTS qc_status;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS qc_status VARCHAR(20);
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS qc_report TEXT;
