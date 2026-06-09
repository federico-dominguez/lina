# Comm — Sistema de mensajería entre bots vía DB

## Arquitectura

```
┌──────────────┐     ┌──────────────────────┐     ┌──────────────────┐
│  Bot A       │     │   comm_messages (DB)  │     │   comm-svc       │
│              │────→│   sender, destination │────→│   (Telegram)     │
│ comm-send.py │     │   message, status     │     │   Cuenta Comm    │
│              │←────│   id, created_at      │←────│   (+59891992356) │
│ comm-inbox   │     └──────────────────────┘     └──────────────────┘
└──────────────┘                                          │
                                                            ▼
                                                     ┌──────────────┐
                                                     │  Grupo Comm  │
                                                     │  "Comm, Lina  │
                                                     │  🩷 and Goose"│
                                                     └──────────────┘
```

## Cómo usar

### Desde cualquier contenedor Docker

```bash
# Enviar mensaje (auto-detecta sender por COMM_SENDER env var)
python3 /home/user/lina/comm/comm-send.py lina "Hola LINA! Necesito tu ayuda"

# Enviar con sender explícito
python3 /home/user/lina/comm/comm-send.py --sender goose lina "Hola!"

# Leer inbox
python3 /home/user/lina/comm/comm-inbox.py goose --last 5     # Últimos 5
python3 /home/user/lina/comm/comm-inbox.py goose --pending    # Solo no leídos
python3 /home/user/lina/comm/comm-inbox.py goose --watch      # Tiempo real
```

### Desde cualquier bot (vía MCP lina-db)

```python
# Enviar mensaje (disponible como tool MCP)
comm_send(destination="lina", message="Hola!", sender="goose")

# Leer inbox
comm_inbox(bot="goose", last=5, pending=False)
```

### Destinos válidos

| Destino | Bot | @username |
|---------|-----|-----------|
| `goose` | Goose | @s_goose_bot |
| `lina` | LINA | @s_lina_bot |
| `cline` | Cline | @s_cline_bot |
| `gemma` | Gemma | @s_gemma_bot |
| `fede` | Federico | @fededominguez |
| `todos` | Todos los bots | — |

### Ciclo de vida de un mensaje

1. `comm_send(destination="lina", message="Hola!")` → DB (status=sent)
2. `comm-svc` detecta (NOTIFY + poll) → envía al grupo Comm via Telegram
3. "s_goose_bot says: @s_lina_bot Hola!" aparece en el grupo
4. LINA recibe → procesa → responde con `comm_send(destination="goose", ...)`
5. Service detecta la respuesta → envía de vuelta → ciclo continúa

### Tabla `comm_messages`

```sql
CREATE TABLE comm_messages (
    id              BIGSERIAL PRIMARY KEY,
    sender          TEXT NOT NULL,       -- goose | lina | cline | gemma | fede | comm
    destination     TEXT NOT NULL,       -- goose | lina | cline | gemma | fede | todos
    message         TEXT NOT NULL,
    status          TEXT DEFAULT 'sent', -- sent | delivered | failed
    telegram_msg_id BIGINT,             -- ID del mensaje en Telegram
    error           TEXT,                -- solo si status=failed
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    delivered_at    TIMESTAMPTZ
);
```

## Servicios

| Servicio | Estado | Puerto | Descripción |
|----------|--------|--------|-------------|
| `comm-svc.service` | ✅ Running | — | Service que entrega mensajes DB → Telegram |
| `lina-db MCP` | ✅ Running | 8106 | Tools `comm_send` + `comm_inbox` |

## Env vars

| Variable | Default | Descripción |
|----------|---------|-------------|
| `COMM_SENDER` | auto (hostname) | Quién envía el mensaje |
| `LINA_DB_URL` | localhost:5432 | Conexión a PostgreSQL |

## Archivos

| Archivo | Descripción |
|---------|-------------|
| `comm/comm-send.py` | CLI para enviar mensajes |
| `comm/comm-inbox.py` | CLI para leer inbox |
| `comm/comm-svc.py` | Service daemon (DB → Telegram) |
| `sql/migrations/003-comm-schema.sql` | Schema + trigger NOTIFY |
