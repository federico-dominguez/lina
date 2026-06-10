-- Migration 016: session_logs — unified append-only log for all agent activity
--
-- Unifies user messages, assistant responses, thinking traces, tool calls,
-- shell commands, and model metadata into a single queryable table.
--
-- Design:
--   • Append-only — no UPDATE, no DELETE (audit trail)
--   • denormalized on purpose: single table, no JOINs for common queries
--   • JSONB for tool_args (flexible, searchable with jsonb_path_exists)
--   • GIN indexes on tags and tool_args for fast filtering
--   • Full-text search via tsvector on msg_text + thinking_text
--
-- Migration: 016 (additive, safe to re-run)

-- ─── session_logs ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lina.session_logs (
    id              BIGSERIAL       PRIMARY KEY,
    session_id      TEXT            NOT NULL,
    turn_number     INTEGER         NOT NULL DEFAULT 0,
    ts              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Quién y qué rol
    role            TEXT            NOT NULL DEFAULT 'system'
                    CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    sender          TEXT            NOT NULL DEFAULT 'system'
                    CHECK (sender IN ('fede', 'goose', 'lina', 'cline', 'gemma', 'system', 'unknown')),

    -- Contenido textual
    msg_text        TEXT,                       -- mensaje del usuario o respuesta del asistente
    thinking_text   TEXT,                       -- bloque <think> del asistente

    -- Tool call info
    tool_name       TEXT,                       -- 'shell' | 'edit' | 'lina-db/searchMemory' | etc
    tool_args       JSONB,                      -- argumentos completos del tool (JSON)
    tool_result     TEXT,                       -- resultado/return del tool (truncado si >10 KB)

    -- Modelo
    model           TEXT,                       -- 'deepseek-v4-flash' | 'deepseek-v4-pro'

    -- Tags para búsqueda rápida
    tags            TEXT[]          NOT NULL DEFAULT '{}',

    -- Costo (opcional, estimado)
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    cost_usd        NUMERIC(14, 8)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_slog_session     ON lina.session_logs (session_id, turn_number DESC);
CREATE INDEX IF NOT EXISTS idx_slog_ts          ON lina.session_logs (ts DESC);
CREATE INDEX IF NOT EXISTS idx_slog_role        ON lina.session_logs (role);
CREATE INDEX IF NOT EXISTS idx_slog_sender      ON lina.session_logs (sender);
CREATE INDEX IF NOT EXISTS idx_slog_tool_name   ON lina.session_logs (tool_name);
CREATE INDEX IF NOT EXISTS idx_slog_tags        ON lina.session_logs USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_slog_tool_args   ON lina.session_logs USING GIN (tool_args jsonb_path_ops);

-- Full-text search: combina msg_text + thinking_text en un solo índice tsvector
CREATE INDEX IF NOT EXISTS idx_slog_fts
    ON lina.session_logs
    USING gin(to_tsvector('spanish', COALESCE(msg_text, '') || ' ' || COALESCE(thinking_text, '')));

COMMENT ON TABLE lina.session_logs IS
    'Log unificado append-only de toda la actividad de Goose. Sesiones, mensajes, thinking, tool calls y shell commands.';

COMMENT ON COLUMN lina.session_logs.role IS
    'Rol del emisor: user, assistant, system, tool';
COMMENT ON COLUMN lina.session_logs.sender IS
    'Quién emite: fede, goose, lina, cline, gemma, system, unknown';
COMMENT ON COLUMN lina.session_logs.msg_text IS
    'Texto del mensaje (user message o assistant response)';
COMMENT ON COLUMN lina.session_logs.thinking_text IS
    'Contenido del bloque <think> del asistente';
COMMENT ON COLUMN lina.session_logs.tool_name IS
    'Nombre del tool MCP llamado (ej: shell, edit, lina-db/searchMemory)';
COMMENT ON COLUMN lina.session_logs.tool_args IS
    'Argumentos completos del tool call en JSON';
COMMENT ON COLUMN lina.session_logs.tool_result IS
    'Resultado del tool call (truncado a ~10 KB si excede)';
COMMENT ON COLUMN lina.session_logs.tags IS
    'Tags para filtrar y agrupar (ej: {moodle, error, critical})';
COMMENT ON COLUMN lina.session_logs.tokens_in IS
    'Tokens de entrada estimados (chars/3)';
COMMENT ON COLUMN lina.session_logs.tokens_out IS
    'Tokens de salida estimados (chars/3)';
COMMENT ON COLUMN lina.session_logs.cost_usd IS
    'Costo estimado en USD (precio al momento de escritura)';
