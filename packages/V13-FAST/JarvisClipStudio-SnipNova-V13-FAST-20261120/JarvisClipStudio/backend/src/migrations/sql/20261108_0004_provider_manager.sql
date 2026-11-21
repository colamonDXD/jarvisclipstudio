-- Local-first provider manager: per-call LLM token accounting and the
-- persisted provider cooldown registry. Token numbers are stored exactly as
-- providers reported them — usage_known = FALSE marks calls whose response
-- carried no usage block (tokens stay NULL, never invented).
CREATE TABLE IF NOT EXISTS llm_token_usage (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    alias TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'generic',
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    usage_known BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_llm_token_usage_task
    ON llm_token_usage (task_id);

CREATE TABLE IF NOT EXISTS provider_cooldowns (
    alias TEXT PRIMARY KEY,
    failure_class TEXT NOT NULL,
    cooldown_until TIMESTAMP NOT NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 1,
    last_error TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
