#!/usr/bin/env python3
"""
bot-health — Bot Health Dashboard para el ecosistema LINA.

Monitorea los 3 bots (LINA:3000, Cline:3001, Gemma:3002),
el Comm Bridge systemd service, y la base de datos PostgreSQL.

Uso:
    python3 bin/bot-health                  # reporte legible
    python3 bin/bot-health --json           # reporte JSON (stdout)
    python3 bin/bot-health --watch          # loop cada 30s
    python3 bin/bot-health --json --watch   # JSON streaming

Exit codes:
    0 = healthy (todo OK)
    1 = degraded (algún componente con problemas no críticos)
    2 = critical (bots o DB caídos)

Dependencias:
    httpx, asyncpg (disponibles en el entorno LINA)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx

# ─── Config ──────────────────────────────────────────────────────────────────

BOTS = {
    "lina": {
        "url": os.environ.get("LINA_GOOSED_URL", "https://localhost:3000"),
        "port": 3000,
        "secret": os.environ.get("LINA_GOOSED_SECRET", ""),
    },
    "cline": {
        "url": os.environ.get("CLINE_GOOSED_URL", "https://localhost:3001"),
        "port": 3001,
        "secret": os.environ.get("CLINE_GOOSED_SECRET", ""),
    },
    "gemma": {
        "url": os.environ.get("GEMMA_GOOSED_URL", "https://localhost:3002"),
        "port": 3002,
        "secret": os.environ.get("GEMMA_GOOSED_SECRET", ""),
    },
}

BRIDGE_SERVICE = os.environ.get("BRIDGE_SERVICE_NAME", "lina-comm-bridge")
DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina_dev@localhost:5432/lina",
)

HTTP_TIMEOUT = 10.0  # segundos por request


# ─── Data Models ─────────────────────────────────────────────────────────────


@dataclass
class BotCheck:
    """Resultado del healthcheck de un bot."""
    name: str
    port: int
    online: bool = False
    status_code: int = 0
    error: str = ""
    latency_ms: float = 0.0
    sessions: int = 0
    version: str = ""


@dataclass
class BridgeCheck:
    """Resultado del healthcheck del Comm Bridge."""
    active: bool = False
    service_name: str = ""
    state: str = ""
    error: str = ""


@dataclass
class DatabaseCheck:
    """Resultado del healthcheck de PostgreSQL."""
    connected: bool = False
    latency_ms: float = 0.0
    error: str = ""
    db_url_sanitized: str = ""


@dataclass
class HealthReport:
    """Reporte completo de salud del ecosistema."""
    timestamp: str = ""
    overall: str = "unknown"  # healthy | degraded | critical
    bots: dict[str, BotCheck] = field(default_factory=dict)
    bridge: BridgeCheck = field(default_factory=BridgeCheck)
    database: DatabaseCheck = field(default_factory=DatabaseCheck)


# ─── Collectors ──────────────────────────────────────────────────────────────


async def check_bot(client: httpx.AsyncClient, name: str, url: str, port: int, secret: str) -> BotCheck:
    """Verifica un bot via GET /status."""
    result = BotCheck(name=name, port=port)
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["x-secret-key"] = secret

    try:
        start = time.monotonic()
        resp = await client.get(f"{url}/status", headers=headers, timeout=HTTP_TIMEOUT)
        elapsed = (time.monotonic() - start) * 1000
        result.latency_ms = round(elapsed, 1)
        result.status_code = resp.status_code

        if resp.status_code == 200:
            result.online = True
            try:
                data = resp.json()
                if isinstance(data, dict):
                    result.sessions = len(data.get("sessions", {}))
                    result.version = data.get("version", "")
            except (json.JSONDecodeError, AttributeError):
                pass
        else:
            result.error = f"HTTP {resp.status_code}"
    except httpx.ConnectError:
        result.error = "connection refused"
    except httpx.TimeoutException:
        result.error = "timeout"
    except Exception as e:
        result.error = str(e)[:120]

    return result


async def check_bridge() -> BridgeCheck:
    """Verifica el Comm Bridge via systemctl is-active."""
    result = BridgeCheck(service_name=BRIDGE_SERVICE)
    try:
        start = time.monotonic()
        r = subprocess.run(
            ["systemctl", "is-active", BRIDGE_SERVICE],
            capture_output=True, text=True, timeout=10,
        )
        state = r.stdout.strip()
        result.state = state
        result.active = (state == "active")

        if r.returncode != 0 and not result.active:
            # Try --user scope
            r2 = subprocess.run(
                ["systemctl", "--user", "is-active", BRIDGE_SERVICE],
                capture_output=True, text=True, timeout=10,
            )
            state2 = r2.stdout.strip()
            if r2.returncode == 0 or state2 == "active":
                result.active = True
                result.state = f"user:{state2}"
    except FileNotFoundError:
        result.error = "systemctl not found (not Linux?)"
    except subprocess.TimeoutExpired:
        result.error = "timeout checking service"
    except Exception as e:
        result.error = str(e)[:120]

    return result


async def check_database() -> DatabaseCheck:
    """Verifica conexión a PostgreSQL."""
    result = DatabaseCheck()
    result.db_url_sanitized = DB_URL.replace(
        DB_URL.split("@")[0].split(":")[-1], "****"
    ) if "@" in DB_URL else DB_URL

    try:
        
        start = time.monotonic()
        conn = await asyncpg.connect(DB_URL, timeout=HTTP_TIMEOUT)
        elapsed = (time.monotonic() - start) * 1000
        result.latency_ms = round(elapsed, 1)

        # Verify connection with a simple query
        val = await conn.fetchval("SELECT 1")
        result.connected = (val == 1)

        await conn.close()
    except ImportError:
        result.error = "asyncpg not installed"
    except Exception as e:
        result.error = str(e)[:120]

    return result


# ─── Report builder ──────────────────────────────────────────────────────────


def _compute_overall(report: HealthReport) -> str:
    """Calcula el estado general del ecosistema."""
    bots_online = sum(1 for b in report.bots.values() if b.online)
    total_bots = len(report.bots)

    if total_bots == 0:
        return "critical"

    if bots_online == total_bots and report.database.connected:
        return "healthy"

    if bots_online >= total_bots / 2 and report.database.connected:
        return "degraded"

    return "critical"


def _build_json(report: HealthReport) -> str:
    """Construye el reporte JSON."""
    d: dict[str, Any] = {
        "timestamp": report.timestamp,
        "overall": report.overall,
        "bots": {},
        "bridge": asdict(report.bridge),
        "database": asdict(report.database),
    }

    for name, bot in report.bots.items():
        d["bots"][name] = {
            "online": bot.online,
            "port": bot.port,
            "status_code": bot.status_code,
            "latency_ms": bot.latency_ms,
            "sessions": bot.sessions,
            "version": bot.version,
            "error": bot.error,
        }

    return json.dumps(d, indent=2, ensure_ascii=False)


def _build_text(report: HealthReport) -> str:
    """Construye el reporte en texto legible."""
    lines: list[str] = []
    lines.append("╔════════════════════════════════════════════════════╗")
    lines.append(f"║    🦆 Bot Health Dashboard — {report.timestamp[:19]}  ║")
    lines.append("╚════════════════════════════════════════════════════╝")
    lines.append("")

    # Overall status
    status_icon = {"healthy": "✅", "degraded": "⚠️", "critical": "❌"}.get(
        report.overall, "❓"
    )
    lines.append(f"  Overall: {status_icon} {report.overall.upper()}")
    lines.append("")

    # Bots
    lines.append("  ── Bots ──────────────────────────────")
    for name in ["lina", "cline", "gemma"]:
        bot = report.bots.get(name)
        if bot is None:
            lines.append(f"    {name:8s}  ⚪ unknown (not checked)")
            continue
        icon = "✅" if bot.online else "❌"
        latency = f"{bot.latency_ms:.0f}ms" if bot.latency_ms > 0 else "-"
        sessions = f"  {bot.sessions} sessions" if bot.sessions > 0 else ""
        err = f"  ⚠ {bot.error}" if bot.error else ""
        version = f"  v{bot.version[:20]}" if bot.version else ""
        lines.append(f"    {name:8s}  {icon}  :{bot.port}  {latency}{sessions}{version}{err}")

    lines.append("")

    # Bridge
    lines.append("  ── Comm Bridge ────────────────────────")
    bridge = report.bridge
    icon = "✅" if bridge.active else ("❌" if bridge.error else "⚪")
    state = bridge.state or bridge.error or "not checked"
    lines.append(f"    bridge  {icon}  {bridge.service_name} [{state}]")
    lines.append("")

    # Database
    lines.append("  ── Database ───────────────────────────")
    db = report.database
    icon = "✅" if db.connected else "❌"
    latency = f"{db.latency_ms:.0f}ms" if db.latency_ms > 0 else "-"
    err = f"  ⚠ {db.error}" if db.error else ""
    lines.append(f"    postgres  {icon}  {latency}{err}")
    lines.append("")

    # Summary
    bots_online = sum(1 for b in report.bots.values() if b.online)
    total_bots = len(report.bots)
    lines.append(f"  Resumen: {bots_online}/{total_bots} bots online, "
                 f"{'DB ✅' if report.database.connected else 'DB ❌'}, "
                 f"{'Bridge ✅' if report.bridge.active else 'Bridge ❌'}")

    return "\n".join(lines)


# ─── Main ────────────────────────────────────────────────────────────────────


async def collect() -> HealthReport:
    """Ejecuta todos los checks y devuelve un HealthReport."""
    timestamp = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient(verify=False) as client:
        bot_tasks = {
            name: check_bot(client, name, cfg["url"], cfg["port"], cfg["secret"])
            for name, cfg in BOTS.items()
        }
        bot_results = await asyncio.gather(*bot_tasks.values())
        bots = dict(zip(bot_tasks.keys(), bot_results))

    bridge = await check_bridge()
    database = await check_database()

    report = HealthReport(
        timestamp=timestamp,
        bots=bots,
        bridge=bridge,
        database=database,
    )
    report.overall = _compute_overall(report)
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bot Health Dashboard — monitoreo del ecosistema LINA"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Salida en formato JSON"
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Loop continuo cada 30s"
    )
    parser.add_argument(
        "--interval", type=int, default=30,
        help="Intervalo en segundos para --watch (default: 30)"
    )
    args = parser.parse_args()

    loop_count = 0
    while True:
        loop_count += 1
        if args.watch:
            print(f"\n📊 Bot Health Dashboard — check #{loop_count} "
                  f"({datetime.now().strftime('%H:%M:%S')})")
            print("─" * 50)

        report = await collect()

        if args.json:
            print(_build_json(report))
        else:
            print(_build_text(report))

        if not args.watch:
            break

        await asyncio.sleep(args.interval)

    # Return exit code based on overall status
    return {"healthy": 0, "degraded": 1, "critical": 2}.get(report.overall, 2)


def run():
    """Entry point for CLI."""
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


if __name__ == "__main__":
    run()
