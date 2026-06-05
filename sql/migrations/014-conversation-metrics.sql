-- LINA — Migration 014: Conversation Metrics (Fase 3 — Observabilidad)
-- Issue #173: Métricas de latencia, tokens y turnos por conversación

CREATE TABLE IF NOT EXISTS conversation_metrics (
    id              SERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL,
    bot_name        TEXT NOT NULL,
    latency_ms      INT,         -- tiempo de respuesta en ms
    tokens          INT,         -- tokens usados en la respuesta
    turn_number     INT,         -- número de turno en la conversación
    intent_category TEXT,        -- categoría detectada por orchestrator
    routed          BOOLEAN DEFAULT FALSE,  -- si fue ruteado a otro bot
    error           BOOLEAN DEFAULT FALSE,  -- si hubo error
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_metrics_conversation ON conversation_metrics(conversation_id);
CREATE INDEX IF NOT EXISTS idx_metrics_bot ON conversation_metrics(bot_name);
CREATE INDEX IF NOT EXISTS idx_metrics_created ON conversation_metrics(created_at DESC);

-- Vista: resumen de métricas por bot
CREATE OR REPLACE VIEW bot_metrics_summary AS
SELECT
    bot_name,
    COUNT(*) AS total_requests,
    AVG(latency_ms)::INT AS avg_latency_ms,
    SUM(tokens) AS total_tokens,
    AVG(tokens)::INT AS avg_tokens,
    COUNT(*) FILTER (WHERE error) AS errors,
    COUNT(*) FILTER (WHERE routed) AS routed_count,
    MIN(created_at) AS first_seen,
    MAX(created_at) AS last_seen
FROM conversation_metrics
GROUP BY bot_name;
