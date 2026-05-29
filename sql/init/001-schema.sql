-- LINA — PostgreSQL schema inicial
-- Ejecutado automáticamente por postgres:16-alpine al inicializar el volumen.

-- ─── memories ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS memories (
    id          SERIAL PRIMARY KEY,
    key         TEXT        NOT NULL UNIQUE,
    value       TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ              -- NULL = sin expiración
);

CREATE INDEX IF NOT EXISTS idx_memories_key      ON memories (key);
CREATE INDEX IF NOT EXISTS idx_memories_expires  ON memories (expires_at)
    WHERE expires_at IS NOT NULL;

-- ─── sessions ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id          SERIAL PRIMARY KEY,
    session_id  TEXT        NOT NULL,
    summary     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions (created_at DESC);

-- ─── preferences ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS preferences (
    id          SERIAL PRIMARY KEY,
    key         TEXT        NOT NULL UNIQUE,
    value       TEXT        NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── audit_logs ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    id              SERIAL PRIMARY KEY,
    tool            TEXT        NOT NULL,
    args_json       JSONB,
    result_summary  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_tool    ON audit_logs (tool);
