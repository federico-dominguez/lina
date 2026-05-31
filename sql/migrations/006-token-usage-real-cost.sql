-- Migration 006: Add accumulated_cost_usd to token_usage
--
-- goosed exposes token_state.accumulatedCost in every SSE Finish event.
-- This is a monotonic per-session counter of real USD cost from DeepSeek.
-- The gateway stores it here; per-turn cost = current - previous row for the session.
--
-- The existing prompt_tokens_est / completion_tokens_est columns are now
-- populated with real token counts (no longer estimates).

ALTER TABLE token_usage
    ADD COLUMN IF NOT EXISTS accumulated_cost_usd NUMERIC(14, 8) NOT NULL DEFAULT 0;

COMMENT ON COLUMN token_usage.prompt_tokens_est IS
    'Prompt token count from goosed token_state.inputTokens (real, not estimated)';
COMMENT ON COLUMN token_usage.completion_tokens_est IS
    'Completion token count from goosed token_state.outputTokens (real, not estimated)';
COMMENT ON COLUMN token_usage.cost_usd_est IS
    'Per-turn cost in USD (delta: accumulated_cost_usd - previous row), real DeepSeek cost';
COMMENT ON COLUMN token_usage.accumulated_cost_usd IS
    'Monotonic accumulated session cost in USD from goosed token_state.accumulatedCost';
