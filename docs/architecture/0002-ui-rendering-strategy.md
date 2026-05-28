# 0002 — Estrategia de rendering UI para Lina

> Fecha: 2026‑05‑26
> Estado: propuesta
> Autor: análisis conjunto Fede + GitHub Copilot

---

## 1. Problema

Lina hoy se expone vía **Telegram Bot API** (gateway in‑process de Goose).
La experiencia de lectura se degrada cuando los mensajes incluyen:

- Tablas (alineación imposible en viewport variable).
- Bloques de pensamiento largos (se ven como pared de texto).
- Output de shell con multilínea + colores ANSI (se pierden).
- Comparaciones lado a lado (RAM antes/después, diffs de archivos).
- Stack traces, diffs unificados, JSON anidado.
- Múltiples tool calls encadenadas (no hay timeline visual claro).

Comparado con **Goose Desktop / Cline / GitHub Copilot Chat**, donde cada
mensaje es un componente React con sintáxis highlighting, diffs colapsables,
spinners en vivo, botones de acción, etc., la experiencia Telegram es
estructuralmente inferior — no por mala implementación, sino por límites
del medio.

Este documento:

1. Enumera **qué se puede** hacer dentro de Telegram (techo real).
2. Identifica **qué duele hoy** y cómo mejorarlo sin abandonar Telegram.
3. Evalúa **alternativas** (Mini App, app propia, web app, terminal nativo).
4. Da un **plan en fases** con esfuerzo estimado y criterio de decisión.

---

## 2. Inventario: qué soporta Telegram Bot API (techo absoluto)

### 2.1 Texto formateado (HTML / MarkdownV2)

Tags HTML soportados (los únicos — el resto rompe el mensaje):

| Tag | Uso | Limitación |
|---|---|---|
| `<b>` `<strong>` | Negrita | — |
| `<i>` `<em>` | Cursiva | — |
| `<u>` `<ins>` | Subrayado | — |
| `<s>` `<strike>` `<del>` | Tachado | — |
| `<tg-spoiler>` | Spoiler (oculto hasta tap) | útil para outputs largos |
| `<a href>` | Link | sólo `href` como atributo |
| `<code>` | Inline mono | sin atributos |
| `<pre>` | Bloque mono | sin sintáxis |
| `<pre><code class="language-X">` | Bloque mono con sintáxis | usa **libprisma** server‑side |
| `<blockquote>` | Cita | indentada, color tenue |
| `<blockquote expandable>` | Cita colapsable | tap para expandir, ideal para "pensamiento" |
| `<tg-emoji emoji-id>` | Emoji custom animado | requiere bot Premium / Fragment |
| `<tg-time unix format>` | Fecha/hora localizada | desde Bot API 9.5 (mar 2026) |

**Lo que NO existe**:

- Tablas (`<table>`), columnas, grids.
- Headings (`<h1>` … `<h6>`) — sólo `<b>` simula.
- Colores (ni text color ni background) fuera de los temas nativos.
- Imágenes inline en texto (sólo como media adjunta).
- HR / divisores reales.
- Listas con sangría real (`<ul>` / `<ol>`). Las simulamos con `•`/`1.`.
- Checkboxes inline (existen `Checklist` como tipo de mensaje aparte).

### 2.2 Sintáxis highlighting nativa

