# Runbook 0002 — DeepSeek V4 `reasoning_content` 400: Backfill Defensivo (Regresión Post-Fase 9)

**Fecha:** 2026-05-27
**Estado:** ✅ Resuelto en producción
**Servicio afectado:** `lina-goosed.service`
**Severidad:** Alta — bloqueaba toda conversación tras el primer tool_call en sesiones largas
**Regresión de:** [Runbook 0001](./0001-deepseek-v4-reasoning-bug-fix.md)
**Constraint del usuario:** *"la capacidad de razonamiento de LINA está prohibido sacársela"* — el modo `thinking: enabled` permanece activo. La solución NO desactiva razonamiento.

---

## TL;DR

Tras el deploy de la Fase 9, el bot volvió a fallar con:

```
Request failed: Bad request (400):
The reasoning_content in the thinking mode must be passed back to the API.
```

El parche original (runbook 0001) ya inyectaba `reasoning_content` durante el formateo de mensajes (`format_messages_with_options`). El problema: esa función tiene **ramas (`continue`, splits, mensajes sintéticos) que pueden dejar a un mensaje `assistant` sin la clave**, y entonces la API rechaza el request entero.

**Fix:** Backfill defensivo e idempotente en `sanitize_request_for_compat` (capa más cercana al envío HTTP). Después de inyectar `thinking: enabled`, recorrer `messages[]` y añadir `reasoning_content: ""` a cualquier `assistant` que no la tenga. Es la última línea de defensa antes del wire.

Junto a este fix se entregan dos correcciones colaterales descubiertas en la misma sesión:

1. **Burbuja vacía** (`Bad Request: message text is empty`) — guard en `edit_text` del gateway de Telegram.
2. **Code blocks truncados sin expansión** — `smart_args_preview` recorta explícitamente a 120 chars para que Telegram no inyecte su propio `…` no expandible.

---

## Tabla de contenidos

