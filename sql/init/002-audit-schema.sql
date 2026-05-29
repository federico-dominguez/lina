-- LINA — Fase 3: esquema de auditoría dedicado
-- Ejecutado automáticamente por postgres:16-alpine al inicializar el volumen.
-- (Sólo se ejecuta en instalaciones nuevas; para migraciones ver sql/migrations/)

-- ─── Esquema audit ───────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS audit;

-- Tabla principal de auditoría: una fila por tool call en cualquier MCP.
-- tool_calls reemplaza a la tabla audit_logs del public schema (Fase 2).
CREATE TABLE IF NOT EXISTS audit.tool_calls (
    id              BIGSERIAL   PRIMARY KEY,
    mcp             TEXT        NOT NULL DEFAULT 'lina-db', -- origen del call
    tool            TEXT        NOT NULL,
    args_json       JSONB,
    result_summary  TEXT,
    duration_ms     INT,        -- duración de la llamada en ms (opcional)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_tc_created ON audit.tool_calls (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_tc_tool    ON audit.tool_calls (tool);
CREATE INDEX IF NOT EXISTS idx_audit_tc_mcp     ON audit.tool_calls (mcp);

-- ─── Vista: resumen diario de costos/uso (3.5) ───────────────────────────────
-- Disponible como audit.daily_summary; expuesta via get_daily_summary tool.
CREATE OR REPLACE VIEW audit.daily_summary AS
SELECT
    date_trunc('day', created_at AT TIME ZONE 'America/Montevideo') AS day,
    mcp,
    tool,
    count(*)                                                          AS calls,
    round(avg(duration_ms))                                           AS avg_ms,
    max(duration_ms)                                                  AS max_ms
FROM audit.tool_calls
GROUP BY 1, 2, 3
ORDER BY 1 DESC, 4 DESC;

-- ─── Compatibilidad hacia atrás: alias público ───────────────────────────────
-- audit_logs en el schema public se mantiene como vista para no romper
-- consultas directas SQL de sesiones anteriores.
CREATE OR REPLACE VIEW public.audit_logs AS
SELECT
    id,
    tool,
    args_json,
    result_summary,
    created_at
FROM audit.tool_calls;
