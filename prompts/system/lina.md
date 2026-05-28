# LINA — System Prompt (fuente de verdad)

> **Nota operacional**: Este archivo es la fuente de verdad documentada.
> El system prompt activo de Goose se inyecta via `AGENTS.md` (auto-cargado como hints).
> Si editás este archivo, reflejá los cambios en `AGENTS.md`.
>
> Mecanismo técnico: `prompt_manager.rs` carga `.goosehints`/`AGENTS.md` del cwd
> como `system_prompt_extras["hints"]` → se append como `# Additional Instructions:`.

---

## Secciones del system prompt

1. [Identidad](#1-identidad)
2. [Idioma y tono](#2-idioma-y-tono)
3. [Formato Telegram](#3-formato-telegram)
4. [Reglas de comportamiento](#4-reglas-de-comportamiento--responsabilidad)
5. [Política de herramientas](#5-política-de-herramientas)
6. [Auto-conocimiento y límites](#6-auto-conocimiento-y-límites)
7. [Convenciones del repo](#7-convenciones-operacionales-del-repo)
8. [Contexto del usuario](#8-contexto-del-usuario)

---

## Análisis de base (scores sesión 2026-05-27)

| Dimensión | Score | Debilidad principal |
|---|:---:|---|
| Performance | 7.5 | Errores técnicos y latencia |
| Inteligencia | 8.5 | — |
| Soltura | 7.0 | Mezcla inglés/español en thinking |
| **Responsabilidad** | **5.5** | Auto-reinicios, no honra `/stop`, no reporta degradación |
| **Velocidad** | **6.0** | Latencia entre tool calls, no heartbeat |
| **Promedio** | **6.9** | Objetivo ≥8.5 en todos |

Fallos documentados:
- F-3: Bloqueo silencioso 18:17–18:23 (14 mensajes `/stop` sin respuesta)
- F-5: Auto-reinicio 3× durante quiz activo
- F-6: 268 bubbles de razonamiento vs ~173 respuestas finales (60% es monólogo interno)
- F-8: Typewriter por palabra, no por carácter

---

## Historial de revisiones

| Fecha | Autor | Cambio |
|---|---|---|
| 2026-05-27 | federico-dominguez | v1.0 — primera versión tras análisis sesión |

---

## Notas de implementación

### Por qué AGENTS.md y no override

El `PromptManager` de Goose soporta `system_prompt_override` (reemplaza TODO el base prompt).
No usamos override porque:
1. El base `system.md` de Goose contiene lógica crítica (tool listing, mode handling).
2. `AGENTS.md` se inyecta como "Additional Instructions" — complementa sin reemplazar.
3. Mantener la base de Goose permite actualizaciones sin reescribir el prompt desde cero.

### Limite de AGENTS.md en contexto

Goose no trunca `AGENTS.md`. Pero contextos muy largos aumentan latencia y costo de tokens.
Target: AGENTS.md ≤ 200 líneas. Detalles de referencia van aquí en `lina.md`.

### Referencia de formato completa

Ver: `prompts/system/telegram-format.md`
