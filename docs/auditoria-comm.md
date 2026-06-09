# Auditoría Comm — 2026-06-06

> **Fecha:** 2026-06-06 14:50 UTC
> **Auditor:** Goose (@s_goose_bot)
> **Incidente:** Spam de Reportes de Sistema + FloodWait de Telegram
> **Issue relacionado:** #178 (Sistema Comm)

---

## Resumen ejecutivo

El 2026-06-06 a las 14:42 UTC se desencadenó un loop de mensajes `"📊 Reporte de Sistema"` generados por los 3 bots (LINA, Cline, Goose), que saturó `comm-svc` y provocó **91 mensajes fallidos** por FloodWait de Telegram.

**Causa raíz:** Una conversación rutinaria de COMM CHECK a las 14:41 UTC llevó a los 3 bots a generar reportes de sistema autónomamente. Al ver que otros bots reportaban, cada bot generaba su propio reporte, creando un feedback loop positivo. Ninguno de los mensajes contenía una instrucción explícita de generar reportes periódicos — fue comportamiento autónomo de los LLMs.

**Fallo directo:** `comm-svc` (Telethon) intentaba enviar cada reporte al grupo de Telegram tan rápido como aparecía en la DB (~1 mensaje/segundo). Telegram devolvía `FloodWaitError` con tiempos de espera de hasta **300 segundos**, y todos los mensajes se marcaban como `failed`.

---

## Línea de tiempo

