# 🧪 Tests E2E del Sistema Comm — Estándar Oficial

## Filosofía

> **Todo test E2E sobre el comportamiento de los bots debe usar el sistema Comm como canal único.**
> No más `send_message` directo a bots en chat privado. No más HTTP directo a gateways.
> El canal oficial de testing es el **grupo Comm** vía la **cuenta Comm (+59891992356)**.

## Arquitectura de Testing

```
┌──────────────────────┐     ┌──────────────────┐     ┌──────────────────────┐
│  CommTestClient      │     │  comm_messages   │     │  Grupo Comm          │
│  (cuenta Comm)       │────→│  (DB)            │────→│  (+59891992356)      │
│                      │     │                  │     │                      │
│  comm_harness.py     │     │  comm-svc polls  │     │  @s_lina_bot         │
│                      │←────│  y entrega       │←────│  @s_goose_bot        │
│                      │     │                  │     │  @s_cline_bot        │
│                      │     │                  │     │  @s_gemma_bot        │
└──────────────────────┘     └──────────────────┘     └──────────────────────┘
```

## Flujo de un Test E2E

1. **`CommTestClient.send_to(bot, mensaje)`** — inserta en `comm_messages` (DB)
2. **`comm-svc.service`** detecta el INSERT y envía al grupo Telegram:
   `"s_comm_test_bot says: @s_lina_bot <mensaje>"`
3. **El bot destino** recibe en el grupo, procesa y responde insertando en `comm_messages`
4. **`comm-svc.service`** entrega la respuesta al grupo
5. **`CommTestClient.wait_response()`** captura los mensajes del bot en el grupo y devuelve el texto completo

## Instalación

```bash
# 1. Instalar dependencias
pip install -r tests/e2e/telegram/requirements-e2e.txt
# o con uv:
cd tests/e2e/telegram && uv pip install -r requirements-e2e.txt

# 2. Autenticar la cuenta Comm (solo la primera vez)
cd tests/e2e/comm && python3 -c "
from comm_harness import CommTestClient
import asyncio
async def auth():
    c = CommTestClient()
    await c.start()
    print('✅ Autenticado como:', c.me.first_name)
    await c.stop()
asyncio.run(auth())
"
# → Te pedirá el código SMS la primera vez
```

## Variables de Entorno

| Variable | Default | Descripción |
|---|---|---|
| `COMM_API_ID` | `35434942` | API ID de la cuenta Comm (Telegram) |
| `COMM_API_HASH` | `9f2a614fbf2e8cbfaf844561b7f43294` | API Hash de la cuenta Comm |
| `COMM_PHONE` | `+59891992356` | Número de la cuenta Comm |
| `COMM_SESSION_DIR` | `tests/e2e/comm/.sessions` | Directorio de sesiones Telethon |
| `LINA_DB_URL` | `postgresql://lina:lina_dev@localhost:5432/lina` | Conexión a PostgreSQL |
| `COMM_E2E_TIMEOUT` | `120` | Timeout máximo por test (segundos) |
| `COMM_E2E_SILENCE` | `8.0` | Segundos sin mensajes = respuesta completa |

## CommTestClient API

### `CommTestClient()`

Cliente oficial para tests E2E vía Comm.

#### Métodos Principales

```python
async def start() -> None
    """Conecta y autentica la cuenta Comm en Telegram.
    Crea el grupo si no existe aún."""

async def stop() -> None
    """Desconecta el cliente."""

async def send_to(bot: str, message: str) -> int
    """Envía mensaje a un bot vía comm_messages DB.
    
    Args:
        bot: Nombre del bot ('lina', 'goose', 'cline', 'gemma', 'fede', 'todos')
        message: Texto del mensaje
    
    Returns:
        int: ID del mensaje en comm_messages (para trackear respuesta)
    """

async def wait_response(
    msg_id: int,
    timeout: float = 120.0,
    silence: float = 8.0,
    from_bot: str | None = None
) -> CommResponse
    """Espera la respuesta completa del bot.
    
    Usa SILENCE TIMEOUT: cuando pasan `silence` segundos sin 
    mensajes nuevos del bot destino, se considera que terminó.
    
    Args:
        msg_id: ID del mensaje enviado (de send_to)
        timeout: Timeout absoluto en segundos
        silence: Segundos de silencio = respuesta completa
        from_bot: Filtrar respuestas de un bot específico
        
    Returns:
        CommResponse con texto, mensajes, métricas
    """

async def send_and_wait(
    bot: str,
    message: str,
    timeout: float = 120.0,
    silence: float = 8.0
) -> CommResponse
    """Atajo: send_to + wait_response en un solo paso."""

async def send_raw(text: str) -> int
    """Envía texto RAW al grupo (sin @mention, para tests del floor token).
    
    Returns:
        int: ID del mensaje en comm_messages
    """

async def get_group_id() -> int | None
    """Detecta y devuelve el ID del grupo Comm."""
```

