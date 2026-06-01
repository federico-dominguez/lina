# LINA — Roadmap de desarrollo

**Última actualización:** 2026-05-31  
**Estado actual:** Fase 3 completada (hardening + session persistence). Fase 4 en curso (multi-agente). Fase 3.5 pospuesta.

---

## Estado de la base

| Componente | Estado |
|---|---|
| Docker stack (goosed + gateway + MCPs + DB) | ✅ producción |
| Session persistence (session_messages en Postgres) | ✅ mergeado #56 |
| Hot-swap error isolation (goosed restart retry) | ✅ mergeado #57 |
| E2E testing (TelegramTestClient) | ✅ mergeado #28 |
| MCPs activos | github, gitlab, moodle, fs-safe, shell-policy, secrets, lina-db, gcalendar, systemd-user, gns3 |

---

## Fase 3.5 — Memoria e Inteligencia (COMPLETADA — 4/7 issues)

> Objetivo original: que LINA recuerde mejor, razone más y perciba más canales.  
> Issues 4-7 pospuestos — priorizamos Fase 4 (multi-agente).

| Prioridad | # | Issue | Qué resuelve | Estado |
|---|---|---|---|---|
| 1 | [#60](https://github.com/federico-dominguez/lina/issues/60) | Smart context summarization | Reemplaza inyección cruda de 20 msgs por summary estructurado | ✅ mergeado |
| 2 | [#61](https://github.com/federico-dominguez/lina/issues/61) | Token/cost metering | Tracking de costo USD por sesión + datos reales del dashboard | ✅ mergeado #72 |
| 3 | [#62](https://github.com/federico-dominguez/lina/issues/62) | Reasoning trace persistence | Guarda bloques `<think>` en DB para auto-análisis | ✅ mergeado #74 |
| 4 | [#63](https://github.com/federico-dominguez/lina/issues/63) | pgvector semantic memory | Búsqueda por significado en recuerdos | ✅ mergeado #76 |
| 5 | [#64](https://github.com/federico-dominguez/lina/issues/64) | Image understanding | Procesa fotos/capturas enviadas a Telegram | 🔲 backlog |
| 6 | [#65](https://github.com/federico-dominguez/lina/issues/65) | TTS voice responses | LINA responde con nota de voz cuando Federico habla | 🔲 backlog |
| 7 | [#66](https://github.com/federico-dominguez/lina/issues/66) | Proactive scheduler | Mensajes programados sin intervención de Federico | 🔲 backlog |

### Detalle #60 — Smart context summarization

**Archivos a tocar:**
- `sql/migrations/004-session-summaries.sql` — nueva tabla `session_summaries`
- `infrastructure/gateway/telegram/src/lina_gateway/boot_hook.py` — `get_smart_context()` reemplaza `get_last_messages()`
- `infrastructure/gateway/telegram/src/lina_gateway/bot.py` — `_maybe_inject_context()` usa `get_smart_context()`
- `mcps/lina-db/src/lina_db/server.py` — herramienta `summarize_session_smart()` para que LINA guarde el summary al final de sesión
- `recipes/session-end.yaml` — agregar paso que llama `summarize_session_smart()`

**Flujo resultante:**
```
Nueva sesión detectada (is_new=True)
  → get_smart_context(db_url, session_id)
      1. Busca session_summaries más reciente  (~200 tokens)
      2. Agrega los últimos 5 mensajes crudos  (~300 tokens)
      3. Agrega top-3 memories relevantes       (~100 tokens)
  → warmup ≤ 700 tokens (antes: hasta 4000+)
```

---

## Fase 4 — Autonomía Plena (Multi-Agente)

> **Prioridad actual.** Empezamos sin esperar Fase 3.5 completa.  
> Objetivo: LINA con agentes especializados (dev, ops, assistant) y capacidad de automejora.  
> Orden estricto por dependencias:

| Orden | # | Issue | Qué resuelve | Prerequisito |
|---|---|---|---|---|
| 0 | — | Saneamiento de repo + merge Fase 3 a `main` | Limpiar branches viejas, unificar código base | ✅ done |
| 1 | [#15](https://github.com/federico-dominguez/lina/issues/15) | **ADR 0010 — mcp-agent + Orchestrator-Workers** | Decisión arquitectónica + prototipo | 🟡 en PR |
| 2 | [#50](https://github.com/federico-dominguez/lina/issues/50) | lina-orchestrator (infra + UX Telegram) | Manager + subagentes con políticas + panel/comandos | #15 |
| 3 | [#48](https://github.com/federico-dominguez/lina/issues/48) | Hot-reload MCPs sin reiniciar goosed | Modificar MCP sin downtime ni rebuild | #50 |
| 4 | [#51](https://github.com/federico-dominguez/lina/issues/51) | lina-self-modify — ciclo completo de automejora | Subagente `dev` con aprobaciones + Temporal | #48 |
| 5 | [#67](https://github.com/federico-dominguez/lina/issues/67) | Observability stack (Prometheus + Grafana) | Métricas por rol, latencia spawn, alertas | paralelo |
| 6 | [#68](https://github.com/federico-dominguez/lina/issues/68) | lina-android-remote — control del celular | Control remoto del celular de Federico | opc. |

**Framework elegido:** [`mcp-agent`](https://github.com/lastmile-ai/mcp-agent) (MCP-native, ~20MB, patrones Anthropic) sobre LangGraph. Ver [docs/architecture/0010-multi-agent-framework.md](architecture/0010-multi-agent-framework.md).

### Plan detallado Fase 4

**Paso 0 — Saneamiento de repo** (este PR)
- Mergear `feat/fase-3-hardening` → `main`
- Borrar branches remotas ya mergeadas (~20)
- Borrar branches locales equivalentes

**Paso 1 — ADR 0010: Framework multi-agente**
- Investigar Agno, CrewAI, LangGraph, AutoGen
- Evaluar integración con MCPs, latencia, debuggability
- Escribir ADR con recomendación

**Paso 2 — Hot-reload MCPs**
- Endpoint en goosed o sidecar para reload de MCPs sin restart
- Alternativa: file watcher automático
- Tool `reload_mcp(name)` en lina-self-modify

**Paso 3 — lina-self-modify**
- Tools: modify_file, run_tests, run_lint, reload_mcp, rollback_mcp
- Pipeline pre-deploy: modify → lint → test → reload
- Rollback automático si falla

**Paso 4 — lina-orchestrator**
- Sistema multi-agente con 3 agentes iniciales:
  - `lina-dev` (código, PRs, refactors)
  - `lina-ops` (infra, deploys, monitoreo)
  - `lina-assistant` (Moodle, calendario, tareas generales)
- Clasificación de intención → delegación → respuesta consolidada
- Servicio nuevo en docker-compose + tests E2E

**Paso 5 — Observability stack**
- Prometheus + Grafana en docker-compose
- Métricas de gateway, orquestador, PostgreSQL
- Dashboard de tokens, latencia, errores, sesiones activas

---

## Convenciones del repo

- **Branches:** `feat/<issue>-<slug>` / `fix/<issue>-<slug>`
- **Commits:** Conventional Commits (`feat/fix/docs/chore`)
- **CI:** ruff lint+format + pytest coverage ≥40% (gateway) antes de merge
- **Reviews:** Copilot como reviewer automático en todos los PRs
- **Migrations:** `sql/migrations/NNN-<nombre>.sql` — aplicar manualmente con `docker compose exec lina-db psql -U lina -d lina -f /migrations/NNN-...sql`
- **MCPs nuevos:** Clean Architecture — `domain/ application/ infrastructure/ server.py`
- **Tests E2E:** `tests/e2e/telegram/.venv/bin/python` — requiere credenciales en env