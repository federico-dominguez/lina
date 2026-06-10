"""PostgreSQL cache for Google Fit data — reduces API calls."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor


def _connect() -> any:
    dsn = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")
    return psycopg2.connect(dsn)


def _ensure_table(conn: any) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lina.health_cache (
                data_type   VARCHAR(64) NOT NULL,
                date        DATE NOT NULL,
                value       JSONB NOT NULL DEFAULT '{}'::jsonb,
                fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (data_type, date)
            );
        """)
        conn.commit()


def get_cached(data_type: str, target_date: date) -> dict | None:
    """Get cached data if fresh (< 1h)."""
    conn = _connect()
    try:
        _ensure_table(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT value, fetched_at FROM lina.health_cache
                   WHERE data_type = %s AND date = %s
                     AND fetched_at > NOW() - INTERVAL '1 hour'""",
                (data_type, target_date),
            )
            row = cur.fetchone()
            if row:
                return {"value": row["value"], "cached": True}
    finally:
        conn.close()
    return None


def set_cache(data_type: str, target_date: date, value: any) -> None:
    """Cache API result."""
    conn = _connect()
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lina.health_cache (data_type, date, value, fetched_at)
                   VALUES (%s, %s, %s::jsonb, NOW())
                   ON CONFLICT (data_type, date) DO UPDATE
                   SET value = %s::jsonb, fetched_at = NOW()""",
                (data_type, target_date, json.dumps(value), json.dumps(value)),
            )
            conn.commit()
    finally:
        conn.close()
