# 0004 — Optimización de visualización de pensamiento en Telegram

> **Estado:** Propuesta
> **Fecha:** 2026-05-27
> **Autor:** LINA (revisado por federico-dominguez)
> **Issue:** #3
> **Repo afectado:** `~/src/goose/` (fork patched) + `~/lina/bin/goosed` (binario resultante)
> **Problema base:** Fallo F-6 — 268 burbujas de razonamiento vs ~173 respuestas finales (60% monólogo interno)

---

## Tabla de contenidos

1. [Contexto](#1-contexto)
2. [Restricciones de la Bot API de Telegram](#2-restricciones-de-la-bot-api-de-telegram)
3. [Opción A: sendChatAction (typing indicator)](#3-opción-a-sendchataction-typing-indicator)
4. [Opción B: editMessageText (burbuja única editable)](#4-opción-b-editmessagetext-burbuja-única-editable)
5. [Opción C: tg-spoiler en respuesta final](#5-opción-c-tg-spoiler-en-respuesta-final)
6. [Opción D: Híbrido (recomendado)](#6-opción-d-híbrido-recomendado)
7. [Impacto esperado](#7-impacto-esperado)
8. [Riesgos y limitaciones](#8-riesgos-y-limitaciones)
9. [Posible implementación en gateway](#9-posible-implementación-en-gateway)

---

## 1. Contexto

Actualmente, cuando LINA procesa un mensaje, el gateway de Telegram envía **cada chunk de razonamiento** como un `sendMessage` individual. Esto produce:

- ~268 burbujas de `💭 Razonando...` por sesión (dato real del análisis del 2026-05-27)
- Saturacion visual del chat
- Dificultad para encontrar la respuesta final entre tanto pensamiento
- Desperdicio de tokens (cada burbuja tiene overhead de API)

### Objetivos

- Reducir la polución visual del chat durante el razonamiento
- Mantener feedback al usuario de que LINA está procesando
- No perder la capacidad de mostrar el razonamiento cuando sea útil
- Respetar rate limits de Telegram (30 msg/segundo en grupos, menos en floods)

---

## 2. Restricciones de la Bot API de Telegram

| Límite | Valor | Relevancia |
|---|---|---|
| `sendMessage` length | 4096 chars | Si se pasa, error 400 |
| `editMessageText` length | 4096 chars | Ídem |
| `editMessageText` rate limit | ~1-2 edits/segundo tolerado | Más de eso → 429 Too Many Requests |
| `sendChatAction` rate limit | ~5 segundos entre actions | Se puede llamar seguido, no hay límite duro documentado |
| Mensajes simultáneos | No hay "edición en vivo" real | Telegram no soporta streaming como SSE/WebSocket |

La API de Telegram es **request-response puro**. No hay conexión persistente. Cada `editMessageText` es un HTTP request independiente.

---

## 3. Opción A: `sendChatAction('typing')`

### Cómo funciona

El bot envía `sendChatAction` con el tipo `typing`. Telegram muestra `LINA está escribiendo...` en el header del chat. No se crea ningún mensaje.

### Flujo

```
T+0s   → sendChatAction(typing) ON
T+5s   → sendChatAction(typing)   (se reenvía porque expira a los ~5s)
T+10s  → sendChatAction(typing)
...
T+N s  → sendMessage(respuesta final)
```

### Pros
- **Cero burbujas** de pensamiento
- Se ve exactamente como cuando una persona escribe
- Sin consumo de mensajes en el chat

### Contras
- El usuario no sabe **qué** está procesando LINA
- Si tarda >15s sin respuesta, parece que colgó
- No hay forma de saber si está leyendo el calendario, ejecutando un comando, o pensando

### Veredicto
✅ Ideal combinado con otra opción. Solo no alcanza.

---

## 4. Opción B: `editMessageText` (burbuja única editable)

### Cómo funciona

LINA envía **un solo mensaje** de pensamiento y lo va **editando** en el lugar a medida que avanza el razonamiento. Nunca hay más de una burbuja de pensamiento visible.

### Flujo

```
T+0s   → sendMessage("💭 Pensando...")
T+3s   → editMessageText("💭 Leyendo tu calendario...")
T+6s   → editMessageText("💭 Encontré eventos...")
T+9s   → editMessageText("✅ Acá va tu respuesta final")
```

### Timeline visual en el chat

```
[22:00] Usuario: Qué tengo esta semana?
[22:00] LINA: 💭 Pensando...
[22:00] LINA: 💭 Leyendo tu calendario...    ← misma burbuja, editada
[22:01] LINA: 💭 Encontré eventos...          ← misma burbuja, editada
[22:01] LINA: ✅ Tenés psicólogo jueves 18hs  ← misma burbuja, editada
```

El usuario ve **siempre una sola burbuja** que cambia de texto.

### Pros
- **Una burbuja máxima** durante el pensamiento
- Muestra progreso: el usuario ve qué está haciendo LINA
- La respuesta final puede reemplazar la burbuja de pensamiento completamente

### Contras
- `editMessageText` tiene rate limit (~1-2 edits/segundo). Si el razonamiento genera chunks muy rápido, hay que throttlearlos.
- Si LINA ejecuta **tool calls** (shell, leer archivos, etc.), el gateway muestra burbujas `⚙️ → ✅/❌` separadas que no se pueden editar (son tool call results, no texto).
- Si la sesión se cae en medio de un edit, el mensaje queda en estado intermedio ("💭 Analizando...").

### Rate limit handling

Para evitar `429 Too Many Requests`:

```
if tiempo_desde_ultimo_edit >= 3s:
    editMessageText(nuevo_texto)
else:
    descartar_update (se acumula)
```

---

## 5. Opción C: `<tg-spoiler>` en respuesta final

### Cómo funciona

La respuesta final incluye el razonamiento **oculto bajo spoiler** de Telegram (`<tg-spoiler>...</tg-spoiler>`). El usuario ve la respuesta limpia, y si quiere ver el razonamiento, toca el spoiler para revelarlo.

### Ejemplo

```html
<b>✅ Tu semana:</b>

• <b>Miércoles 27</b> — Oficina TCS 9-18
• <b>Jueves 28</b> — Psicólogo 18hs
• <b>Viernes 29</b> — Oculista 9:30, Gimnasio 18hs

<tg-spoiler><b>💭 Razonamiento:</b>
Analicé tu calendario entre el 25/05 y 31/05.
Encontré 7 eventos. Filtré los que ya pasaron
(lunes oculista y gimnasio). Mostré los
próximos agrupados por día.</tg-spoiler>
```

Visualmente el usuario ve:

```
✅ Tu semana:

• Miércoles 27 — Oficina TCS 9-18
• Jueves 28 — Psicólogo 18hs
• Viernes 29 — Oculista 9:30, Gimnasio 18hs

💭 Razonamiento oculto [tocar para revelar]
```

### Pros
- **Un solo mensaje**, limpio
- El razonamiento está disponible si el usuario lo quiere ver
- Sin burbujas extra durante el procesamiento

### Contras
- **No muestra progreso** mientras LINA piensa (el usuario no sabe si está procesando o colgó)
- El spoiler se ve distinto en cada cliente (Telegram Desktop, Android, iOS)

### Veredicto
✅ Útil como **toque final** combinado con Option A (typing indicator). No sirve sola porque falta feedback de progreso.

---

## 6. Opción D: Híbrido (recomendado) ⭐

Combina lo mejor de las tres opciones anteriores. Es la solución propuesta para implementar.

### Fase 1 — Mientras piensa (0 a ~4s)

```
sendChatAction(typing) ON
```

El chat muestra "LINA está escribiendo..." en el header. Cero burbujas.

`sendChatAction` expira en ~5 segundos del lado de Telegram. Para mantener el indicador activo, reenviar cada 4s.

Si el razonamiento se resuelve en <4 segundos, el usuario solo ve el typing indicator y después la respuesta. Perfecto.

### Fase 2 — Pensamiento prolongado (>4s)

```
sendMessage("💭 <estado>")
```

Se envía **un solo mensaje** con el estado actual del procesamiento. A partir de acá:

```
cada ~5s:
    editMessageText("💭 <nuevo estado>")
```

Posibles estados:
- `💭 Analizando tu pregunta...`
- `💭 Buscando en tu calendario...`
- `💭 Ejecutando comando...`
- `💭 Consultando Moodle...`
- `💭 Revisando archivos...`
- `💭 Casi listo...`

### Fase 3 — Tool calls

Las burbujas `⚙️ → ✅/❌` se mantienen (son generadas por el gateway, no por LINA). Pero opcionalmente se pueden **ocultar** cambiando una config del gateway (si es que existe).

### Fase 4 — Respuesta final

```
deleteMessage(thinking_message_id)  // borrar burbuja de pensamiento
sendMessage("✅ <respuesta formateada>")  // enviar respuesta como mensaje nuevo
```

**No** editar la burbuja de pensamiento con la respuesta final. Si el usuario borró el mensaje de thinking mientras LINA procesaba, `editMessageText` falla con `400 Bad Request: message to edit not found`. Enviar la respuesta siempre como mensaje nuevo y borrar la burbuja de thinking (si existe) por separado. `deleteMessage` falla silenciosamente si el mensaje ya no existe — sin efecto secundario.

Si se quiere incluir razonamiento en la respuesta final:

```html
✅ <respuesta>

<tg-spoiler><b>💭 Razonamiento:</b>
<razonamiento></tg-spoiler>
```

### Fase 5 — Limpieza

```
sendChatAction(typing) OFF (cancelar el loop)
thinking_state.message_id = None
```

### Diagrama de flujo completo

```
¿Razonamiento necesario?
├── No → sendMessage(respuesta) directo. Fin.
└── Sí →
    ├── sendChatAction(typing) ON (loop cada 4s)
    ├── ¿Pasa >4s?
    │   ├── No → cancelar loop typing
    │   │        sendMessage(respuesta)
    │   │        Fin.
    │   └── Sí →
    │       ├── sendMessage("💭 <estado inicial>") → guardar thinking_msg_id
    │       ├── loop cada ~5s:
    │       │   ├── ¿Nuevo estado relevante?
    │       │   │   ├── Sí → editMessageText(thinking_msg_id, "💭 <nuevo estado>")
    │       │   │   │        (ignorar error 400 si fue borrado)
    │       │   │   └── No → skip
    │       │   └── ¿Respuesta lista?
    │       │       ├── No → continuar loop
    │       │       └── Sí → salir del loop
    │       ├── deleteMessage(thinking_msg_id)  // ignorar error si ya no existe
    │       ├── sendMessage("<respuesta> [spoiler razonamiento?]")
    │       ├── cancelar loop typing
    │       └── Fin.
```

---

## 7. Impacto esperado

### Antes (actual)

```
Por sesión:
- 268 burbujas 💭 (razonamiento)
- 173 burbujas de respuesta
- 441 burbujas totales
- 60% del contenido es monólogo interno
```

### Después (Opción D)

```
Por sesión:
- 0-1 burbujas 💭 editables (máximo)
- 173 burbujas de respuesta
- 173-174 burbujas totales
- 0% de monólogo interno visible (o spoiler opcional)
```

### Reducción: ~60% menos burbujas en el chat

Además:
- **Tokens ahorrados**: cada `sendMessage` de pensamiento tiene overhead HTTP + formato. Se elimina ese overhead.
- **Velocidad percibida**: el typing indicator da sensación de respuesta inmediata.
- **Claridad**: el chat se vuelve legible, las respuestas son fáciles de encontrar.

---

## 8. Riesgos y limitaciones

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| Rate limit de `editMessageText` | Media | Throttle a 1 edit cada 3-5s |
| Mensaje queda en estado intermedio si se cae la sesión | Baja | En el próximo mensaje, LINA puede ignorar el mensaje huérfano |
| Cliente no soporta `tg-spoiler` | Baja (solo clientes muy viejos) | El texto se muestra sin formato, no se pierde |
| typing indicator no llega si hay alta latencia | Media | No crítico, solo es feedback visual |
| El usuario no ve progreso detallado si solo hay typing | Media (subjetivo) | Solucionado con el mensaje editable que aparece a los 8s |

---

## 9. Posible implementación en gateway

El cambio necesario está en el handler de Telegram del gateway de Goose:

**Repositorio:** `~/src/goose/` (fork patched de block/goose)
**Archivo:** `crates/goose/src/gateway/telegram.rs` (línea ~170)

> ⚠️ **Nota de scope**: Este cambio requiere modificar el fork de Goose y recompilar el binario `goosed`. Después de compilar, copiar el binario a `~/lina/bin/goosed` (o actualizar el symlink en `~/goosed_patched`). No es un cambio al repo `lina/` de Python/MCPs.
>
> Comando de compilación: `cargo build --release --bin goosed --no-default-features --features portable-default,system-keyring,code-mode`

### Cambios requeridos

1. **Reemplazar `sendMessage` por `editMessageText`** en el streaming de pensamiento.
   - Guardar `message_id` del primer mensaje de pensamiento.
   - En vez de `sendMessage` para cada chunk, usar `editMessageText` con ese `message_id`.

2. **Agregar throttle timer** para edits:
   ```rust
   last_edit_time: Instant;
   const MIN_EDIT_INTERVAL: Duration = Duration::from_secs(3);
   ```

3. **Agregar `sendChatAction` loop**:
   ```rust
   // Enviar typing cada ~4s mientras se procesa
   spawn(async {
       loop {
           bot.send_chat_action(chat_id, "typing").await?;
           sleep(Duration::from_secs(4)).await;
       }
   });
   ```

4. **Manejar transición pensamiento → respuesta**:
   - Si hay un `message_id` de pensamiento activo → `editMessageText` con la respuesta final.
   - Si no hay (respuesta rápida <8s) → `sendMessage` directo.

### Pseudocódigo conceptual

```rust
struct ThinkingState {
    message_id: Option<i32>,     // ID del mensaje de pensamiento (si se envió)
    last_edit: Instant,          // Último edit para rate limiting
}

impl TelegramHandler {
    async fn send_thinking(&mut self, chat_id: i64, text: &str) {
        if let Some(msg_id) = self.thinking.message_id {
            // Editar mensaje existente (respetando rate limit)
            if self.thinking.last_edit.elapsed() >= MIN_EDIT_INTERVAL {
                bot.edit_message_text(chat_id, msg_id, text).await?;
                self.thinking.last_edit = Instant::now();
            }
        } else {
            // Primer mensaje de pensamiento
            let msg = bot.send_message(chat_id, text).await?;
            self.thinking.message_id = Some(msg.id);
            self.thinking.last_edit = Instant::now();
        }
    }

    async fn send_final_response(&mut self, chat_id: i64, text: &str) {
        // Borrar la burbuja de pensamiento (si existe)
        // deleteMessage falla silenciosamente si ya fue borrado por el usuario
        if let Some(msg_id) = self.thinking.message_id.take() {
            let _ = bot.delete_message(chat_id, msg_id).await;
        }
        // Siempre enviar la respuesta final como mensaje nuevo
        // (nunca editar la burbuja de pensamiento — puede haber sido borrada)
        bot.send_message(chat_id, text).await?;
    }
}
```

---

## Apéndice A: Alternativa no considerada (chatgpt-style streaming)

No es posible en Telegram.

Telegram no soporta streaming de caracteres como ChatGPT. No hay API de WebSocket ni Server-Sent Events para bots. La única forma de simular streaming es:

- Mandar mensajes muy cortos y editarlos rápidamente → rate limit violado.
- Mandar muchos mensajes cortos secuenciales → satura el chat.

Por eso la recomendación es **no intentar replicar el efecto typewriter de ChatGPT** en Telegram. No es el medio adecuado.

---

## Apéndice B: Métricas para evaluar después de implementar

- [ ] Cantidad de burbujas de pensamiento por sesión (< 5 es éxito)
- [ ] Tasa de error 429 (Too Many Requests) — debe ser 0%
- [ ] Tiempo promedio hasta primera respuesta perceptible (typing indicator cuenta como respuesta)
- [ ] Feedback del usuario (Federico) sobre claridad del chat

---

## 10. Implementación real — post análisis de código

> Nota agregada el 2026-05-28 tras leer el código real de `handler.rs`.

El diagnóstico del ADR (Sección 9) era correcto en dirección pero incompleto en mecanismo.

### Causa raíz confirmada

La variable `streaming: Option<StreamingBubble>` en el loop del handler se pone en `None` cada vez que llega un `ToolRequest` (el bubble es sellado con `.seal().await`). Cuando el siguiente chunk de `MessageContent::Thinking` llega, `streaming.is_none()` es `true` → se abre un **nuevo** bubble con `sendMessage`. En una sesión con 15+ tool calls, esto genera 15+ bubbles de razonamiento.

### Fix implementado

Variable `thinking_shown: bool` (inicializada en `false` por mensaje entrante):

```rust
let mut thinking_shown = false;
```

En el branch `MessageContent::Thinking`:
```rust
if streaming.is_none() {
    if !thinking_shown {
        // Abrir bubble normalmente
        // ...
        thinking_shown = true;
    } else {
        // Después del primer tool call: solo typing indicator
        let _ = self.gateway.send_message(&message.user, OutgoingMessage::Typing).await;
    }
}
```

**Resultado:** máximo 1 bubble de thinking por mensaje del usuario, independientemente de cuántos tool calls ocurran en el turno.

### Alcance real vs ADR

El ADR describía cambios en `telegram.rs` (gateway layer). El fix real es en `handler.rs` (orquestación del stream). El gateway no necesitó modificaciones — el `OutgoingMessage::Typing` ya existía y `send_chat_action("typing")` ya estaba implementado.
