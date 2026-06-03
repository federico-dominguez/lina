# Goosed HTTP API Reference

> **Binario**: `/home/fede/lina/bin/goosed` (symlink → `goosed-linux-amd64`, 249 MB)
> **Versión**: goose-server 1.35.0
> **Puerto**: `127.0.0.1:39591` (HTTPS, TLS self-signed, cert CN=goosed localhost)
> **Proceso actual**: PID 1914424 (lanzado por Goose Desktop, PPID 1914319)
> **Fecha relevamiento**: 2026-06-02

---

## Autenticación

**Header**: `X-Secret-Key`
**Valor**: `f7aa8124c29aa1d6c85582e241231568f9780ae8a89886e00da66138f2464ffc`

❌ `Authorization: Bearer <key>` → 401 (no funciona)
✅ `X-Secret-Key: <key>` → OK

La secret key se setea con la variable de entorno `GOOSE_SERVER__SECRET_KEY`.

---

## Endpoints probados y funcionales

### 1. GET /status
No requiere autenticación. Health check.

```bash
curl -sk https://127.0.0.1:39591/status
# Respuesta: ok
```

### 2. GET /features
Feature flags del servidor.

```bash
curl -sk -H "X-Secret-Key: $KEY" https://127.0.0.1:39591/features
```
```json
{
  "features": {
    "local-inference": false,
    "code-mode": true
  }
}
```

### 3. GET /config/providers
Lista los providers configurados.

```bash
curl -sk -H "X-Secret-Key: $KEY" https://127.0.0.1:39591/config/providers
```
```json
{
  "providers": [
    {
      "name": "custom_deepseek",
      "metadata": {
        "display_name": "custom_deepseek",
        "description": "Provider added via custom provider",
        "provider_type": "custom",
        "default_model": null,
        "known_models": []
      },
      "is_configured": true,
      "provider_type": "custom",
      "saved_model": null
    }
  ]
}
```

### 4. POST /agent/start
Crea una nueva sesión de agente. Requiere `X-Secret-Key`.

**Request:**
```json
{
  "working_dir": "/tmp"
}
```

**Response (200):**
```json
{
  "id": "20260602_18",
  "working_dir": "/tmp",
  "name": "New Chat",
  "user_set_name": false,
  "session_type": "user",
  "created_at": "2026-06-02T23:45:00Z",
  "updated_at": "2026-06-02T23:45:00Z",
  "extension_data": {
    "enabled_extensions.v0": {
      "extensions": [
        {"type":"platform","name":"analyze",...},
        {"type":"platform","name":"apps",...},
        {"type":"builtin","name":"computercontroller",...},
        {"type":"builtin","name":"developer",...},
        {"type":"stdio","name":"duckduckgo-search",...},
        {"type":"stdio","name":"gns3",...},
        {"type":"stdio","name":"lina-db",...},
        {"type":"stdio","name":"lina-fs-safe",...},
        {"type":"stdio","name":"lina-gitlab",...},
        {"type":"stdio","name":"lina-moodle",...},
        {"type":"stdio","name":"lina-secrets",...},
        {"type":"stdio","name":"lina-systemd-user",...},
        {"type":"builtin","name":"memory",...},
        {"type":"stdio","name":"Moodle UTEC",...},
        {"type":"platform","name":"skills",...},
        {"type":"platform","name":"summarize",...},
        {"type":"platform","name":"summon",...},
        {"type":"platform","name":"todo",...},
        {"type":"platform","name":"tom",...}
      ]
    }
  },
  "total_tokens": null,
  "message_count": 0,
  "goose_mode": "auto"
}
```

### 5. POST /agent/update_provider
Configura el provider LLM para la sesión. Requiere `X-Secret-Key`.

**Request:**
```json
{
  "provider": "custom_deepseek",
  "session_id": "20260602_18"
}
```

**Response:** 200 OK (body vacío)

### 6. GET /agent/tools
Lista las tools disponibles para una sesión. Requiere `X-Secret-Key`.

```bash
curl -sk -H "X-Secret-Key: $KEY" \
  "https://127.0.0.1:39591/agent/tools?session_id=20260602_18"
```

