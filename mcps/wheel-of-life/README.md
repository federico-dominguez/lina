# 🎡 LINA Wheel of Life MCP

Rueda de la Vida — acompañar a Fede en su equilibrio semanal.

## Áreas (1-10)

1. 💪 **salud** — Energía, ejercicio, sueño, check-ups
2. 💼 **trabajo** — Propósito, desafío, crecimiento, reconocimiento
3. ❤️ **amor** — Conexión, comunicación, intimidad
4. 👥 **amigos** — Calidad de vínculos, tiempo compartido
5. 💰 **finanzas** — Ingresos, ahorro, tranquilidad económica
6. 🌱 **crecimiento** — Aprendizaje, desarrollo, lectura
7. 🎨 **ocio** — Tiempo libre, hobbies, diversión
8. 🧘 **espiritualidad** — Propósito, valores, conexión interior

## Tools

| Tool | Descripción |
|---|---|
| `wheel_areas` | Listar las 8 áreas |
| `wheel_evaluate(area, score, notes?)` | Evaluar un área |
| `wheel_batch(evaluations)` | Evaluar múltiples áreas |
| `wheel_current()` | Rueda actual (último score por área) |
| `wheel_history(weeks?)` | Evolución en N semanas |

## Ejemplo

```json
// POST /mcp → wheel_evaluate(area="salud", score=7, notes="Dormí bien")
{
  "id": 1,
  "area": "salud",
  "score": 7,
  "notes": "Dormí bien",
  "evaluated_at": "2026-06-09T21:00:00-03:00",
  "week_start": "2026-06-09"
}

// wheel_current()
{
  "areas": [
    {"area": "salud", "score": 7, ...},
    {"area": "trabajo", "score": 8, ...}
  ],
  "average": 7.5,
  "count": 2,
  "missing": ["amor", "amigos", "finanzas", "crecimiento", "ocio", "espiritualidad"],
  "summary": {"highest": "trabajo", "lowest": "salud"}
}
```
