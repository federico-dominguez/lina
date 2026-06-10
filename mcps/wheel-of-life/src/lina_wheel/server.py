"""LINA Wheel of Life MCP server — FastMCP with PostgreSQL store.

Tools:
    wheel_evaluate    — save a 1-10 score for one life area
    wheel_batch       — save multiple area scores at once
    wheel_current     — get the latest score for each area
    wheel_history     — evolution over N weeks
    wheel_areas       — list the 8 canonical areas
"""

from __future__ import annotations

import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from . import store

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "lina-wheel-of-life",
    description="Rueda de la Vida — acompañar a Fede en su equilibrio semanal",
)

# ── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
async def wheel_areas() -> list[str]:
    """Listar las 8 áreas de la Rueda de la Vida."""
    return store.AREAS


@mcp.tool()
async def wheel_evaluate(area: str, score: int, notes: str = "") -> dict:
    """Evaluar un área de la Rueda de la Vida con un score de 1 a 10.

    Args:
        area: Nombre del área (salud, trabajo, amor, amigos, finanzas,
              crecimiento, ocio, espiritualidad)
        score: Puntuación de 1 a 10 (1 = muy mal, 10 = excelente)
        notes: Notas opcionales sobre esta evaluación

    Returns:
        La evaluación guardada con id, area, score, notes, evaluated_at y week_start.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, store.save_evaluation, area, score, notes)


@mcp.tool()
async def wheel_batch(evaluations: list[dict]) -> dict:
    """Evaluar múltiples áreas a la vez.

    Args:
        evaluations: Lista de dicts con {area, score, notes?}.
                     Ej: [{"area": "salud", "score": 7}, {"area": "trabajo", "score": 8}]

    Returns:
        Dict con {saved: [...], errors: [...]}.
    """
    loop = asyncio.get_running_loop()
    saved = []
    errors = []
    for ev in evaluations:
        area = ev.get("area", "")
        score = ev.get("score", 0)
        notes = ev.get("notes", "")
        try:
            row = await loop.run_in_executor(None, store.save_evaluation, area, score, notes)
            saved.append(row)
        except (ValueError, Exception) as exc:
            errors.append({"area": area, "score": score, "error": str(exc)})
    return {"saved": saved, "errors": errors, "total": len(saved) + len(errors)}


@mcp.tool()
async def wheel_current() -> dict:
    """Obtener la Rueda de la Vida actual (último score por área).

    Returns:
        Dict con {areas: [{area, score, notes, evaluated_at, week_start}],
                  average, count, missing, summary}.
    """
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, store.get_current_wheel)
    scores = [r["score"] for r in rows]
    avg = round(sum(scores) / len(scores), 1) if scores else 0
    all_areas = {r["area"] for r in rows}
    missing = [a for a in store.AREAS if a not in all_areas]
    highest = max(rows, key=lambda r: r["score"])["area"] if rows else None
    lowest = min(rows, key=lambda r: r["score"])["area"] if rows else None
    return {
        "areas": rows,
        "average": avg,
        "count": len(rows),
        "missing": missing,
        "summary": {"highest": highest, "lowest": lowest},
    }


@mcp.tool()
async def wheel_history(weeks: int = 4) -> list[dict]:
    """Ver la evolución de la Rueda de la Vida en las últimas N semanas.

    Args:
        weeks: Número de semanas hacia atrás (default 4).

    Returns:
        Lista de semanas con {week_start, areas, average, count, missing, summary}.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, store.get_wheel_history, weeks)


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("LINA Wheel of Life MCP starting on port 8000")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
