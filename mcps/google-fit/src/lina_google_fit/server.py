"""LINA Google Fit MCP — FastMCP server.

Tools:
    fit_steps(days)         → pasos diarios últimos N días
    fit_sleep(days)         → sueño (horas, fases) últimos N días
    fit_heart_rate(days)    → BPM promedio diario últimos N días
    fit_activity(days)      → minutos activos diarios últimos N días
    fit_weight()            → peso más reciente
    fit_daily_summary(date) → todo junto para un día
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("lina-google-fit", host="0.0.0.0", port=8000)

# ── Auth ─────────────────────────────────────────────────────────────────────

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")


def _get_service():
    if not CLIENT_ID or not REFRESH_TOKEN:
        raise RuntimeError("Google Fit credentials not configured")
    creds = Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    return build("fitness", "v1", credentials=creds)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _aggregate_daily(service, data_type: str, days: int, data_source: str) -> list[dict]:
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    body = {
        "aggregateBy": [{"dataTypeName": data_type}],
        "bucketByTime": {"durationMillis": 86400000},
        "startTimeMillis": start_ms,
        "endTimeMillis": end_ms,
    }
    ds = service.users().dataset()
    result = ds.aggregate(userId="me", body=body).execute()
    daily: dict[str, float] = {}
    for bucket in result.get("bucket", []):
        ms = int(bucket["startTimeMillis"])
        day = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        for ds2 in bucket.get("dataset", []):
            for point in ds2.get("point", []):
                for val in point.get("value", []):
                    fp = val.get("fpVal") or val.get("intVal") or 0
                    daily[day] = daily.get(day, 0) + (fp if fp else 0)
    return [{"date": d, "value": round(v, 1)} for d, v in sorted(daily.items())]


def _sleep_daily(service, days: int) -> list[dict]:
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    body = {
        "aggregateBy": [{"dataTypeName": "com.google.sleep.segment"}],
        "bucketByTime": {"durationMillis": 86400000},
        "startTimeMillis": start_ms,
        "endTimeMillis": end_ms,
    }
    result = service.users().dataset().aggregate(userId="me", body=body).execute()
    daily: dict[str, float] = {}
    for bucket in result.get("bucket", []):
        ms = int(bucket["startTimeMillis"])
        day = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        for ds2 in bucket.get("dataset", []):
            for point in ds2.get("point", []):
                start = int(point["startTimeNanos"]) / 1e9
                end = int(point["endTimeNanos"]) / 1e9
                duration_h = (end - start) / 3600
                daily[day] = daily.get(day, 0) + duration_h
    return [{"date": d, "hours": round(v, 2)} for d, v in sorted(daily.items())]


def _hr_daily(service, days: int) -> list[dict]:
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    body = {
        "aggregateBy": [{"dataTypeName": "com.google.heart_rate.bpm"}],
        "bucketByTime": {"durationMillis": 86400000},
        "startTimeMillis": start_ms,
        "endTimeMillis": end_ms,
    }
    result = service.users().dataset().aggregate(userId="me", body=body).execute()
    daily: dict[str, list] = {}
    for bucket in result.get("bucket", []):
        ms = int(bucket["startTimeMillis"])
        day = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        for ds2 in bucket.get("dataset", []):
            for point in ds2.get("point", []):
                for val in point.get("value", []):
                    bpm = val.get("fpVal") or 0
                    if bpm > 0:
                        daily.setdefault(day, []).append(bpm)
    out = []
    for d, bpms in sorted(daily.items()):
        bpms_sorted = sorted(bpms)
        avg = sum(bpms) / len(bpms) if bpms else 0
        out.append(
            {
                "date": d,
                "avg_bpm": round(avg, 1),
                "min_bpm": round(bpms_sorted[0], 1) if bpms_sorted else 0,
                "max_bpm": round(bpms_sorted[-1], 1) if bpms_sorted else 0,
                "readings": len(bpms),
            }
        )
    return out


# ── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
async def fit_steps(days: int = 7) -> list[dict]:
    """Pasos diarios de los últimos N días (default 7).

    Returns [{"date": "2026-06-10", "value": 8432}, ...].
    """
    loop = asyncio.get_running_loop()

    def _run():
        service = _get_service()
        return _aggregate_daily(service, "com.google.step_count.delta", days, "estimated_steps")

    return await loop.run_in_executor(None, _run)


@mcp.tool()
async def fit_sleep(days: int = 7) -> list[dict]:
    """Sueño diario de los últimos N días (default 7).

    Returns [{"date": "2026-06-10", "hours": 7.2}, ...].
    """
    loop = asyncio.get_running_loop()

    def _run():
        service = _get_service()
        return _sleep_daily(service, days)

    return await loop.run_in_executor(None, _run)


@mcp.tool()
async def fit_heart_rate(days: int = 7) -> list[dict]:
    """Ritmo cardíaco diario de los últimos N días.

    Returns [{"date":"...","avg_bpm":72,"min_bpm":55,"max_bpm":120,"readings":45}, ...].
    """
    loop = asyncio.get_running_loop()

    def _run():
        service = _get_service()
        return _hr_daily(service, days)

    return await loop.run_in_executor(None, _run)


@mcp.tool()
async def fit_activity(days: int = 7) -> list[dict]:
    """Minutos activos diarios de los últimos N días.

    Returns [{"date": "2026-06-10", "minutes_active": 45}, ...].
    """
    loop = asyncio.get_running_loop()

    def _run():
        service = _get_service()
        return _aggregate_daily(service, "com.google.active_minutes", days, "merge_active_minutes")

    return await loop.run_in_executor(None, _run)


@mcp.tool()
async def fit_weight() -> dict:
    """Peso más reciente registrado en Google Fit.

    Returns {"date":"...","value_kg":82.5} or {"has_data":false}.
    """
    loop = asyncio.get_running_loop()

    def _run():
        service = _get_service()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = now_ms - 90 * 86400000
        body = {
            "aggregateBy": [{"dataTypeName": "com.google.weight"}],
            "bucketByTime": {"durationMillis": 86400000},
            "startTimeMillis": start_ms,
            "endTimeMillis": now_ms,
        }
        result = service.users().dataset().aggregate(userId="me", body=body).execute()
        latest = None
        latest_day = ""
        for bucket in result.get("bucket", []):
            ms = int(bucket["startTimeMillis"])
            day = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            for ds2 in bucket.get("dataset", []):
                for point in ds2.get("point", []):
                    for val in point.get("value", []):
                        w = val.get("fpVal") or 0
                        if w > 10:
                            latest = w
                            latest_day = day
        if latest:
            return {"date": latest_day, "value_kg": round(float(latest), 1)}
        return {"has_data": False}

    return await loop.run_in_executor(None, _run)


@mcp.tool()
async def fit_daily_summary(date_str: str = "") -> dict:
    """Resumen completo de salud para un día (default: hoy).

    Args:
        date_str: Fecha en formato YYYY-MM-DD. Vacío = hoy.

    Returns: {date, steps, sleep_hours, avg_bpm, active_minutes, weight_kg}
    """
    target = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
    loop = asyncio.get_running_loop()

    def _run():
        service = _get_service()
        steps = _aggregate_daily(service, "com.google.step_count.delta", 1, "estimated_steps")
        sleep_data = _sleep_daily(service, 1)
        hr_data = _hr_daily(service, 1)
        active = _aggregate_daily(service, "com.google.active_minutes", 1, "merge_active_minutes")
        return {
            "date": target.isoformat(),
            "steps": steps[0]["value"] if steps else 0,
            "sleep_hours": sleep_data[0]["hours"] if sleep_data else 0,
            "avg_bpm": hr_data[0]["avg_bpm"] if hr_data else 0,
            "active_minutes": active[0]["value"] if active else 0,
        }

    return await loop.run_in_executor(None, _run)


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logger.info("LINA Google Fit MCP starting on port 8000")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
