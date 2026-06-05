-- LINA — Migration 013: Circuit Breaker (Fase 3 — Resiliencia)
-- Issue #173: Circuit breaker por bot para aislar bots fallando
--
-- Estados: closed → open (5 fallos) → half-open (30s) → closed/open

CREATE TABLE IF NOT EXISTS circuit_breaker (
    bot_name        TEXT PRIMARY KEY,
    state           TEXT NOT NULL DEFAULT 'closed'
                        CHECK (state IN ('closed', 'open', 'half-open')),
    failures        INT NOT NULL DEFAULT 0,
    last_failure    TIMESTAMPTZ,
    opened_at       TIMESTAMPTZ,
    half_open_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Trigger: updated_at
CREATE OR REPLACE FUNCTION update_circuit_breaker_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_circuit_breaker_timestamp ON circuit_breaker;
CREATE TRIGGER trg_circuit_breaker_timestamp
    BEFORE UPDATE ON circuit_breaker
    FOR EACH ROW
    EXECUTE FUNCTION update_circuit_breaker_timestamp();

-- Notify cuando un circuito se abre
CREATE OR REPLACE FUNCTION notify_circuit_opened()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.state = 'open' AND OLD.state != 'open' THEN
        PERFORM pg_notify('lina_circuit_breaker', json_build_object(
            'bot', NEW.bot_name,
            'state', NEW.state,
            'failures', NEW.failures,
            'opened_at', NEW.opened_at
        )::TEXT);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_notify_circuit_opened ON circuit_breaker;
CREATE TRIGGER trg_notify_circuit_opened
    AFTER UPDATE OF state ON circuit_breaker
    FOR EACH ROW
    WHEN (NEW.state = 'open')
    EXECUTE FUNCTION notify_circuit_opened();
