-- Migration 011: dead_letter queue, retry backoff, pg_notify on status change
-- Issue #89: Subagentes pueden colgarse, crashear, o quedar en estado raro.
-- Necesitamos manejo robusto: timeout, restart con backoff, DLQ, propagación de kill.

-- ─── 1. Agregar campos de reintento a agent_sessions ─────────────────────────

ALTER TABLE agent_sessions
    ADD COLUMN IF NOT EXISTS retry_count    INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_error     TEXT,
    ADD COLUMN IF NOT EXISTS retry_at       TIMESTAMPTZ,   -- próxima fecha para reintentar
    ADD COLUMN IF NOT EXISTS watchdog_note  TEXT;          -- razón del último timeout/kill

COMMENT ON COLUMN agent_sessions.retry_count IS
    'Contador de reintentos automáticos (issue #89). 0 = primer intento.';
COMMENT ON COLUMN agent_sessions.last_error IS
    'Mensaje de error del último fallo (timeout, crash, etc.).';
COMMENT ON COLUMN agent_sessions.retry_at IS
    'Timestamp UTC del próximo reintento programado (NULL si no hay).';
COMMENT ON COLUMN agent_sessions.watchdog_note IS
    'Razón del último timeout/kill por parte del watchdog (issue #89).';

-- ─── 2. Tabla dead_letter ────────────────────────────────────────────────────
-- Agentes que fallaron >3 veces y no serán reintentados automáticamente.

CREATE TABLE IF NOT EXISTS dead_letter (
    id              BIGSERIAL    PRIMARY KEY,
    session_id      TEXT         NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    role            TEXT         NOT NULL,
    goal            TEXT,
    retry_count     INTEGER      NOT NULL,
    last_error      TEXT,
    moved_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dl_moved ON dead_letter (moved_at DESC);

COMMENT ON TABLE dead_letter IS
    'Sub-agentes que excedieron el máximo de reintentos (issue #89)';
COMMENT ON COLUMN dead_letter.moved_at IS
    'Momento en que el agente fue movido a la cola de fallos.';

-- ─── 3. Vista unificada de agentes fallidos ──────────────────────────────────
-- Útil para monitoreo y dashboard /agents.

CREATE OR REPLACE VIEW failed_agents AS
SELECT
    s.id AS session_id,
    s.role,
    s.goal,
    s.status,
    s.retry_count,
    s.last_error,
    s.watchdog_note,
    s.started_at,
    s.ended_at,
    dl.moved_at AS dead_letter_at
FROM agent_sessions s
LEFT JOIN dead_letter dl ON dl.session_id = s.id
WHERE s.status IN ('failed', 'killed', 'timeout')
ORDER BY s.ended_at DESC NULLS LAST;

COMMENT ON VIEW failed_agents IS
    'Vista de agentes fallidos con info de dead-letter (issue #89).';

-- ─── 4. Trigger pg_notify cuando un agente cambia a failed/killed/timeout ─────
-- Permite que LINA reciba notificaciones en tiempo real sin polling.

CREATE OR REPLACE FUNCTION agent_status_change_notify()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status IN ('failed', 'killed', 'timeout') AND
       (OLD IS NULL OR OLD.status NOT IN ('failed', 'killed', 'timeout')) THEN
        PERFORM pg_notify(
            'agent_status_change',
            json_build_object(
                'session_id', NEW.id,
                'role', NEW.role,
                'status', NEW.status,
                'goal', LEFT(NEW.goal, 100),
                'retry_count', NEW.retry_count,
                'last_error', NEW.last_error
            )::text
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_agent_status_change_notify ON agent_sessions;

CREATE TRIGGER trg_agent_status_change_notify
    AFTER UPDATE OF status ON agent_sessions
    FOR EACH ROW
    WHEN (NEW.status IN ('failed', 'killed', 'timeout'))
    EXECUTE FUNCTION agent_status_change_notify();

COMMENT ON FUNCTION agent_status_change_notify IS
    'Notifica via pg_notify cuando un agente pasa a failed/killed/timeout (issue #89).';
