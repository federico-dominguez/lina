# ADR 0010 — Framework Multi-Agente: mcp-agent + Orchestrator-Workers

- **Estado:** Aceptado
- **Fecha:** 2026-05-31
- **Cierra:** #15
- **Supersede:** ninguno
- **Relacionado:** #48, #50, #51, #67

---

## Contexto

Fase 1–3 dejaron a LINA como **agente único**: un proceso `goosed` con DeepSeek
V4 que carga todos los MCPs (~10) en su contexto y atiende a Federico vía
Telegram. Esto funciona para tareas seriales, pero rompe en Fase 4:

- No puede ejecutar tareas en paralelo (research + dev simultáneos).
- No tiene aislamiento: un MCP destructivo de "ops" está siempre cargado
  aunque la sesión sea de "study".
- No tiene control granular: Federico no puede pausar, matar o reorientar
  una sub-tarea sin abortar toda la sesión.
- No escala a auto-mejora (#51): self-modify necesita ejecutar en un sandbox
  con MCPs limitados, no en el contexto principal.

La meta de Fase 4 es que LINA se convierta en **manager de un grupo de
subagentes especializados**, con control desde Telegram (estado, comandos,
kill, replan) y políticas de acceso por rol.

---

## Decisión

**Framework adoptado:** [`mcp-agent`](https://github.com/lastmile-ai/mcp-agent)
(LastMile AI, 8.3k★, Apache-2.0) ejecutando el patrón **Orchestrator-Workers**
de Anthropic ([*Building Effective Agents*](https://www.anthropic.com/engineering/building-effective-agents)).

**Runtime de ejecución:**
- **`asyncio`** por defecto (Fases B–D).
- **`Temporal`** opt-in en Fase E (#51 self-modify) cuando se requiera
  durabilidad cross-restart real.

**Aislamiento:** cada subagente corre como un **proceso `goosed` independiente**
(no como tarea en el mismo proceso) para garantizar contexto aislado, MCPs
filtrados por política, y `kill -9` limpio.

---

## Alternativas evaluadas

| Framework | MCP-native | Peso Docker | Durable | Manager UI | Streaming | ★ |
|---|---|---|---|---|---|---|
| **mcp-agent + Temporal** | ✅ nativo | ~20MB (asyncio) / ~80MB (temporal) | ✅ Temporal | parcial | ✅ token watchers | 8.3k |
| LangGraph | ⚠️ adaptador | ~200MB | ✅ checkpoint pg | ❌ | ✅ `astream` | 100k |
| OpenAI Agents SDK | ⚠️ util MCP | ~50MB | ❌ | ❌ | ✅ | reciente |
| CrewAI | ❌ | ~150MB | ❌ | parcial | ⚠️ | mediana |
| Google ADK | ❌ | ~80MB | ❌ | ❌ | ⚠️ | pequeña |

### Razones de la elección

1. **MCP-native** — LINA ya es 100% MCP. `mcp-agent` carga MCPs sin adaptadores
   ni wrappers; los demás requieren capas extra. Esto reduce superficie de bugs
   y mantiene un solo modelo mental.
2. **Patrones Anthropic out-of-the-box** — implementa los 6 patrones
   (Router, Parallel, Orchestrator-Workers, Evaluator-Optimizer, Intent
   Classifier, Swarm) como `AugmentedLLM` componibles. No tenemos que escribir
   el grafo manualmente.
3. **Peso** — ~10× más liviano que LangGraph. La imagen `lina-orchestrator`
   se mantiene < 200MB total vs ~400MB con LangGraph.
4. **Subagentes como MCP servers** — `mcp-agent` permite exponer una app entera
   como un MCP server (`create_mcp_server_for_app`). Esto significa que cada
   subagente especializado puede ser llamado por LINA como una `tool` más,
   sin protocolo nuevo.
5. **Human-in-the-loop nativo** — `HumanInputRequest` permite que un subagente
   bloquee esperando aprobación de Federico vía Telegram (botones inline). Es
   exactamente lo que necesitamos para `needs_approval_for: [git_push, ...]`.
6. **Token streaming** — `TokenCounter.watch()` con callbacks permite enviar
   progreso en vivo al panel de Telegram sin polling.
7. **Temporal opt-in** — cuando self-modify (#51) requiera recuperar workflows
   tras un restart de goosed, switcheamos `execution_engine: temporal` sin
   tocar código de los agentes. Mientras tanto, asyncio es suficiente y no
   añade infra.

### Por qué NO LangGraph (la elección obvia)

- Es excelente, pero **no es MCP-first**. Requiere adapter `langchain-mcp-adapters`
  que añade latencia y duplicación de schemas.
- 200MB+ de deps (`langchain-core`, `langchain-community`, `langgraph`,
  `langgraph-checkpoint-postgres`) impactan tiempo de cold-start de goosed y
  tamaño de imagen.
- El grafo de estado explícito es más rígido que el modelo de
  workflows/decorators de `mcp-agent` para iteración rápida.
- La capa A2A SDK (que justificaba LangGraph para "futureproofing") aún es
  experimental; podemos integrarla más adelante sobre cualquier framework.

### Por qué NO OpenAI Agents SDK

- Es nuevo (post-2025) y está optimizado para Responses API + sandbox propio
  de OpenAI. LINA usa DeepSeek + MCPs propios; el match es bajo.
- No tiene patrón Orchestrator-Workers explícito (usa "handoffs" que es más
  parecido a Swarm).

---

## Arquitectura objetivo

```
                    Federico (Telegram)
                          │
                  ┌───────┴────────┐
                  │  lina-gateway   │
                  │  (panel + cmds) │
                  └────────┬────────┘
                           │
                  ┌────────┴────────────┐
                  │   lina-orchestrator │  ◄── mcp-agent + Postgres
                  │   (Manager LLM)     │      orchestrator schema
                  └─┬──────┬──────┬─────┘
        spawn       │      │      │       kill / pause / inject
   ┌────────────────┘      │      └─────────────────────┐
   ▼                       ▼                            ▼
┌──────────┐         ┌──────────┐                ┌──────────┐
│ goosed   │         │ goosed   │     ...        │ goosed   │
│ dev      │         │ ops      │                │ study    │
│ MCPs:    │         │ MCPs:    │                │ MCPs:    │
│ github,  │         │ shell,   │                │ moodle,  │
│ fs-safe, │         │ systemd, │                │ gcal,    │
│ ...      │         │ ...      │                │ ...      │
└──────────┘         └──────────┘                └──────────┘
   (allowlist por política en config/policies.yaml)
```

**Flujo:**
1. Federico manda mensaje → `lina-gateway` lo pasa al manager.
2. Manager clasifica intención + arma plan (orchestrator pattern de mcp-agent).
3. Manager llama `spawn_agent(role, goal)` por cada subtask → cada uno arranca
   en su propio `goosed` con MCPs filtrados.
4. Subagentes emiten eventos (`progress`, `tool_call`, `result`) a
   `orchestrator.agent_events` (Postgres LISTEN/NOTIFY).
5. `lina-gateway` escucha NOTIFY → actualiza panel en Telegram en vivo.
6. Federico puede mandar `/tell <id> <msg>`, `/pause <id>`, `/kill <id>`,
   `/replan <...>`. El manager los traduce a comandos en
   `orchestrator.agent_commands`.
7. Cuando todos los subagentes terminan, el manager consolida y responde.

---

## Patrón de orquestación: Orchestrator-Workers

Citando a Anthropic:

> *"In the orchestrator-workers workflow, a central LLM dynamically breaks down
> tasks, delegates them to worker LLMs, and synthesizes their results."*

Es la opción correcta para LINA porque:
- Las subtareas no se pueden predecir (cada mensaje de Federico es distinto).
- Necesita síntesis (Federico quiere una sola respuesta, no 3 por separado).
- Soporta paralelismo cuando las subtareas son independientes.
- Es exactamente como funciona Claude Code internamente.

Patrones complementarios disponibles vía mcp-agent (uso futuro):
- **Router** — para preguntas triviales, evitar el overhead de spawn.
- **Evaluator-Optimizer** — para self-modify (#51), iterar hasta que tests pasen.
- **Parallel (Map-Reduce)** — para research en N fuentes.

---

## Política de acceso ("modelo empresa")

Cada subagente recibe una **política** declarada en
`config/policies.yaml` (archivo creado en Fase B — aún no existe en el repo):

```yaml
roles:
  dev:
    allowed_mcps: [github, gitlab, fs-safe, shell-policy, lina-db, secrets]
    sudo_allowed: false
    max_runtime_minutes: 30
    max_tokens_per_run: 50000
    needs_approval_for: [git_push, pr_merge, file_delete]
  ops:
    allowed_mcps: [shell-policy, systemd-user, fs-safe, secrets, lina-db]
    sudo_allowed: true
    sudo_allowlist: [apt-get, lina-deploy]
    needs_approval_for: [restart_goosed, modify_compose]
  study:
    allowed_mcps: [moodle, gcalendar, fs-safe, lina-db]
    needs_approval_for: [submit_quiz]
  research:
    allowed_mcps: [duckduckgo, fs-safe, lina-db]
```

Enforcement multi-capa:
1. `mcp_allowlist` en `mcp-agent` → solo conecta MCPs permitidos.
2. `lina-shell-policy` → sudo allowlist por rol.
3. `lina-fs-safe` → paths permitidos por rol.
4. Hard limits (timeout, tokens) impuestos por mcp-agent + Temporal.
5. `needs_approval_for` → bloquea con `HumanInputRequest` → botones en Telegram.

---

## Consecuencias

**Positivas**
- LINA escala a paralelismo real sin reescribir el gateway.
- Aislamiento por proceso = un subagente corrupto no contamina a otros.
- Políticas declarativas auditables (un YAML, no código).
- Self-modify (#51) se vuelve trivial: es solo un subagente `role=dev` con
  política especial.
- Stack 100% MCP-native, alineado con tendencia 2026.

**Negativas**
- Overhead de spawn de proceso goosed (~1–3s) — mitigable con pool de workers.
- mcp-agent es joven (v0.0.21, may-2025); curva de aprendizaje + riesgo de
  breaking changes. Mitigado por: API pequeña, comunidad creciente, fallback a
  ejecución directa si rompe.
- Más infra que correr: 1 orquestador + N goosed. Compensado por imagen
  pequeña de mcp-agent y reutilización del goosed existente.

**Migración**
- LINA actual sigue funcionando exactamente igual hasta que el orquestador esté
  en producción (Fase B). El gateway elige entre "modo directo" (hoy) y "modo
  manager" (con orquestador) vía feature flag.
- Migración full una vez verificados los 7 acceptance tests de Fase C.

---

## Plan de implementación (referencia)

| Fase | Issues | Entregable |
|---|---|---|
| A — ADR | #15 | Este documento + prototipo |
| B — Infra | #50 (1/2) | `mcps/orchestrator/`, migration 009, `policies.yaml` |
| C — UX | #50 (2/2) | Panel Telegram, comandos, notificaciones proactivas |
| D — Hot-reload | #48 | `reload_mcp()`, `register_mcp()` |
| E — Self-modify | #51 | Subagente `dev` con aprobaciones |
| F — Observabilidad | #67 | Prometheus + Grafana |

Ver [docs/roadmap.md](../roadmap.md) para el orden de fases y dependencias.

---

## Referencias

- [Anthropic — Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) (Dec 2024)
- [mcp-agent — GitHub](https://github.com/lastmile-ai/mcp-agent) — 8.3k★, Apache-2.0
- [mcp-agent docs](https://docs.mcp-agent.com/)
- [OpenAI Agents SDK — Multi-agent](https://openai.github.io/openai-agents-python/multi_agent/) (comparativo)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Temporal](https://temporal.io/) — runtime durable opt-in
- ADRs relacionados: [0001-clean-architecture.md](0001-clean-architecture.md), [0005-mcp-transport.md](0005-mcp-transport.md), [0007-secrets-strategy.md](0007-secrets-strategy.md)
