#!/usr/bin/env python3
"""
Bot Health Dashboard — Monitor de salud del sistema LINA.

Endpoints:
  GET /health          → Estado de LINA, Cline, Gemma (heartbeat DB + puertos goosed)
  GET /health/db       → Conexión a PostgreSQL
  GET /health/deepseek → Saldo de la API de DeepSeek

Arquitectura:
  Los 3 bots (LINA, Cline, Gemma) escriben heartbeats periódicos en la tabla
  `bot_heartbeat` de PostgreSQL. Además exponen goosed en los puertos 3000-3002.
  Este dashboard consulta ambas fuentes para determinar el estado de cada bot.

Uso:
  python3 infrastructure/monitor/bot_health.py [--port PUERTO]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

try:
    import asyncpg
except ImportError:
    asyncpg = None

# ─── Config ──────────────────────────────────────────────────────────────────

LINA_DB_URL = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

BOTS = {
    "lina": {"port": 3000, "label": "LINA 🤖"},
    "cline": {"port": 3001, "label": "Cline 💻"},
    "gemma": {"port": 3002, "label": "Gemma 🧪"},
}

HEARTBEAT_TTL = 120  # segundos sin heartbeat → dead

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot_health")


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def check_goosed_port(host: str, port: int, timeout: float = 3.0) -> dict[str, Any]:
    """Intenta conectar a un puerto goosed y obtener /health."""
    result: dict[str, Any] = {"port": port, "reachable": False}
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        result["reachable"] = True

        # Pedir /health vía HTTP
        request = f"GET /health HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        writer.write(request.encode())
        await writer.drain()

        response = b""
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
            if not chunk:
                break
            response += chunk

        writer.close()
        await writer.wait_closed()

        # Parsear el body del HTTP response
        if b"\r\n\r\n" in response:
            _, body = response.split(b"\r\n\r\n", 1)
            body_str = body.decode("utf-8", errors="replace").strip()
            if body_str:
                try:
                    data = json.loads(body_str)
                    result["health"] = data
                except json.JSONDecodeError:
                    result["raw_response"] = body_str[:200]

    except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
        result["error"] = str(e)

    return result


async def check_db_health() -> dict[str, Any]:
    """Verifica conexión a PostgreSQL y estado de tablas críticas."""
    result: dict[str, Any] = {
        "connected": False,
        "configured": bool(LINA_DB_URL),
    }

    if not asyncpg:
        result["error"] = "asyncpg no instalado"
        return result

    try:
        conn = await asyncpg.connect(LINA_DB_URL, timeout=5)
        result["connected"] = True

        # Version
        version = await conn.fetchval("SELECT version()")
        result["version"] = version.split(",")[0] if version else "unknown"

        # Tamaño de la base
        db_size = await conn.fetchval(
            "SELECT pg_size_pretty(pg_database_size(current_database()))"
        )
        result["database_size"] = db_size

        # Conexiones activas
        active_conns = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE state = 'active' AND pid <> pg_backend_pid()"
        )
        result["active_connections"] = active_conns

        # Tablas críticas y su estado
        critical_tables = ["bot_heartbeat", "circuit_breaker", "agent_messages"]
        tables_info = []
        for table in critical_tables:
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = $1)",
                table,
            )
            if exists:
                count = await conn.fetchval(f"SELECT count(*) FROM {table}")
                tables_info.append({"table": table, "exists": True, "row_count": count})
            else:
                tables_info.append({"table": table, "exists": False})
        result["tables"] = tables_info

        await conn.close()
    except Exception as e:
        result["error"] = str(e)

    return result


async def check_deepseek_health() -> dict[str, Any]:
    """Consulta saldo de la API de DeepSeek."""
    result: dict[str, Any] = {
        "configured": bool(DEEPSEEK_API_KEY),
        "available": False,
    }

    if not DEEPSEEK_API_KEY:
        result["error"] = "DEEPSEEK_API_KEY no configurada"
        return result

    try:
        import urllib.request

        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            result["available"] = data.get("is_available", False)
            balances = data.get("balance_infos", [])
            if balances:
                result["balance"] = balances[0].get("total_balance", "unknown")
                result["currency"] = balances[0].get("currency", "USD")
                result["topped_up"] = balances[0].get("topped_up_balance", "0")
    except Exception as e:
        result["error"] = str(e)

    return result


async def get_bot_heartbeats_from_db() -> dict[str, dict]:
    """Lee heartbeats de la DB para cada bot."""
    heartbeats: dict[str, dict] = {}
    if not asyncpg:
        return heartbeats

    try:
        conn = await asyncpg.connect(LINA_DB_URL, timeout=5)
        rows = await conn.fetch("SELECT bot_name, last_pulse, status, failures FROM bot_heartbeat")
        for row in rows:
            name = row["bot_name"]
            last_pulse = row["last_pulse"]
            status = row["status"]
            failures = row["failures"]

            now = datetime.now(timezone.utc)
            if isinstance(last_pulse, datetime):
                if last_pulse.tzinfo is None:
                    last_pulse = last_pulse.replace(tzinfo=timezone.utc)
                seconds_ago = (now - last_pulse).total_seconds()
            else:
                seconds_ago = None

            heartbeats[name] = {
                "status": status,
                "last_pulse": last_pulse.isoformat() if last_pulse else None,
                "seconds_since_pulse": seconds_ago,
                "failures": failures,
                "alive": status == "alive" and (seconds_ago is None or seconds_ago < HEARTBEAT_TTL),
            }
        await conn.close()
    except Exception:
        pass

    return heartbeats


async def build_health_response() -> dict[str, Any]:
    """Construye la respuesta completa de /health."""
    now = time.time()

    # Heartbeat desde DB
    heartbeats = await get_bot_heartbeats_from_db()

    # Check de puertos goosed
    goosed_checks: dict[str, Any] = {}
    for bot_name, info in BOTS.items():
        port_result = await check_goosed_port("localhost", info["port"])
        goosed_checks[bot_name] = {
            "port": info["port"],
            "reachable": port_result["reachable"],
            "error": port_result.get("error"),
        }

    # Consolidar estado general de cada bot
    bots_status: dict[str, Any] = {}
    overall_status = "ok"
    for bot_name, info in BOTS.items():
        hb = heartbeats.get(bot_name, {})
        goosed = goosed_checks.get(bot_name, {})

        db_alive = hb.get("alive", False)
        port_reachable = goosed.get("reachable", False)

        if db_alive and port_reachable:
            bot_status = "healthy"
        elif db_alive or port_reachable:
            bot_status = "degraded"
            overall_status = "degraded"
        else:
            bot_status = "down"
            overall_status = "degraded"

        bots_status[bot_name] = {
            "label": info["label"],
            "status": bot_status,
            "heartbeat": {
                "db_status": hb.get("status", "unknown"),
                "last_pulse": hb.get("last_pulse"),
                "seconds_since_pulse": hb.get("seconds_since_pulse"),
                "alive": db_alive,
            },
            "goosed": {
                "port": info["port"],
                "reachable": port_reachable,
                "error": goosed.get("error"),
            },
        }

    return {
        "status": overall_status,
        "timestamp": now,
        "timestamp_iso": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "service": "bot-health-dashboard",
        "version": "1.0.0",
        "bots": bots_status,
        "heartbeats_db": heartbeats,
    }


# ─── HTTP Server ─────────────────────────────────────────────────────────────


class BotHealthHTTPHandler:
    """Maneja requests HTTP para el dashboard de salud."""

    def __init__(self):
        self._start_ts = time.time()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line:
                writer.close()
                return

            raw = request_line.decode("utf-8", errors="replace").strip()
            parts = raw.split()
            if len(parts) < 2:
                await self._send(writer, 400, {"error": "Bad Request"})
                return

            method, path = parts[0], parts[1]

            # Leer headers (los ignoramos, solo consumimos)
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if not line or line == b"\r\n":
                    break

            if method != "GET":
                await self._send(writer, 405, {"error": "Method Not Allowed"})
                return

            if path == "/health":
                data = await build_health_response()
                await self._send(writer, 200, data)
            elif path == "/health/db":
                data = await check_db_health()
                await self._send(writer, 200, data)
            elif path == "/health/deepseek":
                data = await check_deepseek_health()
                await self._send(writer, 200, data)
            elif path == "/health/ping":
                await self._send(writer, 200, {"ping": "pong", "uptime": round(time.time() - self._start_ts, 1)})
            elif path == "/":
                # Root: HTML info
                html = self._index_html()
                await self._send_html(writer, 200, html)
            else:
                await self._send(writer, 404, {"error": "Not Found", "available": ["/health", "/health/db", "/health/deepseek", "/health/ping"]})
        except asyncio.TimeoutError:
            await self._send(writer, 408, {"error": "Request Timeout"})
        except Exception as e:
            logger.error("Error handling request: %s", e)
            await self._send(writer, 500, {"error": str(e)})
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _send(self, writer: asyncio.StreamWriter, status: int, data: dict):
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        resp = (
            f"HTTP/1.1 {status} {HTTPStatus(status).phrase}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Access-Control-Allow-Origin: *\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + body
        writer.write(resp)
        await writer.drain()

    async def _send_html(self, writer: asyncio.StreamWriter, status: int, html: str):
        body = html.encode("utf-8")
        resp = (
            f"HTTP/1.1 {status} {HTTPStatus(status).phrase}\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + body
        writer.write(resp)
        await writer.drain()

    def _index_html(self) -> str:
        uptime = round(time.time() - self._start_ts, 1)
        return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>🤖 Bot Health Dashboard — LINA System</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0d1117; color: #c9d1d9; padding: 2rem;
    }}
    h1 {{ color: #58a6ff; margin-bottom: 0.5rem; }}
    .subtitle {{ color: #8b949e; margin-bottom: 2rem; }}
    .endpoints {{
      background: #161b22; border: 1px solid #30363d; border-radius: 8px;
      padding: 1.5rem; margin-bottom: 2rem;
    }}
    .endpoints h2 {{ color: #f0883e; margin-bottom: 1rem; }}
    .endpoint {{
      display: flex; align-items: center; padding: 0.5rem 0;
      border-bottom: 1px solid #21262d;
    }}
    .endpoint:last-child {{ border-bottom: none; }}
    .method {{ 
      background: #1f6feb; color: #fff; padding: 2px 8px; border-radius: 4px;
      font-size: 0.75rem; font-weight: 600; margin-right: 1rem; min-width: 45px; text-align: center;
    }}
    .path {{ font-family: monospace; color: #58a6ff; margin-right: 1rem; }}
    .desc {{ color: #8b949e; font-size: 0.9rem; }}
    .status {{ margin-top: 1rem; color: #8b949e; font-size: 0.85rem; }}
    .status span {{ color: #3fb950; }}
    a {{ color: #58a6ff; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>🤖 Bot Health Dashboard</h1>
  <p class="subtitle">Sistema LINA — Monitoreo de salud de bots</p>

  <div class="endpoints">
    <h2>📡 Endpoints disponibles</h2>
    <div class="endpoint">
      <span class="method">GET</span>
      <span class="path">/health</span>
      <span class="desc">Estado completo de LINA, Cline y Gemma</span>
    </div>
    <div class="endpoint">
      <span class="method">GET</span>
      <span class="path">/health/db</span>
      <span class="desc">Estado de conexión a PostgreSQL</span>
    </div>
    <div class="endpoint">
      <span class="method">GET</span>
      <span class="path">/health/deepseek</span>
      <span class="desc">Saldo de la API de DeepSeek</span>
    </div>
    <div class="endpoint">
      <span class="method">GET</span>
      <span class="path">/health/ping</span>
      <span class="desc">Healthcheck simple (ping/pong)</span>
    </div>
    <div class="status">
      Uptime: <span>{uptime}s</span>
      &nbsp;·&nbsp; 
      <a href="/health">Ver JSON completo →</a>
    </div>
  </div>
</body>
</html>"""


# ─── Main ────────────────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description="Bot Health Dashboard")
    parser.add_argument("--port", type=int, default=8080, help="Puerto del servidor (default: 8080)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host (default: 0.0.0.0)")
    args = parser.parse_args()

    handler = BotHealthHTTPHandler()
    server = await asyncio.start_server(handler.handle, args.host, args.port)
    addr = server.sockets[0].getsockname() if server.sockets else (args.host, args.port)

    logger.info("╔══════════════════════════════════════════════╗")
    logger.info("║     🤖 Bot Health Dashboard iniciado        ║")
    logger.info(f"║     http://{addr[0]}:{addr[1]}                 ║")
    logger.info("║                                              ║")
    logger.info("║  Endpoints:                                  ║")
    logger.info(f"║    /health       → estado bots               ║")
    logger.info(f"║    /health/db    → PostgreSQL                ║")
    logger.info(f"║    /health/deepseek → DeepSeek API           ║")
    logger.info(f"║    /health/ping  → ping/pong                 ║")
    logger.info("╚══════════════════════════════════════════════╝")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
