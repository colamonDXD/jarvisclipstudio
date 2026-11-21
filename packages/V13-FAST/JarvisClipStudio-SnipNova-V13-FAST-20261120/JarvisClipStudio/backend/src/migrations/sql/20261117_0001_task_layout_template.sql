-- Per-task streamer layout template from the create form ("face on top, game below":
-- facecam_red, facecam_gold) and the optional credit line it draws ("@creator").
-- NULL means the automatic layout (every task before this control).
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS layout_template VARCHAR(20);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS layout_credit VARCHAR(48);
