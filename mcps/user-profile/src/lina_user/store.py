"""PostgreSQL store for User Profile."""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


def _connect() -> Any:
    dsn = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")
    return psycopg2.connect(dsn)


def _ensure_table(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lina.user_profile (
                key         VARCHAR(100) PRIMARY KEY,
                value       JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        conn.commit()


def set_profile(key: str, value: Any) -> dict[str, Any]:
    """Set a profile field. Value is stored as JSONB."""
    conn = _connect()
    try:
        _ensure_table(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO lina.user_profile (key, value, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (key) DO UPDATE SET value = %s::jsonb, updated_at = NOW()
                RETURNING key, value, updated_at AT TIME ZONE 'America/Montevideo' AS updated_at
                """,
                (key, json.dumps(value), json.dumps(value)),
            )
            row = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    return {
        "key": row["key"],
        "value": row["value"],
        "updated_at": row["updated_at"].isoformat(),
    }


def get_profile(key: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
    """Get a specific profile field or all fields."""
    conn = _connect()
    try:
        _ensure_table(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if key:
                cur.execute(
                    """SELECT key, value, updated_at AT TIME ZONE 'America/Montevideo' AS updated_at
                       FROM lina.user_profile WHERE key = %s""",
                    (key,),
                )
                row = cur.fetchone()
                if not row:
                    return {"key": key, "value": None, "found": False}
                return {
                    "key": row["key"],
                    "value": row["value"],
                    "updated_at": row["updated_at"].isoformat(),
                    "found": True,
                }
            else:
                cur.execute(
                    """SELECT key, value, updated_at AT TIME ZONE 'America/Montevideo' AS updated_at
                       FROM lina.user_profile ORDER BY key"""
                )
                rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "key": r["key"],
            "value": r["value"],
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


def delete_profile(key: str) -> dict[str, Any]:
    """Delete a profile field."""
    conn = _connect()
    try:
        _ensure_table(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("DELETE FROM lina.user_profile WHERE key = %s RETURNING key", (key,))
            deleted = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    return {"key": key, "deleted": deleted is not None}
