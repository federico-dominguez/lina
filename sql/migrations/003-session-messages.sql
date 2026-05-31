-- Migration 003: Short-term session message persistence
-- Part of issue #56 — Sistema de Memoria Multinivel
--
-- Persists individual conversation turns (user + assistant messages) to allow
-- context recovery after goosed restarts, hot-swaps, or crashes.
-- TTL: messages are cleaned up after 48h by the memory consolidator (future).
--
-- Design decisions:
--   • session_id matches the gateway's deterministic "telegram-{chat_id}" key
--   • turn_number is zero-based, incremented per chat_id (not globally unique)
--   • role CHECK mirrors goosed's valid roles
--   • content is plaintext (not HTML) — Telegram formatting applied at display time
--   • No FK to sessions table: messages can arrive before a session row exists

CREATE TABLE IF NOT EXISTS session_messages (
    id          BIGSERIAL   PRIMARY KEY,
    session_id  TEXT        NOT NULL,
    turn_number INTEGER     NOT NULL DEFAULT 0,
    role        TEXT        NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_messages_lookup
    ON session_messages (session_id, turn_number DESC);

CREATE INDEX IF NOT EXISTS idx_session_messages_age
    ON session_messages (created_at DESC);

COMMENT ON TABLE session_messages IS
    'Short-term conversation turns per session. Cleaned up after 48h.';

COMMENT ON COLUMN session_messages.session_id IS
    'Matches gateway deterministic ID: telegram-{chat_id}';