1. [Síntomas](#1-síntomas)
2. [Root cause analysis](#2-root-cause-analysis)
3. [Diseño de la solución](#3-diseño-de-la-solución)
4. [Cambios aplicados](#4-cambios-aplicados)
5. [Tests](#5-tests)
6. [Build, deploy y verificación](#6-build-deploy-y-verificación)
7. [Cómo prevenir que vuelva a pasar](#7-cómo-prevenir-que-vuelva-a-pasar)
8. [Anexo: fixes colaterales (UI Telegram)](#8-anexo-fixes-colaterales-ui-telegram)

---

## 1. Síntomas

Tres regresiones reportadas por el usuario tras el deploy de Fase 9:

| # | Síntoma observado en Telegram                                                              | Origen                                |
|---|--------------------------------------------------------------------------------------------|---------------------------------------|
| A | Burbuja de "pensando…" se queda vacía y el bot dice `Bad Request: message text is empty`   | `run_pacer` → `edit_text` con HTML vacío |
| B | Tras un tool_call, la siguiente turno responde **`Request failed: Bad request (400)`**     | DeepSeek API rechaza historial        |
| C | Args largos en headers de tool (e.g. `code: "import os\\n..."`) se truncan con `…` no expandible | `smart_args_preview` devolvía args completos en `<code>` inline |

El error B en logs del servicio:

```
ERROR goose::providers::openai: DeepSeek API error: HTTP 400
The reasoning_content in the thinking mode must be passed back to the API.
```

---

## 2. Root cause analysis

### 2.1 Por qué falló el parche original

El runbook 0001 introdujo `sanitize_request_for_compat` en [crates/goose/src/providers/openai.rs](https://github.com/block/goose) que:

1. Inyectaba `thinking: { type: "enabled" }` en el body.
2. Confiaba en que `format_messages_with_options` populara `reasoning_content` en cada mensaje `assistant`.

El bug latente: **`format_messages_with_options` tiene múltiples ramas que pueden saltarse la inserción**, entre otras:

- El branch `continue` (~líneas 403-406 de `openai.rs`) en mensajes que ya tienen tool_calls separados — no marca `session_has_thinking=true` y por tanto los siguientes mensajes assistant no reciben el `""` por defecto.
- Mensajes sintéticos creados por el orquestador (e.g. al hacer split de un assistant grande en chunks tool_call + text).
- Historiales migrados desde sesiones anteriores al parche.

Resultado: **una sola omisión** basta para que DeepSeek devuelva 400 y rompa el turno entero. La probabilidad sube con la longitud de la sesión.

### 2.2 Lección arquitectónica

Confiar en que la capa de **formateo** mantenga un invariante exigido por la API es frágil: cualquier refactor o caso de borde rompe el contrato. La validación debe vivir en la capa más cercana al cliente HTTP (sanitización pre-envío), donde el invariante es:

> *"Cualquier `assistant` en `messages[]` debe tener `reasoning_content` cuando `thinking.type == "enabled"`."*

---

## 3. Diseño de la solución

**Principio:** *defense in depth*. El backfill en `sanitize_request_for_compat`:

- Es **idempotente**: si la clave ya existe (con razonamiento real), no la toca.
- Es **API-correct**: `""` es aceptado por DeepSeek y coincide con la convención que ya usa `format_messages_with_options` para turnos vacíos.
- Es **última línea de defensa**: si una refactorización futura rompe el formato, el sanitizer sigue garantizando el invariante.
- **No desactiva razonamiento** — solo añade la clave faltante. La lógica de `disabled` queda intacta y separada.

---

## 4. Cambios aplicados

Todos los archivos viven en el árbol Goose: `/home/fede/src/goose`.

### 4.1 Backfill defensivo — `crates/goose/src/providers/openai.rs`

En la rama `enabled` de `sanitize_request_for_compat`, tras inyectar `thinking`, recorrer `messages` y backfillear:

```rust
// Safety-net backfill: DeepSeek V4 in thinking-enabled mode rejects ANY
// assistant message in history that lacks a `reasoning_content` key with
// HTTP 400 ("The reasoning_content in the thinking mode must be passed back
// to the API."). `format_messages_with_options` already tries to populate it,
// but edge cases (split tool-call messages, synthetic assistant turns,
// sessions migrated from before the patch, or `continue` branches that skip
// marking `session_has_thinking`) can leave an assistant message without the
// field. Backfill an empty string — the API accepts it and our format logic
// already uses the same convention for empty-but-thinking turns.
if let Some(messages) = obj.get_mut("messages").and_then(|m| m.as_array_mut()) {
    for message in messages {
        if let Some(map) = message.as_object_mut() {
            let is_assistant =
                map.get("role").and_then(|r| r.as_str()) == Some("assistant");
            if is_assistant && !map.contains_key("reasoning_content") {
                map.insert(
                    "reasoning_content".to_string(),
                    serde_json::json!(""),
                );
            }
        }
    }
}
```

### 4.2 Guard de HTML vacío — `crates/goose/src/gateway/telegram.rs` (fix síntoma A)

Al inicio de `edit_text`, antes del bloque de truncado a 4096 chars:

```rust
if html.trim().is_empty() {
    // Telegram rechaza con 400 "message text is empty"; el pacer flushea
    // una última vez aunque el stream haya cerrado sin contenido nuevo
    // (típicamente cuando un tool_call arranca inmediatamente).
    return Ok(());
}
```

### 4.3 Truncado de args en preview — `crates/goose/src/gateway/handler.rs` (fix síntoma C)

`smart_args_preview` ahora colapsa whitespace y limita explícitamente a 120 caracteres con `…`, evitando que Telegram aplique su propio recorte interno dentro del tag `<code>` (que sí es no expandible):

```rust
fn smart_args_preview(args: &serde_json::Value) -> String {
    const MAX: usize = 120;
    let raw = ...; // (extraer arg principal o serialize compacto)
    let collapsed: String = raw.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.chars().count() > MAX {
        let head: String = collapsed.chars().take(MAX).collect();
        format!("{head}…")
    } else {
        collapsed
    }
}
```

El payload completo sigue visible en la burbuja **expandible** de resultado.

---

## 5. Tests

Se añadieron dos tests en `crates/goose/src/providers/openai.rs`:

| Test                                                                                | Verifica                                                                 |
|-------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| `deepseek_v4_backfills_empty_reasoning_content_on_assistant_messages_missing_it`    | Un body con `assistant` sin `reasoning_content` recibe `""` tras sanitize. |
| `deepseek_v4_backfill_does_not_overwrite_existing_reasoning_content`                | Mensajes con razonamiento real se preservan intactos (idempotencia).      |

### Ejecución

⚠️ Las pruebas mutan `LINA_DEEPSEEK_THINKING` con `std::env::set_var` y comparten proceso. **Hay que correrlas con `--test-threads=1`** o sufren race con tests que la limpian con `remove_var`:

```bash
cd /home/fede/src/goose
cargo test --release -p goose --lib providers::openai::tests::deepseek_v4 \
  --no-default-features --features portable-default,system-keyring,code-mode \
  -- --test-threads=1
```

Resultado esperado:

```
test result: ok. 8 passed; 0 failed; 0 ignored
```

> **TODO de hygiene** (no bloqueante): anotar los 4 tests `deepseek_v4_flash_env_var_disabled_*` con `#[serial_test::serial]` para que sean seguros en paralelo. El crate `serial_test` ya está en deps.

---

## 6. Build, deploy y verificación

### 6.1 Build

```bash
cd /home/fede/src/goose
cargo build --release --bin goose \
  --no-default-features \
  --features portable-default,system-keyring,code-mode
```

Duración típica: ~3-5 min. Output binario: `target/release/goose` (~264 MB).

### 6.2 Deploy (one-liner)

```bash
systemctl --user stop lina-goosed.service && sleep 2 && \
  (kill $(fuser /home/fede/.local/bin/goose 2>/dev/null | tr ' ' '\n') 2>/dev/null; sleep 1) && \
  cp /home/fede/src/goose/target/release/goose /home/fede/.local/bin/goose && \
  systemctl --user start lina-goosed.service && sleep 3 && \
  systemctl --user is-active lina-goosed.service
```

Esperado: `active`.

### 6.3 Verificación post-deploy

Mandar un mensaje a Telegram que dispare al menos un tool_call y revisar:

```bash
journalctl --user -u lina-goosed.service --since "5 min ago" --no-pager \
  | grep -iE "error|reasoning_content|400|warn"
```

**Criterios de éxito** (todos deben cumplirse):

- ✅ No aparece `Bad Request: message text is empty`.
- ✅ No aparece `reasoning_content in the thinking mode must be passed back`.
- ✅ No aparece ningún `HTTP 400` de DeepSeek.
- ✅ Las burbujas de tool muestran args truncados con `…` y la burbuja de resultado se puede expandir mostrando el payload completo.

---

## 7. Cómo prevenir que vuelva a pasar

### 7.1 Invariantes que deben mantenerse

| Invariante                                                                          | Dónde se aplica                                  |
|-------------------------------------------------------------------------------------|--------------------------------------------------|
| Todo `assistant` en el body enviado tiene `reasoning_content` (real o `""`)         | `sanitize_request_for_compat` (rama enabled)     |
| `thinking: { type: "enabled" }` se inyecta si el cliente no especifica el modo     | `sanitize_request_for_compat`                    |
| `edit_text` nunca llama a la API de Telegram con `text=""`                          | `crates/goose/src/gateway/telegram.rs`           |
| Args en headers `<code>` ≤ 120 chars                                                | `smart_args_preview` en `handler.rs`             |

### 7.2 Checklist al tocar `format_messages_with_options` u `openai.rs`

- [ ] ¿Añade alguna rama nueva un `continue`/`return` que salte la inserción de `reasoning_content`? Si sí, **NO importa**, el sanitizer lo cubre — pero anótalo aquí.
- [ ] ¿Cambias el sanitizer? Corre `cargo test ... providers::openai::tests::deepseek_v4 -- --test-threads=1`. **Los 8 deben pasar.**
- [ ] ¿Cambias la convención de "razonamiento vacío" de `""` a `null`? Hay que actualizar el backfill **y** los tests **y** validar contra DeepSeek real.

### 7.3 Constraint inviolable

> **Razonamiento DeepSeek = ON.** Cualquier fix que dependa de poner `LINA_DEEPSEEK_THINKING=disabled` es inaceptable. Si el modelo razonando rompe algo, se arregla la integración, no se le quita la capacidad.

### 7.4 Mejoras futuras opcionales

1. **Alerta proactiva**: agregar log `WARN` en el sanitizer cuando se hace backfill — permite detectar regresiones en `format_messages_with_options` antes de que dañen producción:
   ```rust
   tracing::warn!(target: "deepseek_sanitize",
       "backfilled missing reasoning_content on assistant message (#{})",
       index);
   ```
2. **Test de integración wire-level** contra mock de DeepSeek que verifique que `format → sanitize → request` siempre incluye la clave en todos los assistants.
3. **Stricter formato**: hacer que `format_messages_with_options` propague un `Result<_, MissingReasoningContent>` en debug builds para detectar el bug en origen.

---

## 8. Anexo: fixes colaterales (UI Telegram)

### 8.1 Síntoma A — burbuja vacía

**Causa:** `run_pacer` mantiene un `last_hash = 0`. El hash de `("", "")` es no-cero, por lo que el primer flush dispara `edit_text` con HTML vacío cuando el stream cierra antes de generar contenido (típicamente porque un tool_call empezó de inmediato).

**Fix:** early-return en `edit_text` si `html.trim().is_empty()`. Idempotente y sin efectos secundarios — Telegram no necesita ser "actualizado a vacío".

### 8.2 Síntoma C — code blocks truncados sin expansión

**Causa:** Telegram trunca contenido largo dentro de tags `<code>` inline (los headers de tool) con su propio `…`, sin permitir expansión. El payload completo igual viaja en la respuesta del tool, pero no se ve en el header.

**Fix:** `smart_args_preview` ahora controla el truncado del lado del emisor (120 chars + `…`), colapsando whitespace para que multi-line code aparezca como una sola línea legible. La burbuja de resultado expandible muestra el contenido íntegro.

---

## Referencias

- [Runbook 0001 — Parche original `sanitize_request_for_compat`](./0001-deepseek-v4-reasoning-bug-fix.md)
- DeepSeek API: [thinking mode contract](https://api-docs.deepseek.com/)
- Archivos tocados:
  - `crates/goose/src/providers/openai.rs` (backfill + 2 tests)
  - `crates/goose/src/gateway/telegram.rs` (empty-html guard)
  - `crates/goose/src/gateway/handler.rs` (smart_args_preview)
- Deploy target: `/home/fede/.local/bin/goose` (servicio systemd-user `lina-goosed.service`)
