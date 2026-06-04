# Reporte de Estado del Proyecto LINA

**Fecha:** 2026-06-01 16:55 UTC
**Generado por:** Sub-agente research (session: 3cfe9f59...)

---

## 1. Resumen Ejecutivo

LINA está en **Fase 4 de desarrollo** (multi-agente). Las Fases 1-3 están completadas (Docker stack, containerización de MCPs, hardening producción). La Fase 3.5 (memoria e inteligencia) está completa en un 57% (4/7 issues mergeados). Actualmente el equipo está enfocado en **v0.4 — Multi-agent core**, con branches activas en los issues clave (#82, #83, #84, #85).

---

## 2. Estado por Milestone

| Milestone | Estado | Issues totales | Completados | Progreso |
|---|---|---|---|---|
| **v0.4 — Multi-agent core** | 🟢 EN PROGRESO (NEXT) | 4 + 1 epic | 0/4 | 0% |
| **v0.4.1 — Telegram UX subagentes** | 🔴 BLOQUEADO | 4 | 0/4 | 0% |
| **v0.5 — Self-modify + Hot-reload** | 🟡 PARALELO POSIBLE | 2 | 0/2 | 0% |
| **v0.5.1 — Observabilidad + guardrails** | 🟡 PARALELO POSIBLE | 4 | 0/4 | 0% |
| **v0.6 — Multimodal + Proactivo** | 🔵 INDEPENDIENTE | 4 | 0/4 | 0% |
| **v0.7 — LINA as MCP server** | 🔴 BLOQUEADO | 1 | 0/1 | 0% |
| **CI/CD hardening** | 🟢 PUEDE EMPEZAR YA | 3 + extras | 2/5 | ~40% |

---

## 3. Fases Completadas

### Fase 1 — Docker Stack (✅)
- goosed + gateway + MCPs + PostgreSQL en Docker Compose
- Session persistence en Postgres (PR #56)
- Hot-swap error isolation (PR #57)
- E2E testing con TelegramTestClient (PR #28)

### Fase 2 — Containerización MCPs (✅)
- Dockerfile.mcp multi-stage para todos los MCPs
- 6 servicios MCP en puertos 8101-8106
- Transporte Streamable HTTP (ADR 0005)
- Smoke tests de contenedores

### Fase 3 — Hardening Producción (✅)
- ADR 0009: read_only containers, cap_drop ALL, no-new-privileges
- nginx gateway con rate limiting (shell: 30 req/min, moodle: 20, resto: 120)
- Schema `audit` en PostgreSQL con vistas de resumen diario
- Backups automáticos horarios con retención de 24h
- Health checks y auto-restart
- Logging JSON estructurado

### Fase 3.5 — Memoria e Inteligencia (⚠️ 57%)
**Completados (mergeados):**
- ✅ #60 Smart context summarization (~700 tokens warmup vs 4000+ antes)
- ✅ #61 Token/cost metering (DeepSeek real data, cost by session)
- ✅ #62 Reasoning trace persistence (bloques `<think>` en DB)
- ✅ #63 pgvector semantic memory (búsqueda por significado, cosine similarity)

**Pospuestos a backlog:**
- 🔲 #64 Image understanding (vision)
- 🔲 #65 TTS voice responses
- 🔲 #66 Proactive scheduler

---

## 4. Estado Actual — v0.4 Multi-agent Core 🟢 EN PROGRESO

### Issues activos con branches:

| Issue | Branch | Descripción | Estado |
|---|---|---|---|
| #82 | `feat/82-policies-yaml` | `config/policies.yaml` + loader pydantic | 🟡 En desarrollo |
| #83, #84, #85 | `feat/83-84-85-multi-agent-spawner` | Migration 009, spawner goosed-per-subagent, MCP orchestrator | 🟡 En desarrollo |
| #48 | `feat/48-mcp-hot-reload` | Hot-reload de MCPs sin reiniciar goosed | 🟡 Puede avanzar en paralelo |
| #79 | `feat/79-docker-build-ci` | Build de imágenes Docker en cada PR | 🟡 En desarrollo |
| #91 | `feat/91-agent-task-recipe` | Recipes para tareas de sub-agentes | 🟡 En desarrollo |
| — | `chore/roadmap-restructure-2026-06` | Reestructura roadmap → milestones v0.4 a v0.7 | ✅ Mergeada |

### Issues abiertos relevantes (sin branch aún):

| # | Título | Milestone | Prioridad |
|---|---|---|---|
| #86 | Comando `/agents` (dashboard live) | v0.4.1 | 🔴 Bloqueado por v0.4 |
| #87 | Comandos `/kill /pause /resume /replan` | v0.4.1 | 🔴 Bloqueado por v0.4 |
| #88 | HumanInputRequest → botones inline | v0.4.1 | 🔴 Bloqueado por v0.4 |
| #89 | Failure modes (timeouts, DLQ, backoff) | v0.4.1 | 🔴 Bloqueado por v0.4 |
| #112 | Comando `/kill <id>` en gateway | v0.4.1 | 🔴 Bloqueado (split de #87) |
| #113 | Watchdog — enforce max_runtime_minutes | v0.4.1 | 🔴 Bloqueado por #84 |
| #114 | E2E tests → repo sandbox dedicado | — | 🟡 Puede empezar |
| #115 | Sub-agentes deben pollear get_pending_instructions | v0.4.1 | 🔴 Bloqueado por #85 |
| #51 | lina-self-modify ciclo completo | v0.5 | 🔴 Bloqueado por #85, #48, #88 |
| #67 | Prometheus + Grafana + alerts | v0.5.1 | 🟡 Paralelo posible |
| #91 | Rate-limiting + cost-guards por rol | v0.5.1 | 🟡 Paralelo posible |
| #92 | Backup automatizado + restore drill | v0.5.1 | 🟡 Paralelo posible |
| #93 | Comando `/audit` en Telegram | v0.5.1 | 🟡 Paralelo posible |
| #90 | MCP lina-bridge (LINA como MCP server) | v0.7 | 🔴 Bloqueado por v0.4 + v0.4.1 + #67 |
| #104 | test(gns3): aumentar coverage 60→75% | — | 🟡 PR #105 abierto |
| #105 | (PR) feat: gns3 coverage 76→95% + recipes | — | 🔍 Abierto, sin mergear |
| #80 | Integration tests con MCP spawn real | — | 🟡 Puede empezar |
| #81 | Coverage agregado + gate global 60% | — | 🟡 Puede empezar |
| #64-66, #68 | Multimodal + Android | v0.6 | 🔵 Independiente |

---

## 5. Branches Activas

| Branch | Último commit | Propósito |
|---|---|---|
| `feat/82-policies-yaml` | 96c32c4 | Configuración de políticas multi-rol |
| `feat/83-84-85-multi-agent-spawner` | 2aff1d8 | Core del orquestador multi-agente |
| `feat/48-mcp-hot-reload` | 2e2d3aa | Hot-reload de MCP containers |
| `feat/79-docker-build-ci` | 51ff8e1 | CI para build de imágenes Docker |
| `feat/91-agent-task-recipe` | 2a605d3 | Recipes de tareas para sub-agentes |
| `chore/roadmap-restructure-2026-06` | 82d63b4 | Roadmap reestructurado (prob. mergeada) |
| `main` | 4d269c3 | Rama principal |

**Observación:** La branch `feat/83-84-85-multi-agent-spawner` es la más crítica — concentra 3 issues en una sola branch. Riesgo de merge grande.

---

## 6. Dependencias entre Milestones (DAG)

```
CI/CD (#79, #80, #81) ──────── paralelo permanente, sin blockers

v0.4 ──> v0.4.1 ──> v0.7
   │       │
   │       └──> v0.5 (#48 paralelo) ──┐
   │                                   ├──> v1.0
   └────── v0.5.1 (paralelo a v0.5) ──┤
   └────── v0.6 (paralelo) ───────────┘
```

**Dependencias clave que bloquean el avance:**
1. **#48 (hot-reload)** puede avanzar en paralelo → desbloquea parte de #51
2. **#82-85 (v0.4)** son el blocker principal → sin esto, nada del multi-agente funciona
3. **v0.4.1** está 100% bloqueado por v0.4
4. **v0.7** depende de v0.4 + v0.4.1 + #67

---

## 7. Arquitectura Actual (desde ADRs)

### ADRs existentes:
| # | Título | Estado |
|---|---|---|
| 0001 | Clean Architecture para MCPs | ✅ Aceptado |
| 0002 | Estrategia de rendering UI (Telegram) | ✅ Aceptado |
| 0003 | Android control via ADB bridge | ✅ Aceptado |
| 0004 | Telegram thinking display | ✅ Aceptado |
| 0005 | Transport MCP: stdio → Streamable HTTP | ✅ Aceptado |
| 0006 | Image Base Policy (distroless) | ✅ Aceptado |
| 0007 | Secrets Strategy (keyring → Docker secrets → sops) | ✅ Aceptado |
| 0008 | Excepción de cobertura para lina-moodle (18%) | ✅ Aceptado |
| 0009 | Fase 3: Hardening a nivel producción | ✅ Aceptado |
| 0010 | Framework Multi-Agente: mcp-agent + Orchestrator-Workers | ✅ Aceptado |

### Stack actual:
- **Runtime:** Goose 1.35.0-canary (fork patched) como systemd service
- **Provider:** DeepSeek V4 Flash (hot-swap a Pro/Chat/Reasoner)
- **Gateway:** Telegram (túnel saliente, sin puerto público)
- **MCPs activos (10):** github, gitlab, moodle, fs-safe, shell-policy, secrets, lina-db, gcalendar, systemd-user, gns3
- **Infra:** Docker Compose con 6 MCPs containerizados + nginx gateway + PostgreSQL + backups
- **Memoria:** Smart context summarization + pgvector semantic memory + reasoning trace persistence

---

## 8. Cambios Recientes (CHANGELOG)

| Versión | Fecha | Highlights |
|---|---|---|
| Unreleased | — | Fase 3 hardening (nginx, audit schema, backups) |
| v0.9.0 | 2026-05-27 | Fix empty thinking bubble, DeepSeek 400 fix, code truncation |
| v0.8.0 | 2026-05-26 | MCP moodle, systemd-user, lina-doctor, AGENTS.md |
| v0.7.0 | 2026-05-25 | Core MCPs Wave 1, Telegram gateway, whisper TTS |

---

## 9. Recomendaciones

### Prioridad 1 — Inmediata: Completar v0.4 Core
- **#82 (policies.yaml)** y **#83-85 (spawner)** deberían mergearse primero. Son el cuello de botella de todo el roadmap.
- La branch `feat/83-84-85-multi-agent-spawner` concentra demasiado — considerar dividir en 2 PRs más pequeños para facilitar review.
- Una vez mergeado v0.4, LINA ya puede spawnear sub-agentes desde Telegram → gran hito visible.

### Prioridad 2 — Desbloquear v0.4.1 y PR #105
- **PR #105** (gns3 coverage 76→95%) debería mergearse rápido — está listo, tests pasan, desbloquea issue #104.
- **#115** (sub-agentes pollean instructions) debería implementarse pronto — es un fix a AGENTS.md que afecta cómo operan los sub-agentes actualmente (incluyéndome a mí ahora mismo).

### Prioridad 3 — Arrancar paralelos
- **#48 (hot-reload)** está en branch activa y no depende de nadie → avanzar.
- **#79 (Docker build CI)** y **#80 (integration tests)** son transversales, sin dependencias → empezar ya.
- **#93 (/audit)** es relativamente simple (solo consultar DB existente) → buen quick win de observabilidad.

### Prioridad 4 — Atención a issues post-Fase 3.5
- **#114** (E2E tests → repo sandbox) es urgente porque los issues #106-#111 son ruido de tests.
- **#112 (/kill <id>)** tiene especificación muy detallada y es un subconjunto de #87 — podría implementarse como PR separado si #87 se atasca.
- **#113 (watchdog)** es crítico para seguridad (evitar que sub-agentes corran indefinidamente).

### Recomendaciones estratégicas
1. **No dispersarse en v0.6 (multimodal)** hasta que v0.4 + v0.4.1 estén operativos — la capacidad de spawn sub-agentes es más valiosa que la visión.
2. **El hot-reload (#48)** debería priorizarse junto con v0.4 — va a hacer toda la diferencia en velocidad de iteración.
3. **Moodle coverage** sigue en 18% (ADR 0008) — la deuda técnica es aceptable hasta Fase 2 de Moodle.
4. **Backup + restore drill (#92)** — crucial para no perder datos de memoria/sesiones acumulados.
5. **El milestone v0.7 (LINA as MCP server)** está muy lejano — no planificar hasta tener v0.4 + v0.4.1 estables.

---

## 10. Riesgos Identificados

| Riesgo | Impacto | Probabilidad | Mitigación |
|---|---|---|---|
| Branch `feat/83-84-85` muy grande → merge conflict | Alto | Media | Dividir en PRs más pequeños |
| mcp-agent (framework joven) puede tener breaking changes | Medio | Baja | API pequeña, fallback a ejecución directa |
| Sin hot-reload, cada cambio de MCP requiere reinicio | Medio | Alta (hoy) | Priorizar #48 en paralelo |
| Costo DeepSeek sin guardrails | Alto | Media | #91 (cost-guards) debería implementarse pronto |
| Perder datos de sesiones/memoria sin backup robusto | Alto | Media | #92 backup cifrado + restore drill |

---

*Reporte generado por sub-agente `research` (session: 3cfe9f59cf32499eb5718bf4de58bdab)*
