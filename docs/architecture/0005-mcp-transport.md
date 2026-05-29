# 0005 — Transport MCP: stdio → Streamable HTTP

> **Estado:** Aceptado
> **Fecha:** 2026-05-29
> **Autor:** LINA (revisado por federico-dominguez)
> **Issue:** #14
> **Aplica a:** Todos los MCPs Python de LINA (`secrets`, `fs-safe`, `shell-policy`, `systemd-user`, `moodle`, `gitlab` y futuros).
> **Dependencia:** Prerequisito obligatorio de issue #10 (Fase 2 — Containerización de MCPs).

---

## Tabla de contenidos

1. [Contexto](#1-contexto)
2. [Definición del problema](#2-definición-del-problema)
3. [Opciones evaluadas](#3-opciones-evaluadas)
4. [Decisión](#4-decisión)
5. [Estrategia de migración por fase](#5-estrategia-de-migración-por-fase)
6. [Impacto en el código de cada MCP](#6-impacto-en-el-código-de-cada-mcp)
7. [Impacto en Goose / goosed](#7-impacto-en-goose--goosed)
8. [Validación (prueba de concepto)](#8-validación-prueba-de-concepto)
9. [Riesgos y mitigaciones](#9-riesgos-y-mitigaciones)
10. [Consecuencias](#10-consecuencias)
11. [Referencias](#11-referencias)

---

## 1. Contexto

### Estado actual

Todos los MCPs de LINA son procesos Python lanzados por Goose como subprocesos hijos. La comunicación ocurre por `stdin`/`stdout` siguiendo el protocolo JSON-RPC del MCP spec, transportados sobre **stdio**.

```
goosed
  ├── fork → lina-fs-safe     (stdin/stdout ← JSON-RPC)
  ├── fork → lina-secrets      (stdin/stdout ← JSON-RPC)
  ├── fork → lina-shell-policy (stdin/stdout ← JSON-RPC)
  ├── fork → lina-systemd-user (stdin/stdout ← JSON-RPC)
  ├── fork → lina-moodle       (stdin/stdout ← JSON-RPC)
  └── fork → lina-gitlab       (stdin/stdout ← JSON-RPC)
```

Cada MCP usa `FastMCP` del SDK Python oficial y termina con:

```python
# patrón actual — todos los MCPs
if __name__ == "__main__":
    mcp.run()          # stdio implícito
    # o en gitlab:
    mcp.run(transport="stdio")
```

### Por qué necesitamos cambiar

El [RFC #7](https://github.com/federico-dominguez/lina/issues/7) y el roadmap de Fase 2 (#10) definen que cada MCP va a vivir en su **propio contenedor Docker**. En ese modelo:

- goosed y los MCPs ya **no comparten espacio de procesos**.
- Los file descriptors `stdin`/`stdout` de un contenedor no son accesibles desde otro.
- **stdio cross-container no existe** en Docker sin hacks frágiles.

Hay que decidir el transport ahora, antes de empezar Fase 2, porque afecta:
1. La forma en que cada `server.py` arranca.
2. El modo de operación del `docker mcp gateway` oficial.
3. La topología de red del `docker-compose.yml` de Fase 1.
4. Los tests de integración (distintos según transport).

---

## 2. Definición del problema

> Dado que Fase 2 containeriza cada MCP en su propio contenedor, ¿cómo se comunica `goosed` (o el MCP Gateway) con los MCPs?

Restricciones hard:
- **No agregar complejidad operacional innecesaria** (nada de gRPC, QUIC o protocolos exóticos).
- **Mantenerse dentro de la MCP spec oficial** (no inventar un transporte propio).
- **Compatible con `docker/mcp-gateway`** (elegido en el RFC como gateway oficial).
- **Path de migración incremental**: Fase 1 debe poder seguir con stdio; el corte se hace en Fase 2.

---

## 3. Opciones evaluadas

### Opción A — stdio sobre `socat`/`docker exec` (bridge)

```
goosed → docker exec -i <container> /mcp-binary → stdin/stdout via pty
```

- goosed invoca `docker exec -i lina-fs-safe lina-fs-safe` por cada sesión de tool.
- El gateway multiplexa los descriptores.

| | |
|---|---|
| ✅ Sin cambio en el código MCP | |
| ✅ Familiar para el operador | |
| ❌ **No es spec-compliant**: la MCP spec no define "stdio via docker exec" | |
| ❌ `docker exec` añade ~50 ms de overhead por invocación | |
| ❌ No funciona con el modo `--transport streaming` del gateway oficial | |
| ❌ No escala a MCPs remotos (cloud, otro host) | |
| ❌ `socat` introduce un punto extra de fallo | |

**Veredicto**: descartada.

---

### Opción B — Streamable HTTP (MCP spec 2025-06-18) ✅ ELEGIDA

Cada MCP expone un endpoint HTTP. El servidor acepta peticiones POST y responde con SSE (Server-Sent Events) para streaming de mensajes.

```
goosed / mcp-gateway
  ├── HTTP POST http://lina-fs-safe:8001/mcp     → llamada bloqueante
  ├── HTTP POST http://lina-secrets:8002/mcp     → con SSE para streaming
  ├── HTTP POST http://lina-shell-policy:8003/mcp
  ├── HTTP POST http://lina-systemd-user:8004/mcp
  ├── HTTP POST http://lina-moodle:8005/mcp
  └── HTTP POST http://lina-gitlab:8006/mcp
```

El SDK Python `mcp[cli]>=1.2.0` ya instalado en todos los MCPs soporta este transport:

```python
# cambio mecánico en cada server.py — 1 línea
mcp.run(transport="streamable-http", host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8000")))
```

| | |
|---|---|
| ✅ **Transport oficial del MCP spec** (rev. 2025-06-18) | |
| ✅ Compatible con `docker mcp gateway --transport streaming` | |
| ✅ Soporta autenticación estándar (Bearer token, OAuth 2.0) | |
| ✅ Permite MCPs remotos en Fase 4 (LINA en cloud, otro host) | |
| ✅ SSE nativo = streaming de resultados largos (moodle, gitlab) | |
| ✅ Cambio mecánico: **1 línea por MCP** | |
| ✅ Healthcheck HTTP estándar (`/health`, `GET /`) | |
| ⚠️ Introduce un puerto TCP por MCP (gestionado por compose `internal: true`) | |
| ⚠️ Requiere considerar autenticación entre contenedores (mitigado: red interna Docker) | |

**Veredicto**: elegida.

---

### Opción C — Gateway hace bridge stdio↔HTTP

El `docker mcp gateway` levanta los MCPs como subprocesos stdio **dentro del mismo container** del gateway, y expone una fachada HTTP hacia fuera.

```
[goosed] → HTTP → [lina-gateway] → fork stdin/stdout → [lina-fs-safe (subprocess)]
```

| | |
|---|---|
| ✅ Sin cambio en los MCPs | |
| ✅ El gateway oficial soporta este modo (`docker mcp gateway run`) | |
| ❌ **Todos los MCPs en el mismo container**: pierde el aislamiento prometido en el RFC | |
| ❌ Un crash en `lina-shell-policy` derriba todos los MCPs | |
| ❌ No escala: no podés tener réplicas de un MCP individual | |
| ❌ Anula la ventaja de la containerización individual | |

**Veredicto**: aceptable como modo de transición temporal en Fase 1, descartada para Fase 2+.

---

## 4. Decisión

**Se adopta Streamable HTTP (Opción B) como transport definitivo de LINA Pro.**

**Plan por fase:**

| Fase | Transport | Justificación |
|---|---|---|
| Fase 0 (tests/fundaciones) | stdio | No hay containers aún; sin cambio. |
| Fase 1 (Docker + Postgres) | stdio o Opción C transitoria | goosed y MCPs pueden cohabitar en misma red host. El foco es levantar el stack Docker, no migrar el transport. |
| **Fase 2** (Containerización MCPs) | **Streamable HTTP** | Cada MCP = container propio. Corte definitivo de stdio cross-container. |
| Fase 3+ | Streamable HTTP + auth | Se añade Bearer token interno entre gateway y MCPs. |

**La migración es incremental**: ningún MCP se rompe hasta que su container se despliegue en Fase 2.

---

## 5. Estrategia de migración por fase

### Fase 1 — Sin cambio de transport

Los MCPs siguen corriendo en el host (o en el container de `goosed` via Opción C transitoria). No se requiere ningún cambio en `server.py`.

Lo que sí se hace en Fase 1 para preparar la migración:
1. Añadir soporte condicional por variable de entorno en cada `server.py`:

```python
# server.py — patrón a adoptar en Fase 1 (no requiere cambio de comportamiento)
def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    port = int(os.getenv("MCP_PORT", "8000"))
    if transport == "streamable-http":
        mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
    else:
        mcp.run()  # stdio — comportamiento actual
```

Cuando `MCP_TRANSPORT` no está seteado → stdio (sin regresión).
Cuando el container lo setea → HTTP.

2. Actualizar `pyproject.toml` a `mcp[cli]>=1.2.0` (ya está en todos los MCPs).

### Fase 2 — Corte a Streamable HTTP

En `docker-compose.yml`:

```yaml
services:
  lina-fs-safe:
    build: ./mcps/fs-safe
    environment:
      MCP_TRANSPORT: streamable-http
      MCP_PORT: "8000"
    expose:
      - "8000"
    networks:
      - lina-internal   # red Docker interna, no expuesta al host
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

El gateway o goosed apuntan a `http://lina-fs-safe:8000/mcp`.

---

## 6. Impacto en el código de cada MCP

El cambio de stdio a Streamable HTTP es **mecánico**. Afecta exactamente un bloque `if __name__ == "__main__"` por MCP:

| MCP | Cambio requerido |
|---|---|
| `lina-fs-safe` | `mcp.run()` → soporte condicional por `MCP_TRANSPORT` |
| `lina-secrets` | idem |
| `lina-shell-policy` | idem |
| `lina-systemd-user` | idem |
| `lina-moodle` | idem |
| `lina-gitlab` | `mcp.run(transport="stdio")` → soporte condicional |

La lógica de negocio, el allowlist, el audit log, las tool definitions: **nada cambia**.

### Diff tipo

```diff
+# Transport config — read early because FastMCP bakes host/port at construction.
+# MCP_TRANSPORT=streamable-http enables HTTP mode (containerised Fase 2+).
+# MCP_PORT overrides the listening port in HTTP mode (default 8000).
+_MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
+_MCP_HTTP_PORT = int(os.environ.get("MCP_PORT", "8000"))
+
-mcp = FastMCP("lina-fs-safe")
+mcp = FastMCP(
+    "lina-fs-safe",
+    host="0.0.0.0",     # only used in streamable-http mode
+    port=_MCP_HTTP_PORT,
+)

 def main() -> None:
     log.info("starting allowlist=%s", [str(p) for p in ALLOWLIST])
-    mcp.run()
+    log.info("transport=%s port=%d", _MCP_TRANSPORT, _MCP_HTTP_PORT)
+    mcp.run(transport=_MCP_TRANSPORT)

 if __name__ == "__main__":
     main()
```

**Líneas cambiadas por MCP**: ~10. Total para los 6 MCPs: ~60 líneas.

> **Nota sobre la API de FastMCP**: el constructor `FastMCP(name, host=..., port=...)` acepta los parámetros directamente. La env var `FASTMCP_HOST`/`FASTMCP_PORT` no es leída en el SDK `1.27.x`. El patrón correcto es leer `MCP_PORT` antes de la construcción y pasarlo al constructor.

> **Nota sobre los headers HTTP**: el transporte Streamable HTTP requiere que el cliente envíe `Accept: application/json, text/event-stream`. Las respuestas se devuelven como SSE (`data: {...}\n\n`). La segunda llamada en adelante requiere el header `Mcp-Session-Id` devuelto por `initialize`.

---

## 7. Impacto en Goose / goosed

### Fase 1 (sin cambio de transport)

Goose sigue lanzando MCPs como subprocesos stdio. La configuración en `~/.config/goose/config.yaml` no cambia.

### Fase 2 (HTTP)

goosed necesita poder descubrir MCPs por URL HTTP en vez de por `cmd`. Goose 1.35+ soporta extensiones tipo `remote` (Streamable HTTP):

```yaml
# ~/.config/goose/config.yaml — Fase 2
extensions:
  lina-fs-safe:
    type: remote          # no stdio
    endpoint: http://lina-fs-safe:8000/mcp
    timeout: 30
```

Alternativamente, `docker mcp gateway` hace el registro automático si se le pasa el catalog YAML, y goosed apunta solo al gateway (`http://lina-gateway:8080/mcp`). Esa decisión se toma en issue #10.

---

## 8. Validación (prueba de concepto)

**Resultado**: ✅ **VALIDADO** — commit `feat(lina-fs-safe): add streamable-http transport mode (POC #14)` en esta branch.

**MCP de prueba**: `lina-fs-safe`.

**Pasos ejecutados**:

1. Añadir soporte condicional por `MCP_TRANSPORT` en `lina-fs-safe/server.py`.
2. Arrancar en modo HTTP:
   ```bash
   MCP_TRANSPORT=streamable-http MCP_PORT=8765 uv run lina-fs-safe
   # → INFO: Uvicorn running on http://0.0.0.0:8765
   ```
3. Handshake `initialize`:
   ```bash
   curl -s -X POST http://localhost:8765/mcp \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize",...}'
   # → HTTP 200 + Mcp-Session-Id: e958efdcac4c4607ad14c48faa01ae1c
   ```
4. `tools/list` con session ID → devuelve las 8 tools (`fs_allowlist`, `fs_read`, `fs_list`, `fs_stat`, `fs_write`, `fs_mkdir`, `fs_delete`, `fs_move`) ✅
5. Sin `MCP_TRANSPORT`, `lina-fs-safe` arranca en modo stdio y responde `initialize` por stdin/stdout sin cambio de comportamiento ✅ (sin regresión)

**Hallazgo técnico documentado**: el SDK Python `mcp 1.27.x` no lee `FASTMCP_HOST`/`FASTMCP_PORT` como env vars standalone; hay que pasar `host`/`port` al constructor `FastMCP(name, host=..., port=...)`. Este diff está incluido en la sección 6.

---

## 9. Riesgos y mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Goose remote endpoint no soporta auth en Fase 2 | Media | Medio | Red Docker `internal: true` mitiga sin auth en MVP; auth en Fase 3. |
| MCP streamable-http no soporta `tools/list` streaming | Baja | Bajo | Verificado en SDK 1.2: `tools/list` es síncrono, no necesita SSE. |
| Puerto de un MCP colisiona con otro servicio | Baja | Bajo | Puertos internos (no expuestos al host), composados por nombre DNS. |
| Timeout de conexión en tools lentas (moodle, gitlab) | Media | Medio | Configurar `timeout` por extensión en goosed config; SSE mantiene la conexión viva. |
| Regresión en modo stdio durante Fase 0/1 | Baja | Alto | El switch es por env var; `MCP_TRANSPORT` no seteado = comportamiento actual. |

---

## 10. Consecuencias

### Positivas
- Cada MCP es un servicio HTTP estándar: testeable con `curl`, `httpx`, `pytest`, `testcontainers`.
- El gateway oficial (`docker mcp gateway`) funciona sin configuración adicional.
- Habilita MCPs remotos en Fase 4: misma interfaz, distinta URL.
- Permite réplicas horizontales de un MCP (múltiples instancias de `lina-moodle` bajo un load balancer).
- Healthchecks HTTP estándar en Docker Compose.

### Neutras
- Los puertos son internos a la red Docker — no hay exposición al exterior.
- El overhead de HTTP vs stdio en LAN Docker es ~2–5 ms por llamada: despreciable para el perfil de uso de LINA (tool calls de segundos a minutos).

### Negativas / aceptadas
- Goose config de Fase 2 requiere declarar extensiones como `type: remote` en vez de `cmd`. Es un cambio controlado al momento de desplegar Fase 2.
- En Fase 0/1 los MCPs corren en dos modos (stdio en host, HTTP en tests): requiere disciplina de testing.

---

## 11. Referencias

- MCP Spec — Transports (rev. 2025-06-18): https://modelcontextprotocol.io/docs/concepts/transports
- MCP Spec — Architecture: https://modelcontextprotocol.io/docs/learn/architecture
- Python SDK `mcp[cli]>=1.2.0` — streamable-http: https://github.com/modelcontextprotocol/python-sdk
- Docker MCP Gateway — transport modes: https://github.com/docker/mcp-gateway
- `docker/compose-for-agents` — patrones multi-container: https://github.com/docker/compose-for-agents
- Issue #7 — RFC Docker completo: https://github.com/federico-dominguez/lina/issues/7
- Issue #10 — Fase 2 containerización: https://github.com/federico-dominguez/lina/issues/10
- Issue #13 — Fase 0 foundations: https://github.com/federico-dominguez/lina/issues/13
