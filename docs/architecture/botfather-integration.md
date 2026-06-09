# 📋 Reporte: Mejora UX de Pipeline Manager en Telegram

## 1. Estado Actual

Actualmente los pipelines se gestionan desde Telegram vía el bot listener
(`@s_lina_bot`) con los siguientes comandos:

```
/pipeline list              → lista pipelines guardados
/pipeline run <nombre>      → ejecuta pipeline
/pipeline run <nombre> -p   → ejecuta en paralelo
/pipeline info <nombre>     → info detallada
/pipeline new <nombre> pasos → crear pipeline vía Telegram
```

### ✅ Lo que funciona
- Creación de pipelines desde Telegram
- Ejecución con detección de Finish via session_events DB
- Sesión Telethon independiente para el subproceso (comm_session vs comm_session_pipeline)
- Listener siempre activo (nunca se desconecta)
- Healthcheck automático cada hora + rutina matutina 9AM

### ❌ Problemas de UX actuales
- No hay menú de comandos (tabulación) — los comandos `/pipeline` no aparecen
  en el menú contextual de Telegram
- No hay botones interactivos (inline buttons) — todo es texto plano
- No hay feedback visual durante la ejecución del pipeline
- No se puede cancelar un pipeline en ejecución
- No hay confirmación antes de ejecutar (podría ejecutarse accidentalmente)
- Formato de `/pipeline new` es difícil de recordar

---

## 2. BotFather — Cómo configurar comandos

