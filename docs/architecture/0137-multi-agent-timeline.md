# ADR-0137: Timeline unificado multi-agente

**Fecha:** 2026-06-03
**Estado:** Propuesto
**Tags:** multi-agent, comunicación, contexto compartido

## Contexto

La comunicación entre LINA, Cline y Goose está fragmentada. Cada bot tiene sesiones aisladas por `chat_id` (privado vs grupo), sin un timeline cronológico compartido entre agentes.

### Problemas identificados

1. **Sesiones paralelas por chat_id**: Un mismo bot tiene distinto contexto si habla con Fede por privado vs si habla en el grupo "Comm, Lina and Goose"
2. **Sin cola de turnos**: Múltiples @mention concurrentes se procesan sin orden ni exclusión mutua
3. **Contexto aislado por agente**: LINA no sabe lo que Goose dijo 2 turnos atrás en el grupo, y viceversa
4. **Dependencia del grupo Telegram**: La comunicación entre bots depende del grupo y de Comm relay, cuando debería poder ser directa vía DB

## Decisión

Implementar 4 cambios en los gateways de LINA, Cline y Goose:

### 1. Sesión única por bot

**Archivo:** `bot.py` → `_session_id()`

En lugar de usar `chat_id` directamente, usar un `user_id` fijo basado en el `trusted_user` configurado. Los mensajes del grupo y del privado van a la misma sesión de goosed.

```python
def _session_id(self, chat_id: int) -> str:
    user_id = self._resolve_owner(chat_id)
    return f"multi-{user_id}"
```

### 2. Timeline unificado desde agent_messages

**Archivo:** `boot_hook.py` → `get_smart_context()`

Al iniciar cada turno:
- Leer últimos N mensajes de `agent_messages` de todos los agentes en los últimos M minutos
- Inyectarlos como contexto cronológico antes del mensaje actual

Formato inyectado:
```
[20:15] Fede → LINA: "chequeá el quiz"
[20:16] LINA → Cline: "revisá resultados"
[20:17] Cline → LINA: "todo ok ✅"
[20:18] Goose → LINA: "yo también lo vi"
```

### 3. Cola de un solo turno

**Archivo:** `bot.py`

Reemplazar `_busy[chat_id]` por `_busy[session_id]` para que mensajes del grupo y privado no puedan ejecutarse en paralelo contra la misma sesión. Si el bot está ocupado, encolar el mensaje para procesarlo secuencialmente.

### 4. Polling directo vía DB

Cada gateway debe hacer polling periódico de `agent_messages WHERE recipient = 'bot_name' AND sent_at > last_check` e inyectar esos mensajes como entrada normal al goosed correspondiente.

## Archivos a modificar

| Archivo | Cambio |
|---------|--------|
| `infrastructure/gateway/telegram/src/lina_gateway/bot.py` | `_session_id()`, `_busy` por sesión unificada, cola de turnos |
| `infrastructure/gateway/telegram/src/lina_gateway/boot_hook.py` | `get_smart_context()` inyectar timeline de `agent_messages` |
| `infrastructure/gateway/telegram/src/lina_gateway/config.py` | Parámetros: `AGENT_MESSAGES_LIMIT`, `AGENT_MESSAGES_WINDOW_MINUTES` |

## Consecuencias

**Positivas:**
- Contexto compartido entre todos los agentes en orden cronológico
- Un agente sabe lo que otro dijo sin necesidad de estar en el mismo chat
- Menos dependencia del grupo de Telegram
- Comunicación más rápida (DB local vs round-trip Telegram)

**Negativas:**
- Mayor uso de tokens de contexto (más historial inyectado)
- Complejidad adicional en los gateways
- Requiere actualizar los 3 gateways (LINA, Cline, Goose)

## Referencias

- `agent_messages` tabla en PostgreSQL (lina-db)
- `bot.py` → `_should_respond()` y `_session_id()`
- `boot_hook.py` → `get_smart_context()`
- `send_bot_lib.py` → sistema Comm relay