Telegram desde finales de 2024 hace **highlight server‑side** del contenido
dentro de `<pre><code class="language-X">…</code></pre>` usando
[libprisma](https://github.com/TelegramMessenger/libprisma#supported-languages).
Soporta ~280 lenguajes incluyendo `bash`, `python`, `rust`, `yaml`, `json`,
`diff`, `dockerfile`, `nginx`, `systemd`, `toml`, `sql`.

**Esto lo estamos sub‑usando**: pulldown‑cmark ya emite el lenguaje cuando
el markdown viene como ` ```rust `, pero no lo estamos pasando al `<pre>`.
Fix: incluir `class="language-{lang}"` en el `<code>` interno.

### 2.3 Affordances que no son texto

| Recurso | Cómo se usa | Beneficio |
|---|---|---|
| `sendPhoto` | Imagen JPG/PNG ≤ 10 MB | renderizar tabla/diff/diagrama como PNG |
| `sendDocument` | Archivo arbitrario ≤ 50 MB | mandar `output.txt`, `diff.patch`, log completo |
| `editMessageText` | Editar mensaje existente | streaming de "pensamiento", progreso de tool |
| `InlineKeyboardMarkup` | Botones bajo el mensaje | `[Re‑ejecutar] [Ver detalle] [Cancelar]` |
| `callback_query` | Click de botón → callback al bot | UI interactiva sin nuevo mensaje |
| `setMessageReaction` | Emoji reaction en mensaje | feedback liviano (✅/❌/🤔) |
| `sendChatAction` "typing" | Indicador "escribiendo…" | mientras se procesa |
| `sendChatAction` "upload_document" | "subiendo archivo" | mientras se genera PNG |
| `Web App` button | Abre un webview interno | UI completa HTML/CSS/JS dentro de Telegram |
| `sendChecklist` | Checklist nativo | TODO lists, multi‑step tasks |
| `sendPoll` (quiz) | Pregunta con opciones | confirmaciones tipo "¿correr esto? Sí/No" |

### 2.4 Límites duros

- **4096 caracteres** por mensaje (tras parsing de entities).
- **1024** para captions de media.
- **30 msg/seg** por bot, **1 msg/seg** por chat (más es flood‑wait).
- `editMessageText`: hasta **20/min** por mensaje, igual que envío.
- Tags HTML mal anidados → API rechaza el mensaje entero (400).
- Sin previews en vivo de URLs custom; sólo de URLs reales.

---

## 3. Cómo lo resuelven otros (referencia)

### 3.1 Goose Desktop / Cline / Copilot Chat (todos webviews React/Tauri/Electron)

- Cada **turno** es una tarjeta con header + cuerpo + acciones.
- **Pensamiento** = panel colapsable con animación de typing token a token.
- **Tool call** = tarjeta gris con icono (▶ shell, 📄 file, 🔍 search) +
  args en una línea + output debajo en un `<pre>` colapsable con scroll
  horizontal real.
- **Diff** = `react-diff-viewer` con + verde / − rojo y scroll por hunk.
- **Tablas** = `<table>` HTML real con borders y zebra stripes.
- **Streaming**: cada token llega por SSE y se appendea al DOM; lo que vemos
  en Copilot Chat es esencialmente `useEffect` + buffer + auto‑scroll.
- **Acciones**: botones inline (`Apply`, `Discard`, `Run`, `Open file`)
  que disparan handlers internos del IDE/desktop app.

### 3.2 Bots de chat tipo ChatGPT, Claude.ai, Perplexity

Web app pura. Tienen **el mismo problema que Lina tendría en Telegram** si
se intentara replicar al pie de la letra → por eso ninguno expone una
interfaz de "agente con tool calls" vía Telegram bot puro.

### 3.3 Bots que SÍ funcionan bien en Telegram

Patrones comunes:

- **Output corto y categórico** (recordatorios, conversiones, traducciones).
- **Resúmenes** con bullets y `<blockquote expandable>`.
- **Adjuntan PNGs / PDFs** cuando el contenido es visual (gráficos, mapas).
- **Inline keyboards** para drill‑down ("ver detalle" en vez de meter todo).
- **Mini App** para flujos complejos (Wallet, GameBots, formularios).

---

## 4. Diagnóstico del estado actual de Lina

| Área | Estado | Dolor |
|---|---|---|
| Bold/italic/code inline | ✅ ok | — |
| Code blocks | ⚠️ funcional pero **sin sintáxis** | Lina sale en blanco y negro |
| Tablas | ⚠️ recién migrado a bold+bullets | mejor que `<pre>`, pero "pierde grilla" |
| Pensamiento | ✅ `<blockquote expandable>` ya está | bien, pero NO se ve "stream" — aparece de golpe |
| Tool calls (shell/etc.) | ⚠️ `<blockquote expandable>` con args + output | sin separación visual entre comando y resultado largo |
| Output con ANSI | ❌ los códigos `\x1b[…m` llegan crudos o se strippean perdiendo color | siempre monocromo |
| Diffs (git) | ❌ son texto plano sin + / − coloreado | ilegibles si > 20 líneas |
| Múltiples mensajes en cascada | ⚠️ chunking básico | rompe contexto visual |
| Progreso en vivo | ❌ casi nulo (sólo `editMessageText` puntual) | usuario no ve "qué está haciendo" |
| Cancelar / re‑ejecutar | ⚠️ `/stop` por texto, sin botones | poco descubrible |
| Archivos/logs largos | ❌ se trunca a 4096 o se envía como múltiples chunks | imposible leer un stacktrace de 200 líneas |

---

## 5. Plan de mejora — 4 fases ascendentes

Las fases son **incrementales y desplegables independientemente**. Cada una
agrega valor sin requerir la siguiente.

---

### Fase 1 — "Apretar el limón" de Telegram nativo (effort: 1‑2 días)

Cero infra nueva. Sólo mejorar el renderer Rust en
`gateway/telegram_format.rs` y el envío en `gateway/telegram.rs`.

#### 1.1 Activar syntax highlighting nativo de Telegram
Pulldown‑cmark ya conoce el lenguaje. Solo hay que emitir:
```html
<pre><code class="language-bash">…</code></pre>
```
en vez del `<pre><code>` actual. **2 líneas de código**. Pinta bash, rust,
python, json, diff, yaml automáticamente con el theme nativo de Telegram.

#### 1.2 Renderizar diffs unificados como `language-diff`
Cuando un tool output empieza con `diff --git` o `--- a/` envolverlo en
`<pre><code class="language-diff">` para que Telegram coloree + / − rojo y
verde. Cero parseo manual.

#### 1.3 Strip de códigos ANSI antes de mostrar
Regex `\x1b\[[0-9;]*[mGKHF]` → quitar. (Hoy llegan crudos a veces.)

#### 1.4 Tool calls como tarjeta visual estructurada
En vez de un único blockquote, usar **dos blocks consecutivos**:

```html
<blockquote>⚙️ <b>shell</b>  <code>free -h</code></blockquote>
<blockquote expandable><pre><code class="language-bash">…output completo…</code></pre></blockquote>
```

El header (siempre visible) muestra herramienta + comando. El output va
abajo, colapsado, con sintáxis. Replica el patrón Cline/Copilot.

#### 1.5 Inline keyboards para acciones comunes
Después de cada respuesta agregar botones según contexto:

- Tras un shell: `[🔁 Re‑ejecutar] [📋 Copiar output] [📄 Como archivo]`
- Tras un diff: `[✅ Aplicar] [❌ Descartar] [👁 Ver completo]`
- Tras un error: `[🔍 Investigar] [🤖 Reintentar]`
- Siempre: `[⏹ Stop]` (reemplaza `/stop` invisible)

Implementación: `callback_query` handler en el gateway.

#### 1.6 Reactions automáticas como status liviano
- `setMessageReaction('🤔')` mientras está pensando.
- Reemplazar por `✅` al terminar exitoso, `❌` si error, `⏹` si cancelado.
- Más sutil que editar el mensaje cada vez.

#### 1.7 Auto‑promover a `sendDocument` cuando el output es enorme
Heurística: si tool output > 3500 chars o > 50 líneas → enviar como
`output.txt` adjunto con un mensaje cortito "Output guardado en archivo
(N líneas)". Patrón ya común en bots devops.

#### 1.8 `sendChatAction("typing")` continuo mientras está la LLM
Cada 4s mientras dura la respuesta. Hoy el usuario no sabe si está viva.

#### 1.9 `<tg-time>` para timestamps
Cualquier ISO/unix en respuestas → entity `<tg-time>` para localización
automática al timezone del usuario.

#### 1.10 Streaming en vivo del pensamiento y del output (`editMessageText`)

**Idea**: en vez de esperar a que termine de pensar y de ejecutar para mandar
un mensaje completo, Lina manda un mensaje *placeholder* al empezar y
lo va **editando** a medida que llegan tokens del LLM o líneas del shell.
Visualmente: el globito de pensamiento se ve "escribirse solo", igual que
Goose Desktop / Cline / Copilot Chat.

**¿Tiene sentido?** Sí — hoy es el dolor más grande: el usuario manda algo,
ve `⚙️ shell ...`, y queda 30–60s con cara de póquer sin saber si Lina
está viva, qué está razonando, ni cuánto falta. Streaming convierte la
espera en un proceso transparente.

**¿Es posible en Telegram?** Sí, con `editMessageText`. Es exactamente
para esto. Patrón probado por varios bots de mercado.

#### 1.10.1 Mecánica propuesta

```
Usuario: "qué está pesando en el SSD?"

t=0    Bot → sendMessage "💭 <i>Razonando…</i>"          (msg_id=42)
t=0.4  Bot → editMessageText(42, "💭 Razonando…\nEl usuario quiere…")
t=0.9  Bot → editMessageText(42, "💭 Razonando…\nEl usuario quiere… voy a empezar por du…")
t=1.4  Bot → editMessageText(42, "💭 Razonando…\n… elijo /home primero…")
t=1.8  Bot → editMessageText(42, "⚙️ <b>shell</b> <code>du -sh /home/*</code>\n<i>ejecutando…</i>")
t=2.1  Bot → editMessageText(42, "⚙️ shell du …\n```\n18G\tgoose\n9.1G\t.local\n… (streaming línea a línea)")
t=4.0  Bot → editMessageText(42, "✅ shell … (output completo colapsado)\n\nResumen final …")
```

El `msg_id=42` es **el mismo mensaje todo el tiempo**: el usuario ve un solo
globo creciendo, no una cascada de 8 mensajes.

**Estructura interna del buffer**:

- Un **`StreamingMessage`** por turno (struct nuevo en el gateway).
- Contiene: `message_id`, `thinking_buffer`, `tool_calls: Vec<ToolCallStream>`,
  `final_body_buffer`.
- Cada `ToolCallStream` tiene su propio `args`, `output_lines: VecDeque<String>`,
  `status: Pending|Running|Done|Failed`.
- Cada tick (cada ~1s o cada N tokens, lo que llegue primero) re‑renderiza
  todo el `StreamingMessage` a HTML completo y manda `editMessageText`.
- Si el HTML supera **3500 chars** → "sellar" el mensaje actual (cerrarlo
  en estado final) y abrir uno nuevo desde donde se cortó. Es la única
  forma de evitar el límite duro de 4096.

#### 1.10.2 Límites duros de Telegram que importan

| Límite | Valor | Mitigación |
|---|---|---|
| Texto por mensaje | 4096 chars | sellar + abrir nuevo a los ~3500 |
| Edits por chat | ~1/seg sostenido (más = `429 retry_after`) | throttle por chat con `tokio::time::interval(1s)` + coalescing |
| Edits idénticos | Telegram devuelve 400 "message is not modified" | comparar hash antes de mandar |
| HTML mal formado durante el stream (tag a medias) | rechazo 400 → pierde TODO el formato | siempre cerrar tags abiertos al renderizar; nunca emitir HTML parcial |
| Sin notificación en edits | ✅ *feature*, no problema | el usuario no recibe ping por cada token — ideal |
| Flicker en Android cuando edits son muy rápidos | observado a > 2 edits/seg | throttle a 1–1.5/seg + agrupar batches |
| `editMessageText` requiere texto **plano** (no media) | sólo aplica a `sendMessage`, no a `sendPhoto` | el stream siempre vive en un mensaje de texto; PNGs se mandan al final |

#### 1.10.3 Patrones de render mientras streamea

**Pensamiento puro** (tokens del LLM, sin tool call aún):
```html
<blockquote expandable>💭 <i>Razonando…</i>
{buffer acumulado de tokens}{cursor parpadeante "█"}</blockquote>
```
El cursor `█` (block char) da feel de "typing" sin tener que animar nada
— con que titile entre dos edits ya parece vivo.

**Tool call corriendo**:
```html
<blockquote>⚙️ <b>shell</b>  <code>du -sh /home/*</code>  ⏳</blockquote>
<blockquote expandable><pre><code class="language-bash">
{líneas de output acumuladas}{cursor}
</code></pre></blockquote>
```
El `⏳` se reemplaza por `✅` o `❌` cuando termina; el segundo blockquote
queda colapsado por defecto.

**Spillover por límite de 4096**: cuando el render alcanza ~3500 chars,
Lina manda un nuevo mensaje placeholder y desde ahí sigue editando ese.
El mensaje anterior queda "sellado" con el contenido final hasta ese punto.

#### 1.10.4 Implementación incremental

No hace falta refactor grande, se puede ir habilitando por capa:

1. **MVP**: streamear sólo el **pensamiento final** (entre tool calls).
   1 mensaje placeholder, edits cada 1s con el buffer del LLM, al cerrar
   se reemplaza por el render final. **~150 LOC en `gateway/`.**
2. **+ Tool output**: capturar stdout/stderr línea a línea del MCP
   `shell-policy` (ya emite por `stderr` los eventos), inyectar en el
   buffer del mensaje vivo.
3. **+ Spillover**: lógica de sellar/abrir cuando llega a 3500.
4. **+ Reacciones de status** (🤔 → ✅) sobre el mensaje del *usuario* (no
   sobre el del bot — que ya tiene su propio estado visible).

#### 1.10.5 ¿Quién más hace esto?

- **father-bot/chatgpt_telegram_bot** (gh, ~6k stars): patrón clásico
  de "placeholder + edit cada 1.2s". Es el bot de ChatGPT no oficial
  más usado en Telegram, exactamente este patrón.
- **Bothub / GPTunneL** (comerciales rusos): mismo patrón, agregan
  contador de tokens al final y botones de "regenerar".
- **Claude bots** no oficiales en TG (varios): idem.
- **Vercel AI SDK + Telegram bot examples**: documentan el patrón con
  `streamText` + throttle.
- **No conozco** ningún bot público que además streamee **output de
  shell en vivo en el mismo globo** — sería algo distintivo de Lina.

#### 1.10.6 Cuándo NO conviene streamear

- Respuestas que se sabe que serán cortas (< 200 chars) → 1 mensaje
  final, sin edits.
- Respuestas con tool calls que generan output gigante (> 10k líneas)
  → mostrar **resumen en vivo** (últimas 20 líneas + contador) y
  adjuntar el log completo como `sendDocument` al cerrar.
- Cuando el usuario ya envió otro mensaje → cortar el stream actual
  con el snapshot que tengamos y empezar el turno nuevo.

**Ganancia estimada (sumada a 1.1–1.9)**: ≅90% del dolor de "Lina parece
estar muerta mientras piensa". Es probablemente la mejora **más alta en
relación a esfuerzo** de toda Fase 1.

**Ganancia estimada**: ~70% del dolor actual. Sigue sin resolver tablas
complejas, diagramas, vistas lado‑a‑lado.

---

### Fase 2 — Rendering a imagen para casos "ricos" (effort: 3‑4 días)

Cuando el contenido es **inherentemente visual** (tablas grandes, gráficos,
árboles, diagramas, diffs largos, comparaciones lado a lado), generar un
PNG y mandarlo con `sendPhoto` o `sendDocument`.

#### Stack propuesto
- **MCP propio `lina-render`** (Python, en `mcps/render/`):
  - `render_markdown_to_png(markdown, theme='dark') → png_bytes`
  - `render_diff_to_png(diff, side_by_side=True) → png_bytes`
  - `render_table_to_png(rows, headers) → png_bytes`
  - `render_mermaid_to_png(diagram) → png_bytes`
- Engine: **Playwright headless Chromium** o **wkhtmltoimage**.
  Render a partir de templates HTML/CSS con un theme alineado a Goose
  Desktop (mismo tipo de letra, mismos colores `#1e1e1e` etc.).
- Cache LRU por hash del contenido para que re‑pedidas iguales sean
  instantáneas.

#### Heurística de uso (en `telegram_format.rs`)
- Tabla con > 4 columnas o > 6 filas → PNG.
- Diff con > 30 líneas → PNG.
- Mermaid / ASCII art → PNG.
- Resto sigue siendo texto.

#### Beneficio
Las comparaciones "antes/después" como la de RAM se ven **idénticas a
Goose Desktop**, con grilla real, colores, iconos. Pinch‑zoom funciona.
Se puede guardar la imagen en el rollo.

#### Costo
- Latencia: render PNG ~ 200‑500 ms.
- Memoria: headless Chromium pesa ~150 MB RSS — aceptable en host de
  desarrollo, atención en RPi.

**Ganancia estimada**: ~85% del dolor resuelto.

---

### Fase 3 — Telegram Mini App "Lina Studio" (effort: 1‑2 semanas)

Botón en el menú del bot que abre una **Web App de Telegram**: un webview
HTML/CSS/JS dentro del cliente Telegram, con la sesión activa del usuario
y `initData` firmado.

#### Capacidades que destraba
- React + Tailwind = paridad visual TOTAL con Goose Desktop.
- Streaming WebSocket → tokens token‑a‑token, igual que Copilot Chat.
- Diff viewer real (`react-diff-viewer`), JSON viewer (`react-json-view`),
  syntax highlighting (`shiki`).
- Drag & drop archivos, scroll virtual para outputs gigantes.
- Theme oscuro/claro coordinado con Telegram.
- Atajos de teclado, multi‑pane (chat + file explorer + terminal log).
- Acceso al portapapeles del dispositivo, geolocalización, biometría.

#### Arquitectura
```
Telegram client
   │ tap "📱 Abrir Lina Studio"
   ▼
Web App URL (HTTPS) — ej. https://lina.local/studio
   │ initData firmado (HMAC con bot token)
   ▼
Vite + React app  ──── WebSocket ────► gateway extendido en goosed
                                       (mismas sesiones que el chat)
```

El **chat de Telegram sigue funcionando** como hoy (modo "rápido"). El
botón Mini App es para "modo análisis profundo".

#### Costo
- Infra: dominio + cert SSL + reverse proxy (caddy/nginx). Existe ya por
  Goose Desktop si lo quisiéramos exponer.
- Código: ~1k‑2k LOC TypeScript inicial.
- Mantenimiento: dos frontends (chat plano y rich).

**Ganancia estimada**: ~95% paridad con Goose Desktop, sin instalar nada.

---

### Fase 4 — App propia (Android / PWA / Desktop) (effort: 3‑8 semanas)

Sólo si Fase 3 se siente limitada. Opciones:

| Opción | Effort | Pros | Contras |
|---|---|---|---|
| **PWA standalone** | 3 sem | reutiliza casi todo de Fase 3, "instalable" en Android/iOS/Desktop, push via VAPID | sin acceso a Termux/shell del device, notifs limitadas en iOS |
| **App Android nativa (Compose)** | 6 sem | acceso pleno al device, integración con Tasker/Termux, notifs ricas | sólo Android, mantenimiento separado |
| **Tauri / Electron desktop** | 4 sem | mismo bundle que Goose Desktop, hace falta forkearlo y rebranding | duplicación del trabajo de Goose Desktop upstream |
| **Forkear Goose Desktop** | 2 sem | ya existe, lo personalizamos | depende del cycle upstream |

Mi recomendación: **NO entrar a Fase 4 a menos que** Telegram + Mini App
muestren un techo real. La probabilidad es baja porque el Mini App ya
permite casi todo lo de una PWA, con login implícito.

---

## 6. Decisión recomendada

```
                    Esfuerzo   Ganancia   Recomendación
Fase 1 (tweaks)        S          70%      ✅ HACER YA
Fase 2 (render PNG)    M          15%      ✅ Hacer si Fase 1 no alcanza
Fase 3 (Mini App)      L          10%      ⏸ Evaluar tras Fase 2
Fase 4 (app propia)    XL          5%      ❌ Sólo si Mini App no alcanza
```

**Camino crítico**:

1. **Esta semana** → Fase 1 completa (es deuda técnica de baja fricción).
2. **Próximas 2 semanas** → Fase 2 si Lina empieza a generar más
   contenido visual (gráficos, diffs grandes, comparaciones).
3. **Mes 2** → Decidir Mini App según volumen de uso "profundo".
4. **App propia** → sólo si hay un caso de uso que ni Mini App resuelve
   (ej. integración con shell local del teléfono).

---

## 7. Backlog detallado (Fase 1 — implementable inmediato)

Cada item es una PR independiente.

- [ ] **F1‑a** `language-X` class en `<pre><code>` (passa el `lang` que ya
      conoce pulldown‑cmark al HTML).
- [ ] **F1‑b** Detectar `diff --git` / `--- a/` y envolver en
      `language-diff` aunque no venga marcado.
- [ ] **F1‑c** ANSI strip antes de inyectar tool output en el HTML.
- [ ] **F1‑d** Refactor `format_tool_status`: dos blockquotes (header
      visible + output colapsable con sintáxis).
- [ ] **F1‑e** `InlineKeyboardMarkup` con botones por tipo de respuesta
      (shell, diff, error, default). Handler `callback_query` en
      `gateway/handler.rs`.
- [ ] **F1‑f** Reactions automáticas (🤔 → ✅/❌/⏹).
- [ ] **F1‑g** Auto‑promote a `sendDocument` cuando output > N chars.
- [ ] **F1‑h** `sendChatAction("typing")` keep‑alive cada 4s mientras hay
      respuesta en curso.
- [ ] **F1‑i** Entity `<tg-time>` para timestamps detectados (ISO 8601 /
      "hace X min").
- [ ] **F1‑j** Strip de tablas markdown vacías o degeneradas (1 columna).
- [ ] **F1‑k** Test de smoke: enviar un mensaje con cada feature al chat
      de Fede y screenshotear (manual, una vez).
- [ ] **F1‑l** `StreamingMessage` struct + loop de `editMessageText` con
      throttle de 1s para streaming de pensamiento (MVP de 1.10.4 paso 1).
- [ ] **F1‑m** Inyectar stdout/stderr del MCP `shell-policy` línea a
      línea en el `StreamingMessage` activo (1.10.4 paso 2).
- [ ] **F1‑n** Spillover automático al llegar a ~3500 chars: sellar
      mensaje actual y abrir uno nuevo continuando el stream (1.10.4 paso 3).

---

## 8. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Telegram rechaza HTML mal anidado → caemos a plain text y se pierde TODO el formato | Tests de HTML válido + sanitizer pre‑envío + logs explícitos del `description` del error 400 |
| `editMessageText` rate‑limit en streaming | Throttle a 1 edit/seg como ya hace Cline; coalescing de tokens; comparar hash antes de mandar para evitar "message is not modified" |
| HTML parcial durante stream (tag abierto a medias) rompe el render | Renderizador siempre cierra tags abiertos al final de cada snapshot; nunca emitir HTML "a medio camino" |
| Límite 4096 chars excedido mid‑stream | Spillover: sellar mensaje actual a ~3500 y abrir uno nuevo |
| Mini App requiere HTTPS público | usar `caddy` con Let's Encrypt en `lina.<dominio>`, ya planeado para Goose Desktop remoto |
| PNG render bloquea event loop | MCP independiente, llamado async desde el gateway |
| Botones inline + callback_query → cambio de mental model en la LLM | Documentar en `.goosehints` que algunas acciones son botones, no chat |

---

## 9. Referencias

- Telegram Bot API — Formatting options: <https://core.telegram.org/bots/api#formatting-options>
- Telegram Bot API — MessageEntity: <https://core.telegram.org/bots/api#messageentity>
- libprisma (highlighter de Telegram): <https://github.com/TelegramMessenger/libprisma>
- Telegram Web Apps: <https://core.telegram.org/bots/webapps>
- Cline (rendering React patterns): <https://github.com/cline/cline>
- Goose Desktop (Tauri webview): <https://github.com/block/goose/tree/main/ui/desktop>

---

## 10. Próximo paso accionable

Empezar con **F1‑a + F1‑c + F1‑d** (sintáxis nativa, ANSI strip, refactor
tool status). Son las que más mueven la aguja por línea de código y se
pueden mergear esta misma tarde.
