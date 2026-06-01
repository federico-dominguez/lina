-- Migration 009: agent_sessions, agent_events, agent_commands
-- Control plane persistente para sub-agentes de LINA (issues #83, #84, #85).
--
-- Diseño:
--   agent_sessions  — ciclo de vida de cada sub-proceso goosed
--   agent_events    — log inmutable de lo que hizo un agente (append-only)
--   agent_commands  — buzón de instrucciones: LINA → sub-agente (polling)

-- ─── agent_sessions ──────────────────────────────────────────────────────────
-- Una fila por sub-agente spawneado. `pid` es el PID del proceso goosed hijo.
-- `status` sigue la máquina de estados:
--   pending → running → (completed | failed | killed | timeout)
CREATE TABLE IF NOT EXISTS agent_sessions (
    id              TEXT        PRIMARY KEY,          -- UUID asignado al spawn
    role            TEXT        NOT NULL,             -- dev / ops / study / research
    goal            TEXT        NOT NULL,             -- instrucción original
    status          TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','completed','failed','killed','timeout')),
    pid             INTEGER,                          -- PID del proceso goosed hijo (NULL hasta arrancar)
    config_path     TEXT,                             -- ruta al goose config temporal filtrado por rol
    result_summary  TEXT,                             -- resumen final (NULL hasta completar)
    started_at      TIMESTAMPTZ,                      -- cuando el proceso arrancó (NULL hasta running)
    ended_at        TIMESTAMPTZ,                      -- cuando terminó (NULL si sigue vivo)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ases_status    ON agent_sessions (status);
CREATE INDEX IF NOT EXISTS idx_ases_role      ON agent_sessions (role);
CREATE INDEX IF NOT EXISTS idx_ases_created   ON agent_sessions (created_at DESC);

COMMENT ON TABLE agent_sessions IS
    'Ciclo de vida de sub-agentes goosed spawneados por el orquestador (issue #84).';
COMMENT ON COLUMN agent_sessions.id IS
    'UUID hexadecimal (sin guiones) asignado al crear la sesión.';
COMMENT ON COLUMN agent_sessions.pid IS
    'PID del proceso goosed hijo en el host. NULL si aún no arrancó o ya terminó.';

-- ─── agent_events ─────────────────────────────────────────────────────────────
-- Log append-only de eventos de un sub-agente. Nunca se actualiza ni borra.
-- kind: spawn_requested | process_started | process_ended | heartbeat |
--       tool_called | error | instruction_received | instruction_ack
CREATE TABLE IF NOT EXISTS agent_events (
    id              BIGSERIAL   PRIMARY KEY,
    session_id      TEXT        NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    kind            TEXT        NOT NULL,
    payload_json    JSONB,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aevt_session   ON agent_events (session_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_aevt_kind      ON agent_events (kind);

COMMENT ON TABLE agent_events IS
    'Eventos inmutables de sub-agentes. Append-only — nunca UPDATE/DELETE.';

-- ─── agent_commands ───────────────────────────────────────────────────────────
-- Buzón de instrucciones: LINA principal escribe, sub-agente lee por polling.
-- El sub-agente marca ack_at cuando procesa el comando.
-- kind: pause | resume | kill | send_instruction | set_budget
CREATE TABLE IF NOT EXISTS agent_commands (
    id              BIGSERIAL   PRIMARY KEY,
    session_id      TEXT        NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    kind            TEXT        NOT NULL,
    args_json       JSONB,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ack_at          TIMESTAMPTZ             -- NULL = pendiente de ACK
);

CREATE INDEX IF NOT EXISTS idx_acmd_session   ON agent_commands (session_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_acmd_pending   ON agent_commands (session_id, ack_at)
    WHERE ack_at IS NULL;

COMMENT ON TABLE agent_commands IS
    'Buzón de instrucciones LINA→sub-agente. Sub-agente hace polling y escribe ack_at.';

-- ─── NOTIFY trigger para agent_commands ──────────────────────────────────────
-- Cuando se inserta un comando, notifica al canal 'agent_commands_<session_id>'
-- para que el sub-agente (si está escuchando con LISTEN) lo reciba sin polling.
CREATE OR REPLACE FUNCTION agent_commands_notify() RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify(
        'agent_cmd_' || NEW.session_id,
        json_build_object('id', NEW.id, 'kind', NEW.kind)::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_agent_commands_notify ON agent_commands;
CREATE TRIGGER trg_agent_commands_notify
    AFTER INSERT ON agent_commands
    FOR EACH ROW EXECUTE FUNCTION agent_commands_notify();

-- ─── Vista de agentes activos ─────────────────────────────────────────────────
CREATE OR REPLACE VIEW agent_sessions_active AS
SELECT
    id,
    role,
    goal,
    status,
    pid,
    started_at,
    EXTRACT(EPOCH FROM (NOW() - started_at))::INT AS elapsed_seconds
FROM agent_sessions
WHERE status IN ('pending', 'running')
ORDER BY created_at DESC;

COMMENT ON VIEW agent_sessions_active IS
    'Agentes en estado pending o running — para /agents dashboard de Telegram.';
