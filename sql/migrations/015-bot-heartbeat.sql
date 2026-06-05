-- LINA — Migration 012: Bot Heartbeat (Fase 3 — Resiliencia)
-- Issue #173: Heartbeat de bots cada 30s con TTL de 60s
--
-- Tabla: cada bot escribe su pulso periódicamente.
-- Si un bot no actualiza su pulso por > 60s, se considera caído.

CREATE TABLE IF NOT EXISTS bot_heartbeat (
    bot_name        TEXT PRIMARY KEY,
    last_pulse      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status          TEXT NOT NULL DEFAULT 'alive'
                        CHECK (status IN ('alive', 'degraded', 'dead')),
    session_id      TEXT,
    failures        INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Trigger: actualizar updated_at automáticamente
CREATE OR REPLACE FUNCTION update_heartbeat_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_heartbeat_timestamp ON bot_heartbeat;
CREATE TRIGGER trg_heartbeat_timestamp
    BEFORE UPDATE ON bot_heartbeat
    FOR EACH ROW
    EXECUTE FUNCTION update_heartbeat_timestamp();

-- Función: marcar bots como dead si no pulsean
CREATE OR REPLACE FUNCTION expire_stale_heartbeats()
RETURNS TABLE(bot_name TEXT, old_status TEXT, new_status TEXT) AS $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        UPDATE bot_heartbeat
        SET status = 'dead'
        WHERE last_pulse < NOW() - INTERVAL '60 seconds'
          AND status != 'dead'
        RETURNING bot_heartbeat.bot_name, 'alive'::TEXT, 'dead'::TEXT
    LOOP
        bot_name := r.bot_name;
        old_status := 'alive';
        new_status := 'dead';
        RETURN NEXT;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Notify cuando un bot cambia a dead
CREATE OR REPLACE FUNCTION notify_bot_dead()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'dead' AND OLD.status != 'dead' THEN
        PERFORM pg_notify('lina_heartbeat', json_build_object(
            'bot', NEW.bot_name,
            'status', NEW.status,
            'last_pulse', NEW.last_pulse
        )::TEXT);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_notify_bot_dead ON bot_heartbeat;
CREATE TRIGGER trg_notify_bot_dead
    AFTER UPDATE OF status ON bot_heartbeat
    FOR EACH ROW
    WHEN (NEW.status = 'dead')
    EXECUTE FUNCTION notify_bot_dead();
