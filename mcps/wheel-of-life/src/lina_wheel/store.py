"""PostgreSQL store for Wheel of Life evaluations."""

from __future__ import annotations

import os
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

AREAS = [
    "salud",
    "trabajo",
    "amor",
    "amigos",
    "finanzas",
    "crecimiento",
    "ocio",
    "espiritualidad",
]


def _connect() -> Any:
    dsn = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")
    return psycopg2.connect(dsn)


def _ensure_table(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lina.wheel_of_life (
                id              BIGSERIAL PRIMARY KEY,
                area            VARCHAR(50) NOT NULL,
                score           INTEGER NOT NULL CHECK (score >= 1 AND score <= 10),
                notes           TEXT,
                evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                week_start      DATE GENERATED ALWAYS AS (
                    DATE_TRUNC('week', evaluated_at)::DATE
                ) STORED
            );
            CREATE INDEX IF NOT EXISTS idx_wheel_week_area
                ON lina.wheel_of_life (week_start, area);
        """)
        conn.commit()


def save_evaluation(area: str, score: int, notes: str = "") -> dict[str, Any]:
    """Save a single wheel evaluation and return the created row."""
    if area not in AREAS:
        raise ValueError(f"Área inválida: {area}. Válidas: {', '.join(AREAS)}")
    if not 1 <= score <= 10:
        raise ValueError("Score debe estar entre 1 y 10")

    conn = _connect()
    try:
        _ensure_table(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO lina.wheel_of_life (area, score, notes)
                VALUES (%s, %s, %s)
                RETURNING id, area, score, notes,
                          evaluated_at AT TIME ZONE 'America/Montevideo' AS evaluated_at,
                          week_start
                """,
                (area, score, notes),
            )
            row = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    return {
        "id": row["id"],
        "area": row["area"],
        "score": row["score"],
        "notes": row["notes"],
        "evaluated_at": row["evaluated_at"].isoformat(),
        "week_start": str(row["week_start"]),
    }


def get_current_wheel() -> list[dict[str, Any]]:
    """Get the latest evaluation for each area (current wheel snapshot)."""
    conn = _connect()
    try:
        _ensure_table(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT ON (area)
                    area, score, notes,
                    evaluated_at AT TIME ZONE 'America/Montevideo' AS evaluated_at,
                    week_start
                FROM lina.wheel_of_life
                ORDER BY area, evaluated_at DESC
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "area": r["area"],
            "score": r["score"],
            "notes": r["notes"],
            "evaluated_at": r["evaluated_at"].isoformat(),
            "week_start": str(r["week_start"]),
        }
        for r in rows
    ]


def get_wheel_history(weeks: int = 4) -> list[dict[str, Any]]:
    """Get wheel evaluations for the last N weeks grouped by week."""
    conn = _connect()
    try:
        _ensure_table(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT week_start, area, MAX(score) AS score,
                       MAX(evaluated_at) AT TIME ZONE 'America/Montevideo' AS last_evaluated
                FROM lina.wheel_of_life
                WHERE week_start >= DATE_TRUNC('week', NOW()) - INTERVAL '%s weeks'
                GROUP BY week_start, area
                ORDER BY week_start DESC, area
            """, (weeks,))
            rows = cur.fetchall()
    finally:
        conn.close()

    # Group by week
    weeks_data: dict[str, dict[str, Any]] = {}
    for r in rows:
        ws = str(r["week_start"])
        if ws not in weeks_data:
            weeks_data[ws] = {"week_start": ws, "areas": {}, "summary": {}}
        weeks_data[ws]["areas"][r["area"]] = r["score"]
        weeks_data[ws]["last_evaluated"] = r["last_evaluated"].isoformat()

    result = []
    for ws in sorted(weeks_data.keys(), reverse=True):
        wd = weeks_data[ws]
        scores = [wd["areas"].get(a, 0) for a in AREAS if wd["areas"].get(a)]
        wd["average"] = round(sum(scores) / len(scores), 1) if scores else 0
        wd["count"] = len(scores)
        wd["missing"] = [a for a in AREAS if a not in wd["areas"]]
        wd["summary"] = {
            "highest": max(wd["areas"].items(), key=lambda x: x[1])[0] if wd["areas"] else None,
            "lowest": min(wd["areas"].items(), key=lambda x: x[1])[0] if wd["areas"] else None,
        }
        result.append(wd)

    return result
