# LINA — Roadmap de desarrollo

**Última actualización:** 2026-05-31  
**Estado actual:** Fase 3 completada (hardening + session persistence). En curso: Fase 3.5.

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

## Fase 3.5 — Memoria e Inteligencia

> Objetivo: que LINA recuerde mejor, razone más y perciba más canales.  
> Orden sugerido (cada issue es independiente, hacer en este orden maximiza valor acumulado).

| Prioridad | # | Issue | Qué resuelve | Estado |
|---|---|---|---|---|
| 1 | [#60](https://github.com/federico-dominguez/lina/issues/60) | Smart context summarization | Reemplaza inyección cruda de 20 msgs por summary estructurado | 🔲 siguiente |
| 2 | [#61](https://github.com/federico-dominguez/lina/issues/61) | Token/cost metering | Tracking de costo USD por sesión | 🔲 |
| 3 | [#62](https://github.com/federico-dominguez/lina/issues/62) | Reasoning trace persistence | Guarda bloques `<think>` en DB para auto-análisis | 🔲 |
| 4 | [#63](https://github.com/federico-dominguez/lina/issues/63) | pgvector semantic memory | Búsqueda por significado en recuerdos | 🔲 |
| 5 | [#64](https://github.com/federico-dominguez/lina/issues/64) | Image understanding | Procesa fotos/capturas enviadas a Telegram | 🔲 |
| 6 | [#65](https://github.com/federico-dominguez/lina/issues/65) | TTS voice responses | LINA responde con nota de voz cuando Federico habla | 🔲 |
| 7 | [#66](https://github.com/federico-dominguez/lina/issues/66) | Proactive scheduler | Mensajes programados sin intervención de Federico | 🔲 |

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

## Fase 4 — Autonomía Plena

> Prerequisito: Fase 3.5 completa (especialmente #61 y #62 para observabilidad del agente).  
> Orden estricto por dependencias:

| Orden | # | Issue | Prerequisito |
|---|---|---|---|
| 1 | [#15](https://github.com/federico-dominguez/lina/issues/15) | ADR 0008 — elegir framework multi-agente | — |
| 2 | [#48](https://github.com/federico-dominguez/lina/issues/48) | Hot-reload MCPs sin reiniciar goosed | — |
| 3 | [#67](https://github.com/federico-dominguez/lina/issues/67) | Observability stack (Prometheus + Grafana) | rec. #61 |
| 4 | [#51](https://github.com/federico-dominguez/lina/issues/51) | lina-self-modify — ciclo completo de automejora | #48 |
| 5 | [#50](https://github.com/federico-dominguez/lina/issues/50) | Multi-agente con lina-orchestrator | #15 + #51 |
| 6 | [#68](https://github.com/federico-dominguez/lina/issues/68) | lina-android-remote — control del celular | opc. #64 |

---

## Convenciones del repo

- **Branches:** `feat/<issue>-<slug>` / `fix/<issue>-<slug>`
- **Commits:** Conventional Commits (`feat/fix/docs/chore`)
- **CI:** ruff lint+format + pytest coverage ≥40% (gateway) antes de merge
- **Reviews:** Copilot como reviewer automático en todos los PRs
- **Migrations:** `sql/migrations/NNN-<nombre>.sql` — aplicar manualmente con `docker compose exec lina-db psql -U lina -d lina -f /migrations/NNN-...sql`
- **MCPs nuevos:** Clean Architecture — `domain/ application/ infrastructure/ server.py`
- **Tests E2E:** `tests/e2e/telegram/.venv/bin/python` — requiere credenciales en env
