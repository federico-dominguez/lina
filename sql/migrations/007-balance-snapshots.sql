-- Migration 007: Balance snapshots for real DeepSeek cost tracking
--
-- DeepSeek has no billing/usage API. The only way to get exact spend matching
-- the dashboard is to track the actual account balance over time.
--
-- Strategy:
--   1. Record balance at gateway startup, periodically, and on-demand.
--   2. real_monthly_cost(year, month) = first_snapshot_of_month - last_snapshot_of_month
--   3. This gives numbers identical to the DeepSeek dashboard.
--
-- Also: add cache-aware token columns for future use when goosed exposes them.

CREATE TABLE IF NOT EXISTS balance_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    balance_usd NUMERIC(12, 4) NOT NULL,
    source      TEXT NOT NULL DEFAULT 'periodic', -- 'startup', 'periodic', 'manual'
    api_key_hint TEXT,                            -- last 4 chars of key for multi-key support
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_balance_snapshots_recorded
    ON balance_snapshots (recorded_at DESC);

COMMENT ON TABLE balance_snapshots IS
    'Periodic snapshots of the DeepSeek account balance. '
    'Daily/monthly spend = snapshot_at_start - snapshot_at_end. '
    'Matches the DeepSeek dashboard exactly.';