### `CommResponse`

```python
@dataclass
class CommResponse:
    messages: list[CapturedMessage]  # Todos los mensajes capturados
    text: str                        # Texto completo concatenado
    msg_id: int                      # ID del mensaje original
    ttft: float | None               # Time to first token (segundos)
    ttlt: float | None               # Time to last token (segundos)
    bot_responses: int               # Cantidad de bots que respondieron
    from_bots: list[str]             # Lista de bots que respondieron
    
    def contains(self, text: str) -> bool
    def has_any(self, *texts: str) -> bool
    def summary(self) -> str
```

## Fixtures pytest Disponibles

```python
# En tests/e2e/comm/conftest.py

@pytest.fixture
async def comm() -> CommTestClient:
    """Cliente Comm autenticado y listo para usar.
    
    Uso:
        async def test_algo(comm):
            resp = await comm.send_and_wait("lina", "Hola!")
            assert resp.contains("Hola")
    """

@pytest.fixture
def comm_timeout() -> float:
    """Timeout personalizable por test. Default: 120s."""

@pytest.fixture
def comm_silence() -> float:
    """Ventana de silencio personalizable. Default: 8s."""
```

## Escenarios de Test

### Nivel 1 — Smoke Tests (< 30s c/u)

| Test | Descripción | Comando |
|---|---|---|
| `test_ping_lina` | Enviar "ping" a LINA → esperar respuesta | `@s_lina_bot ping` |
| `test_ping_goose` | Enviar "ping" a Goose → esperar respuesta | `@s_goose_bot ping` |
| `test_ping_cline` | Enviar "ping" a Cline → esperar respuesta | `@s_cline_bot ping` |
| `test_ping_gemma` | Enviar "ping" a Gemma → esperar respuesta | `@s_gemma_bot ping` |

### Nivel 2 — Functional Tests (< 120s c/u)

| Test | Descripción |
|---|---|
| `test_floor_token_basic` | 2 bots conversan alternándose sin pisarse |
| `test_delegation_lina_goose` | LINA delega tarea simple a Goose |
| `test_mention_routing` | Mensaje con @mención llega al bot correcto |

### Nivel 3 — Resilience Tests (< 300s c/u)

| Test | Descripción |
|---|---|
| `test_floor_timeout` | Timeout expira → token pasa al siguiente bot |
| `test_context_accumulation` | Contexto incluye últimos N mensajes |
| `test_queue_fifo` | Mensajes pendientes se procesan en orden |

## Ejecución

```bash
# Todos los tests Comm
pytest tests/e2e/comm/ -v

# Un test específico
pytest tests/e2e/comm/test_ping.py -v

# Con timeout aumentado (para tests lentos)
COMM_E2E_TIMEOUT=300 pytest tests/e2e/comm/ -v

# Reporte JSON
pytest tests/e2e/comm/ --json-report --json-report-file=comm-report.json
```

## Arquitectura de Archivos

```
tests/e2e/comm/
├── README.md              ← Este archivo (el estándar)
├── conftest.py            ← Fixtures pytest globales
├── comm_harness.py        ← CommTestClient oficial
├── .sessions/             ← Sesiones Telethon (gitignored)
├── .gitignore             ← Ignora .sessions/
├── test_ping.py           ← Smoke tests (ping a cada bot)
├── test_floor_token.py    ← Tests de protocolo conversacional
└── test_delegation.py     ← Tests de delegación entre bots
```

## Reglas de Oro

1. **Siempre usar `send_to()`** — nunca mandar mensajes directo por Telegram
2. **Siempre esperar respuesta con `wait_response()`** — nunca usar `sleep()` fijo
3. **Usar `contains()` / `has_any()`** para validar — no comparar strings exactas
4. **Timeouts generosos** — los bots pueden tardar (LINA es reflexiva)
5. **Un test por escenario** — no meter 3 escenarios en un solo test
6. **Marcar tests lentos** con `@pytest.mark.slow` (se saltean por defecto)