**Response (71 tools):**
```json
[
  {"name":"analyze__analyze","description":"Analyze code structure..."},
  {"name":"apps__create_app","description":"Create a new Goose app"},
  {"name":"apps__iterate_app","description":"Iterate/update an existing app"},
  {"name":"apps__list_apps","description":"List all created apps"},
  {"name":"apps__delete_app","description":"Delete an app"},
  {"name":"chatrecall__chatrecall","description":"Search past conversations..."},
  {"name":"computercontroller__automationScript","description":"..."},
  {"name":"computercontroller__cache","description":"..."},
  {"name":"computercontroller__computerControl","description":"..."},
  {"name":"computercontroller__docxTool","description":"..."},
  {"name":"computercontroller__pdfTool","description":"..."},
  {"name":"computercontroller__webScrape","description":"..."},
  {"name":"computercontroller__xlsxTool","description":"..."},
  {"name":"developer__shell","description":"Execute a shell command"},
  {"name":"developer__edit","description":"Edit a file by finding and replacing text"},
  {"name":"developer__write","description":"Create or overwrite a file"},
  {"name":"developer__tree","description":"List a directory tree"},
  {"name":"duckduckgo-search__search","description":"Search the web"},
  {"name":"duckduckgo-search__fetchContent","description":"Fetch webpage content"},
  {"name":"gns3__version","description":"..."},
  {"name":"gns3__projects","description":"..."},
  {"name":"gns3__nodes","description":"..."},
  {"name":"gns3__links","description":"..."},
  {"name":"gns3__templates","description":"..."},
  {"name":"gns3__topologySnapshot","description":"..."},
  {"name":"gns3__startNode","description":"..."},
  {"name":"gns3__stopNode","description":"..."},
  {"name":"gns3__nodeCreateFromTemplate","description":"..."},
  {"name":"gns3__nodeDelete","description":"..."},
  {"name":"gns3__linkCreate","description":"..."},
  {"name":"gns3__linkDelete","description":"..."},
  {"name":"gns3__configureClient","description":"..."},
  {"name":"gns3__pingFromClient","description":"..."},
  {"name":"gns3__addClient","description":"..."},
  {"name":"gns3__ensureVlanRouting","description":"..."},
  {"name":"gns3__runPingSuite","description":"..."},
  {"name":"gns3__openProject","description":"..."},
  {"name":"gns3__closeProject","description":"..."},
  {"name":"gns3__consoleExec","description":"..."},
  {"name":"gns3__dockerNodeExec","description":"..."},
  {"name":"gns3__recreateDockerNode","description":"..."},
  {"name":"gns3__health","description":"..."},
  {"name":"lina-db__store_memory","description":"..."},
  {"name":"lina-db__get_memory","description":"..."},
  {"name":"lina-db__search_memory","description":"..."},
  {"name":"lina-db__summarize_session","description":"..."},
  {"name":"lina-db__get_last_sessions","description":"..."},
  {"name":"lina-db__write_cline_command","description":"..."},
  {"name":"lina-db__get_cline_commands","description":"..."},
  {"name":"lina-fs-safe__read","description":"..."},
  {"name":"lina-fs-safe__write","description":"..."},
  {"name":"lina-fs-safe__list","description":"..."},
  {"name":"lina-fs-safe__delete","description":"..."},
  {"name":"lina-gitlab__list_projects","description":"..."},
  {"name":"lina-gitlab__list_issues","description":"..."},
  {"name":"lina-gitlab__create_issue","description":"..."},
  {"name":"lina-gitlab__list_merge_requests","description":"..."},
  {"name":"lina-moodle__moodleLogin","description":"..."},
  {"name":"lina-moodle__moodleGetMyCourses","description":"..."},
  {"name":"lina-moodle__moodleGetCourseContents","description":"..."},
  {"name":"lina-moodle__moodleGetQuizAttempts","description":"..."},
  {"name":"lina-moodle__moodleGetQuizAttemptData","description":"..."},
  {"name":"lina-moodle__moodleStartQuizAttempt","description":"..."},
  {"name":"lina-moodle__moodleSubmitQuizAnswer","description":"..."},
  {"name":"lina-secrets__get_secret","description":"..."},
  {"name":"lina-secrets__set_secret","description":"..."},
  {"name":"lina-secrets__list_secrets","description":"..."},
  {"name":"lina-systemd-user__list_units","description":"..."},
  {"name":"lina-systemd-user__unit_status","description":"..."},
  {"name":"lina-systemd-user__restart_unit","description":"..."},
  {"name":"memory__rememberMemory","description":"..."},
  {"name":"memory__retrieveMemories","description":"..."},
  {"name":"memory__removeMemoryCategory","description":"..."},
  {"name":"memory__removeSpecificMemory","description":"..."},
  {"name":"Moodle UTEC__moodleLogin","description":"..."},
  {"name":"Moodle UTEC__moodleGetMyCourses","description":"..."},
  {"name":"Moodle UTEC__moodleGetCourseContents","description":"..."},
  {"name":"Moodle UTEC__moodleGetQuizAttempts","description":"..."},
  {"name":"Moodle UTEC__moodleGetQuizAttemptData","description":"..."},
  {"name":"Moodle UTEC__moodleStartQuizAttempt","description":"..."},
  {"name":"Moodle UTEC__moodleSubmitQuizAnswer","description":"..."},
  {"name":"skills__load_skill","description":"..."},
  {"name":"summarize__summarize","description":"..."},
  {"name":"summon__load","description":"Load knowledge or delegate tasks"},
  {"name":"summon__delegate","description":"Delegate a task to a subagent"},
  {"name":"todo__todoWrite","description":"..."},
  {"name":"tom__get_current_tom_message","description":"..."}
]
```

