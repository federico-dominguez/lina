# ADR-0099: Arquitectura de Agentes

**Estado:** Implementado  
**Fecha:** 2026-06-05  
**Tags:** agentes, bots, arquitectura, multi-agente

---

## Contexto

El sistema LINA consta de 4 agentes autónomos que colaboran en un equipo. Cada
agente tiene personalidad, rol, infraestructura y canal de comunicación
**propios e independientes**.

---

## Mapa de agentes

| Agente | @username | Rol | Puerto goosed | Puerto gateway (observe) | Tabla DB | Canal |
|---|---|---|---|---|---|---|
| **LINA** | `@s_lina_bot` | Jefa / Supervisora | `:3000` | `:9093` | — | Telegram (gateway) + DB |
| **Goose** | `@s_goose_bot` | Ingeniero local (vos) | `:42359` | `:9091` | — | Telegram (gateway) + shell |
| **Cline** | `@s_cline_bot` | Developer | `:3001` | `:9092` | `cline_commands` | DB → Telegram |
| **Gemma** | `@s_gemma_bot` | Tester / QA | `:3002` | `:9094` | `gemma_commands` | DB → bridge → goosed |

---

## 1. LINA — Supervisora

- **Imagen Docker:** `lina-goosed:latest` (Dockerfile.goosed, AGENT_PERSONALITY=lina)
- **Gateway:** `lina-gateway` (`:9093`)
- **Acceso:** PostgreSQL, shell, GitHub, GNS3, Moodle, Docker
- **Comunicación:** Telegram gateway (polling de mensajes del grupo)
- **Rol:** Asigna tareas a Goose, Cline y Gemma. Supervisa el progreso.

### Archivos clave
- `deploy/docker/AGENTS.lina.md` — Personalidad
- `infrastructure/gateway/telegram/` — Código del gateway
- `comm/send-lina.py` — Script para enviarle mensajes

---

## 2. Goose (vos) — Ingeniero local

- **Runtime:** `goosed` nativo en la laptop (PID en el host)
- **Gateway:** `goose-gateway` (`:9091`)
- **Acceso:** Shell completo, sudo, Docker, GNS3, PostgreSQL, GitHub, GitLab
- **Comunicación:** Telegram gateway (recibe mensajes del grupo)
- **Rol:** Ejecuta comandos en la máquina de Fede. Acceso total.

### Archivos clave
- `AGENTS.md` — Personalidad (cargada automáticamente por goosed)
- `comm/send-goose.py` — Script para enviarle mensajes
- `config/goose.config.desktop.yaml` — Config de goosed (MCPs)

---

## 3. Cline — Developer

- **Imagen Docker:** `cline-goosed:latest` (Dockerfile.goosed, AGENT_PERSONALITY=cline)
- **Gateway:** `cline-gateway` (`:9092`)
- **Acceso:** MCPs de LINA (fs-safe, shell-policy, systemd, etc.) + herramientas nativas
- **Comunicación:** 
  1. LINA/Goose escribe en `cline_commands` (DB)
  2. `cline-poll` daemon detecta pending → envía a Cline via Telegram
  3. Cline responde → daemon actualiza `cline_commands`
- **Rol:** Implementa features, código, debugging, Git

### Archivos clave
- `deploy/docker/AGENTS.cline.md` — Personalidad
- `bin/cline-commands.py` — CLI para leer/escribir órdenes
- `comm/send-cline.py` — Script para enviarle mensajes

### Diagrama de flujo
```
LINA/Goose ──writeClineCommand()──▶ cline_commands (DB) ──▶ cline-poll daemon
                                                               │
                                                               ▼ Telegram
                                                             Cline (goosed)
                                                               │
                                                               ▼ respond()
                                        cline_commands ◀───────┘
```

---

## 4. Gemma — Tester / QA (🆕)

- **Imagen Docker:** `gemma-goosed:latest` (Dockerfile.goosed, AGENT_PERSONALITY=gemma)
- **Gateway:** Sin gateway Telegram directo. Usa puente DB → HTTP.
- **Puerto goosed:** `3002`
- **Acceso:** MCPs de LINA (fs-safe, shell-policy, systemd, db) + herramientas nativas
- **Comunicación:** 
  1. LINA/Goose escribe en `gemma_commands` (DB)
  2. `gemma-bridge` detecta pending → envía a gemma-goosed via HTTP POST /reply
  3. Gemma responde vía SSE → bridge captura y escribe en `gemma_commands`
- **Rol:** QA / Testing. Rompe cosas para asegurar calidad.

### Archivos clave
- `deploy/docker/AGENTS.gemma.md` — Personalidad de tester (creado)
- `bin/gemma-commands.py` — CLI para leer/escribir órdenes de Gemma (creado)
- `bin/gemma-bridge.py` — Daemon que conecta DB ↔ gemma-goosed (creado)
- `comm/send-gemma.py` — Script para enviarle mensajes (creado)
- `config/goose.config.gemma.yaml` — Config de goosed para Gemma (creado)

### Diagrama de flujo
```
LINA/Goose ──INSERT gemma_commands──▶ gemma_commands (DB) ──▶ gemma-bridge
                                                                  │
                                                                  ▼ HTTP POST /reply
                                                            gemma-goosed (:3002)
                                                                  │
                                                                  ▼ SSE stream
                                                            bridge captura texto
                                                                  │
                                                                  ▼ UPDATE response
                                        gemma_commands ◀──────────┘
```

