-- LINA — Comm: sistema de mensajería entre bots vía DB
-- Tabla única: cualquier bot escribe aquí, comm-svc envía via Telegram (cuenta Comm)
-- 
-- Uso:
--   INSERT INTO comm_messages (sender, destination, message)
--   VALUES ('goose', 'lina', 'Hola!');
--
--   comm-svc lo detecta, envía al grupo Comm:
--   "s_goose_bot says: @s_lina_bot Hola!"
--
--   LINA lo procesa, responde:
--   INSERT INTO comm_messages (sender, destination, message)
--   VALUES ('lina', 'goose', 'Todo bien!');
--
--   Y así sucesivamente.

CREATE TABLE IF NOT EXISTS comm_messages (
    id              BIGSERIAL PRIMARY KEY,
    sender          TEXT NOT NULL CHECK (sender IN ('goose','lina','cline','gemma','fede','comm')),
    destination     TEXT NOT NULL CHECK (destination IN ('goose','lina','cline','gemma','fede','todos')),
    message         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'sent' CHECK (status IN ('sent','delivered','failed')),
    telegram_msg_id BIGINT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at    TIMESTAMPTZ
);

-- Índice para polling rápido: mensajes pendientes
CREATE INDEX IF NOT EXISTS idx_comm_messages_pending
    ON comm_messages (created_at)
    WHERE status = 'sent';

-- Vista: últimos mensajes por bot
CREATE OR REPLACE VIEW comm_inbox AS
SELECT * FROM comm_messages
WHERE status = 'delivered'
ORDER BY created_at DESC;

-- NOTIFY para despertar al service en vez de poll cada 2s
CREATE OR REPLACE FUNCTION notify_comm_message()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify('comm_new_message', NEW.id::TEXT);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_comm_message_notify ON comm_messages;
CREATE TRIGGER trg_comm_message_notify
    AFTER INSERT ON comm_messages
    FOR EACH ROW
    WHEN (NEW.status = 'sent')
    EXECUTE FUNCTION notify_comm_message();