### 7. POST /reply
Envía un mensaje al agente y recibe respuesta vía **Server-Sent Events (SSE)**.
Requiere `X-Secret-Key`. Content-Type: `application/json`. Accept: `text/event-stream`.

**Request:**
```json
{
  "session_id": "20260602_18",
  "user_message": {
    "role": "user",
    "created": 1749091380,
    "content": [
      {"type": "text", "text": "¿Quién sos? Decime tu nombre y qué hacés. Respondé en una sola oración."}
    ],
    "metadata": {
      "agentVisible": true,
      "userVisible": true
    }
  }
}
```

**Response (SSE stream):**
```
event: ping
data: {"type":"Ping"}

event: message
data: {"type":"Message","session_id":"20260602_18","content":[{"type":"thinking","thinking":"The"}]}

event: message
data: {"type":"Message","session_id":"20260602_18","content":[{"type":"thinking","thinking":" user"}]}

event: message
data: {"type":"Message","session_id":"20260602_18","content":[{"type":"thinking","thinking":" is"}]}

... (streaming de thinking token por token) ...

event: message
data: {"type":"Message","session_id":"20260602_18","content":[{"type":"text","text":"Soy Goose, un agente de IA de propósito general creado por AAIF para ayudarte con análisis de código, automatización, búsquedas y muchas otras tareas."}]}

event: message
data: {"type":"Result","session_id":"20260602_18","total_tokens":3463,"input_tokens":3396,"output_tokens":67,...}
```

**Tipos de eventos en el stream SSE:**

| Tipo | Descripción |
|------|-------------|
| `Ping` | Keepalive cada ~15s |
| `Message` + `thinking` | Razonamiento token por token (DeepSeek V4) |
| `Message` + `text` | Respuesta de texto |
| `Message` + `ToolRequest` | Tool invocada por el agente |
| `Message` + `ToolResponse` | Resultado de la tool |
| `Result` | Métricas finales (tokens, costo) |
| `Error` | Error (ej: "Provider not set") |

---

## Flujo completo para usar la API

```bash
KEY="f7aa8124c29aa1d6c85582e241231568f9780ae8a89886e00da66138f2464ffc"
BASE="https://127.0.0.1:39591"

# 1. Crear sesión
SESSION=$(curl -sk -H "X-Secret-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"working_dir":"/tmp"}' \
  "$BASE/agent/start" | jq -r '.id')

# 2. Configurar provider
curl -sk -H "X-Secret-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"provider\":\"custom_deepseek\",\"session_id\":\"$SESSION\"}" \
  "$BASE/agent/update_provider"

# 3. Listar tools
curl -sk -H "X-Secret-Key: $KEY" \
  "$BASE/agent/tools?session_id=$SESSION" | jq 'length'

# 4. Enviar mensaje (SSE)
NOW=$(date +%s)
curl -sk -N -H "X-Secret-Key: $KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d "{
    \"session_id\": \"$SESSION\",
    \"user_message\": {
      \"role\": \"user\",
      \"created\": $NOW,
      \"content\": [{\"type\":\"text\",\"text\":\"Hola\"}],
      \"metadata\": {\"agentVisible\":true,\"userVisible\":true}
    }
  }" \
  "$BASE/reply"
```

---

## Endpoints NO probados (existen en el código pero no se verificaron)

- `GET  /system_info` — información del sistema
- `GET  /diagnostics/{session_id}` — zip de diagnóstico
- `POST /agent/stop` — detener sesión
- `POST /agent/restart` — reiniciar sesión
- `POST /agent/resume` — reanudar sesión
- `POST /agent/call_tool` — ejecutar tool directamente
- `POST /agent/add_extension` — agregar extensión
- `GET  /sessions` — listar sesiones
- `GET  /sessions/{id}` — detalle de sesión
- `GET  /config` — configuración completa
- `PUT  /config` — actualizar configuración
- `GET  /config/extensions` — listar extensiones
- `POST /setup/status` — estado de setup
- `GET  /recipe` — listar recetas
- `POST /recipe/run` — ejecutar receta
- `WS   /session_events` — WebSocket eventos en tiempo real
- `GET  /scalar` — documentación Swagger UI
- `GET  /openapi.json` — OpenAPI schema

---

## Limitaciones observadas

1. **Sin system prompt**: las sesiones arrancan sin `AGENTS.md` ni personalización LINA. El agente responde como "Goose" genérico.
2. **Provider obligatorio**: después de `start`, hay que llamar a `update_provider` antes de `reply`.
3. **TLS self-signed**: requiere `-k` en curl o configurar el CA.
4. **Solo localhost**: el puerto 39591 solo escucha en `127.0.0.1`.
5. **Dos instancias de goosed**: la de Goose Desktop (PID 1914424, puerto 39591) y la de LINA/gateway (sin puerto HTTP).
