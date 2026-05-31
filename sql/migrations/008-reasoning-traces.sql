-- Migration 008: reasoning_traces
-- Stores DeepSeek <think> blocks for each LINA turn so LINA can introspect
-- her own reasoning history (issue #62).

CREATE TABLE reasoning_traces (
    id            BIGSERIAL PRIMARY KEY,
    session_id    TEXT        NOT NULL,
    turn_number   INTEGER     NOT NULL DEFAULT 0,
    thinking_text TEXT        NOT NULL,
    prompt_hash   TEXT,                         -- SHA256 of user prompt (for dedup)
    model         TEXT        NOT NULL DEFAULT 'deepseek-v4-flash',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_traces_session ON reasoning_traces (session_id, turn_number DESC);
CREATE INDEX idx_traces_created ON reasoning_traces (created_at DESC);

-- Full-text search index for search_traces() MCP tool
CREATE INDEX idx_traces_fts ON reasoning_traces
    USING gin(to_tsvector('spanish', thinking_text));

COMMENT ON TABLE reasoning_traces IS
    'DeepSeek <think>…</think> blocks persisted per turn for LINA self-analysis (issue #62).';
COMMENT ON COLUMN reasoning_traces.session_id IS
    'Gateway deterministic session ID (e.g. telegram-123456789).';
COMMENT ON COLUMN reasoning_traces.turn_number IS
    'Monotonically increasing turn index within the session (mirrors session_messages).';
COMMENT ON COLUMN reasoning_traces.thinking_text IS
    'Raw text content of the <think> block(s) for this turn (concatenated if multiple).';
COMMENT ON COLUMN reasoning_traces.prompt_hash IS
    'SHA-256 hex digest of the user prompt — used to deduplicate accidental double-saves.';
COMMENT ON COLUMN reasoning_traces.model IS
    'DeepSeek model that produced this trace (e.g. deepseek-v4-flash).';
