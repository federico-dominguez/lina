-- Migration 002 — gateway_events
-- Tracks gateway lifecycle events for session recovery (issue #49).
-- Each row records a 'started' or 'shutdown' event from lina-gateway.
-- The boot hook reads the last row to decide whether the previous run was
-- graceful (last event = 'shutdown') or interrupted (last event = 'started').

CREATE TABLE IF NOT EXISTS gateway_events (
    id          BIGSERIAL   PRIMARY KEY,
    event_type  TEXT        NOT NULL CHECK (event_type IN ('started', 'shutdown')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gateway_events_created
    ON gateway_events (created_at DESC);

COMMENT ON TABLE  gateway_events IS
    'Lifecycle events from lina-gateway; used by the boot hook for session recovery.';
COMMENT ON COLUMN gateway_events.event_type IS
    '''started'' written on boot, ''shutdown'' written on graceful SIGTERM.';
