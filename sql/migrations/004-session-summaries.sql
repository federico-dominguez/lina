-- Migration 004: Structured session summaries for smart context injection
-- Part of issue #60 — Smart context summarization
--
-- Stores a structured summary of each session produced at session-end
-- (via lina-db `summarize_session_smart` tool + session-end.yaml recipe).
-- Used by the gateway at session-start to warm up goosed with a compact,
-- high-signal context block instead of injecting raw messages.
--
-- Design decisions:
--   • topics / facts / pending stored as TEXT[] for easy retrieval and display
--   • raw_summary is the full prose summary (for warmup prompt)
--   • tokens_in/out are optional — populated when LINA generates the summary
--     via DeepSeek; NULL if summary was written manually
--   • One row per session (UNIQUE on session_id) — upsert on conflict

CREATE TABLE IF NOT EXISTS session_summaries (
    id           BIGSERIAL   PRIMARY KEY,
    session_id   TEXT        NOT NULL UNIQUE,
    topics       TEXT[]      NOT NULL DEFAULT '{}',
    facts        TEXT[]      NOT NULL DEFAULT '{}',
    pending      TEXT[]      NOT NULL DEFAULT '{}',
    raw_summary  TEXT        NOT NULL,
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fast lookup by session_id (UNIQUE already creates a btree index, but named
-- explicitly so migrations/rollbacks are clear).
CREATE INDEX IF NOT EXISTS idx_session_summaries_session
    ON session_summaries (session_id);

-- Chronological retrieval: get the most recently updated summary.
CREATE INDEX IF NOT EXISTS idx_session_summaries_updated
    ON session_summaries (updated_at DESC);

COMMENT ON TABLE session_summaries IS
    'Structured summaries of past sessions for compact context injection at session-start.';

COMMENT ON COLUMN session_summaries.raw_summary IS
    'Prose summary injected verbatim into the goosed warmup prompt.';

COMMENT ON COLUMN session_summaries.topics IS
    'High-level topic tags extracted from the session (e.g. ["Moodle M2-R7", "config goosed"]).';

COMMENT ON COLUMN session_summaries.facts IS
    'Discrete facts to remember across sessions (e.g. ["número favorito = 42"]).';

COMMENT ON COLUMN session_summaries.pending IS
    'Action items that were not completed in the session.';
