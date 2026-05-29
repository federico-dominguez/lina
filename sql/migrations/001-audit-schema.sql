-- LINA — Migración 001: audit schema (Fase 3 hardening)
-- Aplica a instancias existentes con la tabla audit_logs en el public schema.
-- Ejecutar manualmente: psql $LINA_DB_URL -f sql/migrations/001-audit-schema.sql
--
-- Idempotente: se puede ejecutar múltiples veces sin efecto adicional.

BEGIN;

-- 1. Crear esquema audit y tabla tool_calls (idempotente)
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS audit.tool_calls (
    id              BIGSERIAL   PRIMARY KEY,
    mcp             TEXT        NOT NULL DEFAULT 'lina-db',
    tool            TEXT        NOT NULL,
    args_json       JSONB,
    result_summary  TEXT,
    duration_ms     INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_tc_created ON audit.tool_calls (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_tc_tool    ON audit.tool_calls (tool);
CREATE INDEX IF NOT EXISTS idx_audit_tc_mcp     ON audit.tool_calls (mcp);

-- 2. Migrar datos de audit_logs → audit.tool_calls (sólo si la tabla existe)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'audit_logs'
          AND table_type = 'BASE TABLE'
    ) THEN
        INSERT INTO audit.tool_calls (tool, args_json, result_summary, created_at)
        SELECT tool, args_json, result_summary, created_at
        FROM   public.audit_logs
        ON CONFLICT DO NOTHING;

        -- Reemplazar la tabla por vista de compatibilidad
        DROP TABLE IF EXISTS public.audit_logs;
    END IF;
END;
$$;

-- 3. Vista de compatibilidad hacia atrás
CREATE OR REPLACE VIEW public.audit_logs AS
SELECT id, tool, args_json, result_summary, created_at
FROM   audit.tool_calls;

-- 4. Vista daily_summary
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

COMMIT;