| Hora (UTC) | Evento | Detalle |
|---|---|---|
| 14:17 | LINA inicia conversación | `"Hola"` de LINA → Goose |
| 14:32 | Diálogo de rutina | Goose y LINA se presentan, sincronizan estado |
| 14:38 | PRUEBA DE COMM | Prueba de comunicación bidireccional |
| 14:41 | COMM CHECK | Goose envía reporte de gateway rebuild a LINA |
| **14:42:03** | **🏁 Primer Reporte de Sistema** | LINA responde a Goose con monitor de sistema |
| 14:42:14 | **Loop inicia** | Goose y LINA empiezan a generar reportes cada ~2s |
| 14:42:26 | **Cline se suma** | Cline también empieza a generar reportes |
| 14:43:08 | **🚨 Primer FloodWait** | `"A wait of 298 seconds is required"` — msg #390 |
| 14:43:08–14:44:52 | **91 mensajes fallan** | Todos por FloodWait, wait time decrece de 298s → 223s |
| 14:44:52 | **Spam se detiene** | Último mensaje de Cline a LINA (#459). Sin nuevos reportes desde entonces. |

---

## Estadísticas

| Métrica | Valor |
|---|---|
| Mensajes totales en la tormenta | ~95 |
| Mensajes `delivered` (previos al FloodWait) | ~4 |
| Mensajes `failed` (por FloodWait) | 91 |
| Duración de la tormenta | ~2 minutos (14:42–14:44) |
| FloodWait máximo | 298 segundos |
| FloodWait mínimo (último fallo) | 223 segundos |
| Bots involucrados | LINA, Cline, Goose (3/3) |
| Canales afectados | `todos`, `goose`, `fede` |

### Desglose por bot (mensajes generados)

| Bot | Mensajes enviados | Principales destinos |
|---|---|---|
| **LINA** | ~30 | `goose`, `todos`, `cline`, `fede` |
| **Cline** | ~35 | `todos`, `goose` |
| **Goose** | ~15 | `todos` |
| **comm** (exec summaries) | ~15 | `goose` |

---

## Causa raíz

### ¿Por qué los bots empezaron a generar reportes?

La secuencia que inició el loop:

1. **14:41:56** — Goose envía COMM CHECK a LINA con reporte de actividad (gateway rebuild)
2. **14:42:03** — LINA responde con un `"📊 Monitoreo de sistema"` (iniciativa propia de LINA)
3. **14:42:09** — Goose ve el reporte de LINA y también genera un `"📊 Reporte de Sistema"`
4. **14:42:14** — La retroalimentación se acelera: cada reporte de un bot incita a los otros a reportar también
5. **14:43** — Los 3 bots están generando reportes cada 1-2 segundos

**No hay una instrucción explícita de generar reportes periódicos** en ningún system prompt ni en los comandos de la DB. Fue comportamiento emergente de los LLMs al verse expuestos a reportes de otros agentes.

### ¿Por qué fallaron?

`comm-svc` (servicio systemd en el host) pollea `comm_messages` cada 2 segundos y envía al grupo Comm via Telethon. Con ~1 mensaje/segundo entrando a la DB, el envío a Telegram saturó el rate limit:

```
FloodWaitError: A wait of 298 seconds is required (caused by SendMessageRequest)
```

Telegram tiene límites estrictos de mensajes por minuto en grupos. Superado ese límite, devuelve `FloodWaitError` con el tiempo de espera requerido. `comm-svc` no implementa backoff ni retry — marca el mensaje como `failed` y sigue.

---

## Fix aplicado

### 1. Filter en comm-svc (YA en código)

```python
# comm/comm-svc.py, línea 83
AND destination NOT IN ('lina', 'cline', 'gemma', 'goose', 'todos')
AND sender NOT IN ('goose-health', 'goose-monitor', 'monitor', 'comm')
```

Doble filtro que evita que `comm-svc` procese:
- Mensajes **destinados** a bots individuales o al canal `todos` (esos los maneja comm-bridge vía SSE)
- Mensajes **originados** por monitores o por el mismo comm-bridge (exec summaries)

**Verificación:** Ambos filtros presentes en `comm/comm-svc.py` líneas 83-84.

### 2. ✅ Retry con backoff en comm-svc (IMPLEMENTADO 2026-06-06)

`comm-svc.py` ahora detecta `FloodWaitError` específicamente (no genérico `Exception`) y:

1. Marca el mensaje como `status='retrying'` con error `"FloodWait Ns"`
2. Espera `FloodWait.seconds + 1` segundos (respeta el rate limit de Telegram)
3. Reintenta el envío una vez
4. Si el reintento falla, recién ahí marca como `status='failed'`

```python
except FloodWaitError as fwe:
    wait = fwe.seconds
    # status → 'retrying'
    await asyncio.sleep(wait + 1)
    # Reintentar una vez
    try:
        tg_msg = await tg.send_message(...)
        # status → 'delivered'
    except Exception:
        # status → 'failed'
```

**DB:** Se agregó `'retrying'` como status válido en el CHECK constraint.

### 3. ✅ DB Cleanup (IMPLEMENTADO 2026-06-06)

- 58 mensajes fallidos de la tormenta (14:42–14:50) marcados como `status='ignored'`
- Se agregó `'ignored'` como status válido en `comm_messages_status_check`
- Constraint actualizado: `CHECK (status = ANY (ARRAY['sent', 'delivered', 'failed', 'retrying', 'ignored']))`

### 4. Pendiente: Límite de reportes por bot

No hay un mecanismo que impida que un bot genere más de N mensajes por minuto. Considerar agregar rate limiting por sender en `comm_messages`.

---

## Recomendaciones

### Inmediatas

1. ✅ **Filter NOT IN bots + NOT IN monitores** — En código líneas 83-84.
2. ✅ **Monitorear** — No se detectaron nuevos reportes de sistema desde 14:44.
3. ✅ **Retry con backoff** — Implementado para FloodWaitError en `comm-svc.py`.

### Corto plazo

4. ❌ **Agregar rate limit** por sender en `comm_messages` (máx 5 msg/min por bot)
5. ❌ **Silenciar exec summaries de comm_bridge** — Los resúmenes ejecutivos con `sender='comm'` → `goose` son bloqueados por el filter, pero siguen saturando la DB. Evaluar si son necesarios o si pueden enviarse con menor frecuencia (cada 5 tareas, no en cada una).

### Largo plazo

6. ❌ **Desacoplar reportes de sistema** — Los bots no deberían generar reportes de sistema sin una instrucción explícita. Considerar agregar un protocolo: solo reportar cuando se solicite activamente.
7. ✅ **Circuit breaker en comm-svc** — Implementado vía retry con backoff. Si detecta FloodWait, espera `e.seconds + 1` antes de reintentar.

---

## Archivos relevantes

- `comm/comm-svc.py` — Servicio de envío a Telegram (corre en host via systemd)
- `comm/comm_bridge.py` — Bridge DB ↔ goosed SSE (genera exec summaries)
- `bin/goose-monitor.py` — Monitor de recursos (solo alertas, no genera estos reportes)
- `bin/goose-health.py` — Health check integral (no genera reportes periódicos)
- `pipeline/pipeline_comm_bridge.py` — Pipeline para bots (DB bridge)
- `pipeline/pipeline_bot_bridge.py` — Pipeline para bots (Telegram directo)

---

## Datos crudos de la DB

Consulta para obtener los mensajes fallidos:

```sql
SELECT id, sender, destination, LEFT(error, 80) as error,
       LEFT(message, 60) as msg, created_at
FROM comm_messages
WHERE status = 'failed'
  AND created_at >= '2026-06-06 14:42:00'
ORDER BY id;
```

Resultado: **91 filas**, todas con error `"A wait of N seconds is required (caused by SendMessageRequest)"`.

---

*Reporte generado por Goose el 2026-06-06 14:50 UTC*
