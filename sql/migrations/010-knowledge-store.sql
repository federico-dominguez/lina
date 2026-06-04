-- LINA — Migration 010: Unified Knowledge Store (RAG + Multi-Agent)
-- Issue #152: Sistema de Memoria Unificada
--
-- Crea 3 tablas para el sistema de memoria unificada:
--   1. knowledge_store     — conocimiento compartido multi-agente con embeddings
--   2. session_summaries   — agrega embedding y agent a la tabla existente
--   3. repo_index          — índice RAG del código fuente
--
-- Depende de: pgvector (migration 007), session_summaries (migration 004)

-- ═══════════════════════════════════════════════════════════════════════════════
--  1. knowledge_store — Conocimiento compartido multi-agente
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS knowledge_store (
    id          SERIAL PRIMARY KEY,
    key         TEXT        NOT NULL UNIQUE,
    value       TEXT        NOT NULL,
    embedding   vector(1536),
    source      TEXT        NOT NULL DEFAULT 'chat',  -- chat, web, repo, preference, manual
    agent       TEXT        DEFAULT NULL,               -- lina, gemma, cline, goose, null
    tags        TEXT[]      DEFAULT '{}',
    ttl         TIMESTAMPTZ DEFAULT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices knowledge_store
CREATE INDEX IF NOT EXISTS idx_knowledge_source ON knowledge_store (source);
CREATE INDEX IF NOT EXISTS idx_knowledge_agent ON knowledge_store (agent);
CREATE INDEX IF NOT EXISTS idx_knowledge_tags ON knowledge_store USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_knowledge_ttl ON knowledge_store (ttl) WHERE ttl IS NOT NULL;
-- IVFFlat index for ANN cosine similarity search (lists=100 for up to ~100k rows)
CREATE INDEX IF NOT EXISTS idx_knowledge_embedding
    ON knowledge_store
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

COMMENT ON TABLE knowledge_store IS
    'Unified multi-agent knowledge store with vector embeddings for semantic search.';

COMMENT ON COLUMN knowledge_store.source IS
    'Origin of the knowledge: chat, web, repo, preference, manual.';
COMMENT ON COLUMN knowledge_store.agent IS
    'Agent that created/imported this knowledge (lina, gemma, cline, goose, null).';
COMMENT ON COLUMN knowledge_store.tags IS
    'Automatic and manual tags for filtering (e.g. {bug_fix, config, api_doc}).';
COMMENT ON COLUMN knowledge_store.ttl IS
    'Expiration timestamp; NULL = permanent memory.';

-- ═══════════════════════════════════════════════════════════════════════════════
--  2. session_summaries — Agregar embedding + agent a la tabla existente
-- ═══════════════════════════════════════════════════════════════════════════════

ALTER TABLE session_summaries
    ADD COLUMN IF NOT EXISTS embedding vector(1536),
    ADD COLUMN IF NOT EXISTS agent TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_session_summaries_embedding
    ON session_summaries
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

COMMENT ON COLUMN session_summaries.embedding IS
    'Vector embedding of raw_summary for semantic search across sessions.';
COMMENT ON COLUMN session_summaries.agent IS
    'Agent that generated this session summary.';

-- ═══════════════════════════════════════════════════════════════════════════════
--  3. repo_index — Índice RAG del código fuente
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS repo_index (
    id            SERIAL PRIMARY KEY,
    file_path     TEXT        NOT NULL,
    content       TEXT        NOT NULL,
    embedding     vector(1536),
    language      TEXT        DEFAULT NULL,
    last_commit   TEXT        DEFAULT NULL,
    chunk_index   INTEGER     DEFAULT 0,   -- order within a file (0, 1, 2...)
    total_chunks  INTEGER     DEFAULT 1,
    indexed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_repo_file_path ON repo_index (file_path);
CREATE INDEX IF NOT EXISTS idx_repo_language ON repo_index (language);
CREATE INDEX IF NOT EXISTS idx_repo_embedding
    ON repo_index
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

COMMENT ON TABLE repo_index IS
    'Semantic code index for RAG-based code search across the LINA repository.';
COMMENT ON COLUMN repo_index.language IS
    'Detected programming language (py, rs, js, yaml, ...).';
COMMENT ON COLUMN repo_index.last_commit IS
    'Git commit hash when this chunk was last indexed.';
COMMENT ON COLUMN repo_index.chunk_index IS
    'Position of this chunk within the file (0-based).';
COMMENT ON COLUMN repo_index.total_chunks IS
    'Total number of chunks this file was split into.';