### ¿Qué es BotFather?
[@BotFather](https://t.me/BotFather) es el bot oficial de Telegram para crear y
gestionar bots. Permite definir la lista de comandos que aparecen en el menú
contextual (al escribir `/` en el chat).

### Cómo configurar comandos (paso a paso):

```
1. Abrir @BotFather en Telegram
2. Enviar /mybots
3. Seleccionar @s_lina_bot
4. Ir a "Edit Bot" → "Edit Commands"
5. Enviar la lista de comandos:

pipeline - Gestionar pipelines multi-bot
pipeline list - Listar pipelines guardados
pipeline run - Ejecutar un pipeline
pipeline new - Crear pipeline desde Telegram
pipeline info - Info detallada de un pipeline
pipeline cancel - Cancelar pipeline en ejecución
```

Pero Telegram solo permite comandons de UNA palabra (`/pipeline`) con una
descripción corta. No soporta subcomandos anidados como `/pipeline run`.
Los subcomandos se muestran como `/pipeline list`, `/pipeline run`, etc.
en el menú de autocompletado.

### Alternativa recomendada por Telegram:

En lugar de subcomandos, exponer cada acción como comando raíz:

```
/pipelines - Listar, crear y gestionar pipelines
/run - Ejecutar un pipeline guardado
/pipeline_new - Crear pipeline paso a paso (asistente)
/status - Ver estado de pipelines en ejecución
/cancel - Cancelar pipeline en ejecución
```

---

## 3. Mejoras propuestas

### 3.1 Menú de comandos en BotFather
Configurar los comandos para que aparezcan con tabulación:

| Comando | Descripción |
|---------|-------------|
| `/pipelines` | 📋 Listar y gestionar pipelines |
| `/run` | 🚀 Ejecutar pipeline (elige del menú) |
| `/new` | ✨ Crear pipeline (asistente paso a paso) |
| `/status` | 📊 Estado de pipelines en ejecución |
| `/cancel` | 🛑 Cancelar pipeline en ejecución |
| `/help` | ❓ Ayuda del sistema |

### 3.2 Botones inline (InlineKeyboardMarkup)

En vez de escribir `/pipeline run duo`, el usuario ve botones:

```
📋 Pipeline Manager

Seleccioná un pipeline:

[📂 duo]       [📂 healthcheck]
[📂 TestPipe]  [📂 rutina-matutina]

[➕ Crear nuevo]  [🔄 Refrescar]
```

Al tocar un pipeline → se abre menú de acciones:

```
🚀 Pipeline: duo (2 pasos)
@lina → hola
@cline → decime CPU y memoria

[▶️ Ejecutar]  [▶️ Paralelo]  [ℹ️ Info]  [🗑️ Eliminar]
```

### 3.3 Feedback visual durante ejecución

```
🚀 Pipeline 'duo' en ejecución...
┌─ Paso 1/2: @lina:h ola          ─┐
│  ⏳ Esperando respuesta...        │
└──────────────────────────────────┘
┌─ Paso 2/2: @cline: decime CPU   ─┐
│  ⏳ Esperando respuesta...        │
└──────────────────────────────────┘

[🛑 Cancelar]
```

Y cuando termina:

```
✅ Pipeline 'duo' completado (14.2s)
┌─ Paso 1/2: @lina → ✅ (5.1s, $0.002)   ─┐
└──────────────────────────────────────────┘
┌─ Paso 2/2: @cline → ✅ (9.1s, $0.003)  ─┐
└──────────────────────────────────────────┘

💰 Costo total: $0.005
[▶️ Re-ejecutar]  [📋 Nuevo pipeline]
```

### 3.4 Asistente interactivo para /new

En vez de escribir todo en una línea:

```
User: /new
Bot: 🧰 ¿Nombre del pipeline?
User: MoodleStatus
Bot: 🤖 ¿Primer bot? (lina/cline/gemma/goose)
User: lina
Bot: 📝 ¿Mensaje para lina?
User: Revisa los cuestionarios de Moodle
Bot: ✅ Paso 1 agregado. ¿Otro bot?
User: cline
Bot: 📝 ¿Mensaje para cline?
User: Verifica el reporte de lina
Bot: ✅ Pipeline 'MoodleStatus' creado (2 pasos)
     [▶️ Ejecutar]  [📋 Info]  [➕ Agregar paso]
```

### 3.5 Pipeline status tracking

```python
# El pipeline actualiza un registro en session_events con:
{
    "type": "pipeline_status",
    "pipeline_name": "duo",
    "step": 1,
    "total_steps": 2,
    "bot": "lina",
    "status": "running",  # running | done | failed
    "tokens": 26176,
    "cost": 0.002,
    "elapsed": 5.1
}
```

El listener lee estos eventos y actualiza el mensaje en el grupo
con el progreso en vivo. Similar a cómo GitHub Actions muestra
el progreso de workflows.

---

## 4. Implementación técnica

### Archivos a modificar/crear:

| Archivo | Cambio |
|---------|--------|
| `bot.py` (gateway) | Manejar inline keyboard callbacks |
| `bin/pipeline-listener.py` | Agregar soporte para botones inline + status tracking |
| `bin/pipeline-wizard.py` | **Nuevo** — asistente interactivo paso a paso |
| `bin/status.py` | **Nuevo** — muestra estado de pipelines en DB |
| `config/pipeline_status.py` | **Nuevo** — modelo de datos para status tracking |

### Dependencia:
```bash
pip install python-telegram-bot  # Para botones inline (si usamos python-telegram-bot)
# O usar InlineKeyboardMarkup desde Telethon directamente
```

Telethon ya soporta `InlineKeyboardMarkup` directamente:

```python
from telethon.tl.custom import Button

buttons = [
    [Button.inline("▶️ Ejecutar", data="run_duo")],
    [Button.inline("ℹ️ Info", data="info_duo"), Button.inline("🗑️ Borrar", data="delete_duo")],
]
await client.send_message(chat, "📋 Pipeline duo", buttons=buttons)
```

---

## 5. Roadmap

| Prioridad | Feature | Esfuerzo | Dependencias |
|-----------|---------|----------|--------------|
| 🔴 1 | BotFather: configurar comandos | 5 min | Acceso a @BotFather |
| 🔴 2 | Botones inline en listener | 2h | Telethon ya soportado |
| 🟡 3 | Feedback visual durante ejecución | 4h | #2 |
| 🟡 4 | Asistente interactivo /new | 3h | #2 |
| 🟢 5 | Pipeline status tracking | 4h | DB schema |
| 🟢 6 | Cancelar pipeline en ejecución | 2h | #5 |

---

## 6. Referencias

- [BotFather documentation](https://core.telegram.org/bots/features)
- [Inline keyboards (Telethon)](https://docs.telethon.dev/en/stable/modules/client.html#telethon.client.messages.MessageMethods.send_message)
- [Telegram Bot API: InlineKeyboardMarkup](https://core.telegram.org/bots/api#inlinekeyboardmarkup)
- [Telegram Menu Button](https://core.telegram.org/bots/api#menubuttoncommands)
- [Commands via BotFather](https://core.telegram.org/bots/features#commands)
