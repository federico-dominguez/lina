-- Migration 005: Token usage tracking per conversation turn
-- Part of issue #61 — Token/cost metering
--
-- Records estimated token consumption and inferred cost for every
-- user↔assistant turn, enabling per-session and daily cost reports.
--
-- Design notes:
--   • Tokens are ESTIMATED (chars / 3) — goosed does not expose usage from
--     the DeepSeek SSE stream. Error is ±15% empirically.
--   • Cost is calculated using pricing as of 2026-05-31 and stored at write
--     time so historical records are not affected by price changes.
--   • model defaults to 'deepseek-v4-flash' (= deepseek-chat, non-thinking).
--     When the gateway knows the model, it passes it explicitly.
--   • No UNIQUE constraint to allow retry writes.

CREATE TABLE IF NOT EXISTS token_usage (
    id                    BIGSERIAL    PRIMARY KEY,
    session_id            TEXT         NOT NULL,
    model                 TEXT         NOT NULL DEFAULT 'deepseek-v4-flash',
    -- character counts (raw, always available)
    prompt_chars          INTEGER      NOT NULL,
    completion_chars      INTEGER      NOT NULL,
    -- estimated token counts (chars / 3, rounded)
    prompt_tokens_est     INTEGER      NOT NULL,
    completion_tokens_est INTEGER      NOT NULL,
    -- cost in USD (calculated at write time using current pricing)
    cost_usd_est          NUMERIC(14, 8) NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_token_usage_session
    ON token_usage (session_id);

CREATE INDEX IF NOT EXISTS idx_token_usage_created
    ON token_usage (created_at DESC);

COMMENT ON TABLE  token_usage IS 'Estimated token consumption per conversation turn (issue #61)';
COMMENT ON COLUMN token_usage.prompt_tokens_est     IS 'Estimated input tokens = prompt_chars / 3';
COMMENT ON COLUMN token_usage.completion_tokens_est IS 'Estimated output tokens = completion_chars / 3';
COMMENT ON COLUMN token_usage.cost_usd_est          IS 'Cost at write time, USD (input*price_in + output*price_out)';
