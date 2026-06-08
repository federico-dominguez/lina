# Pipeline SSE — Detección de Finalización vía session_events

> **Estado:** ✅ Producción  
> **Última actualización:** 2026-06-08  
> **Issue:** #183  
> **Tags:** `pipeline` `sse` `finish` `multi-bot` `telegram`

## Descripción

Pipeline que envía mensajes a bots (LINA, Cline) vía Telegram y detecta cuándo terminan
de procesar mediante el evento `Finish` persistido en `session_events` (PostgreSQL).

A diferencia del sistema anterior (timeouts de 90s + polling), usa la señal nativa del
Observer del Gateway, eliminando falsos positivos y doble procesamiento.

## Arquitectura

```
send-bot.py → @s_bot msg → Grupo Telegram → Gateway → goosed
                                                    ↓
                                              Observer → session_events (DB)
                                                    ↓
                                              pipeline-runner.py poll → 🏁 Finish
                                                    ↓
                                              tg_notify_plain("✅ completo.")
```

## Archivos

| Archivo | Propósito |
|---------|-----------|
| `bin/pipeline-runner.py` | Pipeline configurable por pasos (YAML) |
| `bin/test-pipeline-sse.py` | Pipeline fijo de prueba |
| `comm/send-bot.py` | Envío con @mention al grupo |
| `comm/send_bot_lib.py` | Librería Telethon |

## Bugs corregidos

1. Mensajes de Comm redirigidos al grupo incorrecto (is_bot)
2. Floor control bloqueaba respuestas (deshabilitado)
3. URL de goosed en HTTP (cambiado a HTTPS)
4. Cline respondía a mensajes de LINA (filtro de mención)
5. Notificaciones con @mention causaban loop (texto plano)
