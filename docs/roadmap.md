# LINA — Roadmap de desarrollo

**Última actualización:** 2026-06-01  
**Estado actual:** Fase 3 completada. ADR 0010 mergeado (PR #78). Roadmap reestructurado en milestones `v0.4` → `v0.7`.

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

## Milestones (orden lógico, sin fechas)

| Milestone | Contiene | Estado |
|---|---|---|
| **v0.4 — Multi-agent core** | #82, #83, #84, #85 + epic #50 | 🟢 next |
| **v0.4.1 — Telegram UX subagentes** | #86, #87, #88, #89 | bloqueado por v0.4 |
| **v0.5 — Self-modify + Hot-reload** | #48, #51 | #48 puede empezar en paralelo |
| **v0.5.1 — Observabilidad + guardrails** | #67, #91, #92, #93 | paralelo a v0.5 |
| **v0.6 — Multimodal + Proactivo** | #64, #65, #66, #68 | independiente |
| **v0.7 — LINA as MCP server** | #90 | bloqueado por v0.4 + v0.4.1 + #67 |

**Transversal (sin milestone):** CI/CD hardening — #79, #80, #81. Sin dependencias, arranca ya.

**Framework elegido:** [`mcp-agent`](https://github.com/lastmile-ai/mcp-agent) — ver [ADR 0010](architecture/0010-multi-agent-framework.md).

---

## v0.4 — Multi-agent core

Construye el orquestador con `mcp-agent` (Orchestrator-Workers de Anthropic):

- [#82](https://github.com/federico-dominguez/lina/issues/82) `config/policies.yaml` + loader pydantic
- [#83](https://github.com/federico-dominguez/lina/issues/83) migration `009-orchestrator.sql` + LISTEN/NOTIFY
- [#84](https://github.com/federico-dominguez/lina/issues/84) spawner goosed-per-subagent con kill seguro
- [#85](https://github.com/federico-dominguez/lina/issues/85) MCP `mcps/orchestrator/` con tools de gestión
- [#50](https://github.com/federico-dominguez/lina/issues/50) — epic paraguas

**Criterio de cierre:** LINA recibe "investigá X" → spawn `research` → resultado vuelve. `/agents` lista al subagente activo.

## v0.4.1 — Telegram UX subagentes

- [#86](https://github.com/federico-dominguez/lina/issues/86) comando `/agents` (dashboard live editable)
- [#87](https://github.com/federico-dominguez/lina/issues/87) comandos `/kill /pause /resume /replan`
- [#88](https://github.com/federico-dominguez/lina/issues/88) HumanInputRequest → botones inline
- [#89](https://github.com/federico-dominguez/lina/issues/89) failure modes (timeouts, DLQ, restart con backoff)

## v0.5 — Self-modify + Hot-reload

- [#48](https://github.com/federico-dominguez/lina/issues/48) hot-reload de MCPs sin reiniciar goosed
- [#51](https://github.com/federico-dominguez/lina/issues/51) lina-self-modify — ciclo completo con Temporal

## v0.5.1 — Observabilidad + guardrails

- [#67](https://github.com/federico-dominguez/lina/issues/67) Prometheus + Grafana + alerts
- [#91](https://github.com/federico-dominguez/lina/issues/91) rate-limiting + cost-guards por rol
- [#92](https://github.com/federico-dominguez/lina/issues/92) backup automatizado + restore drill mensual
- [#93](https://github.com/federico-dominguez/lina/issues/93) comando `/audit` en Telegram

## v0.6 — Multimodal + Proactivo

- [#64](https://github.com/federico-dominguez/lina/issues/64) image understanding (vision)
- [#65](https://github.com/federico-dominguez/lina/issues/65) TTS voice responses (opt-in)
- [#66](https://github.com/federico-dominguez/lina/issues/66) proactive scheduler
- [#68](https://github.com/federico-dominguez/lina/issues/68) lina-android-remote (opcional)

## v0.7 — LINA as MCP server

- [#90](https://github.com/federico-dominguez/lina/issues/90) MCP `lina-bridge` — expone LINA a otros agentes

## Transversal — CI/CD hardening

- [#79](https://github.com/federico-dominguez/lina/issues/79) build de imágenes Docker en cada PR + size-gate
- [#80](https://github.com/federico-dominguez/lina/issues/80) integration tests con MCP spawn real
- [#81](https://github.com/federico-dominguez/lina/issues/81) coverage agregado + gate global 60%

---

## DAG de ejecución

```
CI/CD (#79, #80, #81) ───────── paralelo permanente, sin blockers

v0.4 ──> v0.4.1 ──> v0.7
   │       │
   │       └──> v0.5 (#48 paralelo) ──┐
   │                                   ├──> v1.0
   └────── v0.5.1 (paralelo a v0.5) ──┤
   └────── v0.6 (paralelo) ───────────┘
```

**Puede empezar ya en paralelo:**
- CI/CD #79, #80, #81
- Hot-reload #48 (sin dependencias)
- ADRs de modelo vision (#64) y TTS (#65)

**Requiere review estricto (no auto-merge):**
- Todo v0.4 (arquitectura core)
- #51 self-modify (es meta)
- #91 cost-guards (toca billing)
- #92 backup (toca data)

---

## Convenciones del repo

- **Branches:** `feat/<issue>-<slug>` / `fix/<issue>-<slug>`
- **Commits:** Conventional Commits (`feat/fix/docs/chore`)
- **CI:** ruff lint+format + pytest coverage ≥40% (gateway) antes de merge
- **Reviews:** Copilot como reviewer automático en todos los PRs
- **Migrations:** `sql/migrations/NNN-<nombre>.sql` — aplicar manualmente con `docker compose exec lina-db psql -U lina -d lina -f /migrations/NNN-...sql`
- **MCPs nuevos:** Clean Architecture — `domain/ application/ infrastructure/ server.py`
- **Tests E2E:** `tests/e2e/telegram/.venv/bin/python` — requiere credenciales en env