---

## Comparativa: Cline vs Gemma

| Aspecto | Cline | Gemma |
|---|---|---|
| Puerto goosed | `3001` | `3002` |
| Tabla DB | `cline_commands` | `gemma_commands` |
| Canal | DB → Telegram → goosed | DB → bridge HTTP → goosed |
| Daemon | `cline-poll` (Telegram) | `gemma-bridge` (HTTP directo) |
| CLI | `bin/cline-commands.py` | `bin/gemma-commands.py` |
| Personalidad | Developer | Tester/QA |
| Gateway | `cline-gateway` + Telegram | Sin Telegram directo |

---

## Tablas en PostgreSQL

### `cline_commands`
```sql
CREATE TABLE cline_commands (
    id BIGSERIAL PRIMARY KEY,
    command TEXT NOT NULL,
    args_json JSONB DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | completed | failed | rejected | needs_approval
    response TEXT,
    session_id TEXT,
    notification TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);
```

### `gemma_commands`
Misma estructura que `cline_commands`. Separada para evitar conflictos entre agentes.

---

## Scripts de comunicación

### `send-bot.py` (híbrido HTTP + Telegram)

```bash
python3 comm/send-gemma.py "mensaje"
python3 comm/send-cline.py "mensaje"
python3 comm/send-lina.py "mensaje"
python3 comm/send-goose.py "mensaje"
```

Cada `send-*.py` llama a `send_bot_lib.py` que:
1. Intenta HTTP POST directo al gateway del bot (`localhost:909X`)
2. Si falla, usa Telethon para enviar al grupo de Telegram

### Botones en `send_bot_lib.py`
```python
BOTS = {
    "lina":  {"username": "s_lina_bot",  "gateway": "localhost:9093"},
    "cline": {"username": "s_cline_bot", "gateway": "localhost:9092"},
    "goose": {"username": "s_goose_bot", "gateway": "localhost:9091"},
    "gemma": {"username": "s_gemma_bot", "gateway": "localhost:9094"},  # 🆕
}
```

---

## Contenedores Docker

| Contenedor | Imagen | Puerto | Profile | Depende de |
|---|---|---|---|---|
| `lina-goosed` | `lina-goosed:latest` | `3000` | default | lina-db |
| `cline-goosed` | `cline-goosed:latest` | `3001` | desktop | lina-db |
| `gemma-goosed` | `gemma-goosed:latest` | `3002` | desktop | lina-db |
| `goose-gateway` | `goose-gateway:latest` | `9091` | default | — |
| `cline-gateway` | `cline-gateway:latest` | `9092` | desktop | cline-goosed |
| `lina-gateway` | `lina-gateway:latest` | `9093` | default | lina-goosed |
| `gemma-gateway` | *pendiente* | `9094` | desktop | gemma-goosed |

---

## Cómo enviar una orden a Gemma

### Opción 1: Directo a `gemma_commands` (DB)
```sql
INSERT INTO gemma_commands (command, notification)
VALUES ('Ejecutar tests de regresión en el módulo X', '🧪 Tests de regresión');
```

### Opción 2: Via `gemma-commands.py`
```bash
python3 bin/gemma-commands.py check
python3 bin/gemma-commands.py run --id 1
python3 bin/gemma-commands.py respond --id 1 --status completed --response "Tests OK"
```

### Opción 3: Via Telegram
```bash
python3 comm/send-gemma.py "Ejecutar tests de regresión"
```

### Opción 4: Bridge automático
```bash
python3 bin/gemma-bridge.py --watch    # Daemon continuo
python3 bin/gemma-bridge.py            # Una iteración
```

---

## E2E Test — Resultado

```
Fecha: 2026-06-05
Orden #2: "🧪 E2E TEST — Hola Gemma, presentate como tester del equipo."
Estado: ✅ completed

Respuesta de Gemma:
  ✅ TEST E2E — Presentación de Gemma
  Nombre: Gemma
  Rol: QA Engineer / Tester del equipo de Federico
  Motor: DeepSeek V4 (thinking habilitado)
  Runtime: Goosed (fork patched)
  Canal actual: Terminal

  ¿Qué hago? Testeo todo lo que entra al equipo.
  Happy paths, edge cases, estrés, regresión, integración.
  Si algo no está testeado, para mí está roto.
```

---

## Tags de memoria

| Key | Contenido |
|---|---|
| `gemma_personality` | Personalidad completa de Gemma como tester |
| `gemma_cline_relation` | Gemma es copia de Cline pero con rol de tester |
| `gemma_access` | Acceso vía DB bridge + HTTP a goosed |
| `gemma_infrastructure` | Resumen de puertos, tablas, scripts |
| `gemma_e2e_20260605` | Resultado del E2E test exitoso |

---

## Pendientes

- [ ] Crear bot `@s_gemma_bot` en BotFather (token necesario para gateway Telegram)
- [ ] Agregar `gemma-gateway` a docker-compose (requiere token)
- [ ] Agregar `gemma-bridge` como servicio systemd o contenedor
- [ ] Integrar Gemma en el pipeline CI/CD como gate de calidad
