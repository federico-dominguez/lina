-- Fase 1 — Protocolo Conversacional: Token de turno (floor token)
-- ADR-0138 Vecindario de Agentes
-- Crea las tablas conversation_floor y conversation_messages

-- ─── conversation_floor ─────────────────────────────────────────────────────
-- Token de turno: qué bot tiene la palabra y hasta cuándo.
-- Solo puede haber UNA fila activa por conversation_id (floor activo).
CREATE TABLE IF NOT EXISTS conversation_floor (
    id              SERIAL PRIMARY KEY,
    active_bot      TEXT        NOT NULL,           -- 'lina', 'cline', 'goose'
    conversation_id UUID        NOT NULL,           -- grupo/chat UUID
    acquired_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,           -- acquired_at + timeout
    released_at     TIMESTAMPTZ,                    -- NULL = aún activo
    release_reason  TEXT                            -- 'timeout' | 'voluntary' | 'preempted'
);

-- Índice para encontrar rápido el floor activo de una conversación
CREATE UNIQUE INDEX IF NOT EXISTS idx_floor_active
    ON conversation_floor (conversation_id)
    WHERE released_at IS NULL;

-- ─── conversation_messages ──────────────────────────────────────────────────
-- Cola FIFO de mensajes entre bots dentro de una conversación.
CREATE TABLE IF NOT EXISTS conversation_messages (
    id              SERIAL PRIMARY KEY,
    conversation_id UUID        NOT NULL,
    from_bot        TEXT        NOT NULL,           -- remitente
    to_bot          TEXT        NOT NULL,           -- destinatario
    message         TEXT        NOT NULL,           -- contenido del mensaje
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at    TIMESTAMPTZ,                    -- NULL = pendiente
    ack_at          TIMESTAMPTZ                     -- NULL = no acusado
);

-- Índice para sacar mensajes pendientes (FIFO)
CREATE INDEX IF NOT EXISTS idx_messages_pending
    ON conversation_messages (conversation_id, created_at ASC)
    WHERE processed_at IS NULL;

-- Índice para contexto acumulativo: últimos N mensajes de una conversación
CREATE INDEX IF NOT EXISTS idx_messages_recent
    ON conversation_messages (conversation_id, created_at DESC);
