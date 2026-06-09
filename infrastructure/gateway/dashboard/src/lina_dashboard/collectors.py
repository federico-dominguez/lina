"""
collectors.py — Data collection layer for LINA Dashboard.

Polling targets:
1. Bot observe ports (9091=Goose, 9092=CLINE, 9093=LINA)
2. Docker socket proxy (container list + health)
3. MCP nginx gateway (health check per MCP endpoint)
4. PostgreSQL — comm_messages stats (via psycopg2)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────────

BOT_NAMES = {
    9090: "Gemma",
    9091: "Goose",
    9092: "CLINE",
    9093: "LINA",
}

# Telegram handles y orden de display
BOT_METADATA: list[dict[str, Any]] = [
    {"name": "Goose",  "port": 9091, "username": "s_goose_bot"},
    {"name": "LINA",   "port": 9093, "username": "s_lina_bot"},
    {"name": "CLINE",  "port": 9092, "username": "s_cline_bot"},
    {"name": "Gemma",  "port": 9090, "username": "s_gemma_bot"},
]

MCP_REGISTRY: list[dict[str, Any]] = [
    {"name": "lina-secrets",      "port": 8101, "url": "/", "used_by": ["LINA", "CLINE", "Goose", "Gemma"]},
    {"name": "lina-fs-safe",      "port": 8102, "url": "/", "used_by": ["LINA", "CLINE", "Goose", "Gemma"]},
    {"name": "lina-shell-policy", "port": 8103, "url": "/", "used_by": ["LINA", "CLINE"]},
    {"name": "lina-systemd-user", "port": 8104, "url": "/", "used_by": ["LINA"]},
    {"name": "lina-moodle",       "port": 8105, "url": "/", "used_by": ["LINA"]},
    {"name": "lina-db",           "port": 8106, "url": "/", "used_by": ["LINA", "CLINE", "Goose", "Gemma"]},
    {"name": "lina-github",       "port": 8107, "url": "/", "used_by": ["LINA", "CLINE"]},
    {"name": "lina-gitlab",       "port": 8108, "url": "/", "used_by": ["LINA"]},
    {"name": "lina-gcalendar",    "port": 8109, "url": "/", "used_by": ["LINA"]},
    {"name": "lina-gns3",         "port": 8110, "url": "/", "used_by": ["LINA"]},
    {"name": "lina-orchestrator", "port": 8111, "url": "/", "used_by": ["LINA", "CLINE"]},
]


@dataclass
class BotStatus:
    name: str
    port: int
    online: bool
    username: str = ""
    error: str | None = None
    sessions: list[dict[str, Any]] = field(default_factory=list)
    session_count: int = 0
    last_event: str | None = None
    clients: int = 0

    @property
    def status_label(self) -> str:
        return "online" if self.online else "offline"

    @property
    def dot_class(self) -> str:
        return "online" if self.online else "offline"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "port": self.port,
            "online": self.online,
            "username": self.username,
            "error": self.error,
            "session_count": self.session_count,
            "last_event": self.last_event,
            "clients": self.clients,
        }


@dataclass
class ContainerStatus:
    name: str
    state: str
    status: str
    ports: str
    uptime: str | None = None
    image: str = ""

    @property
    def healthy(self) -> bool:
        return self.state == "running"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "status": self.status,
            "ports": self.ports,
            "uptime": self.uptime,
            "image": self.image,
        }


@dataclass
class MCPStatus:
    name: str
    port: int
    online: bool
    used_by: list[str] | None = None
    error: str | None = None
    response_time_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "port": self.port,
            "online": self.online,
            "used_by": self.used_by,
            "error": self.error,
            "response_time_ms": self.response_time_ms,
        }


@dataclass
class CommStats:
    """Estadísticas de mensajería del Comm Bridge."""

    total: int = 0
    delivered: int = 0
    failed: int = 0
    ignored: int = 0
    sent: int = 0
    retrying: int = 0
    by_sender: dict[str, int] = field(default_factory=dict)
    latest_errors: list[dict[str, Any]] = field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None
    db_available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "delivered": self.delivered,
            "failed": self.failed,
            "ignored": self.ignored,
            "sent": self.sent,
            "retrying": self.retrying,
            "by_sender": self.by_sender,
            "latest_errors": self.latest_errors,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "db_available": self.db_available,
        }


@dataclass
class DashboardSnapshot:
    bots: list[BotStatus] = field(default_factory=list)
    containers: list[ContainerStatus] = field(default_factory=list)
    mcps: list[MCPStatus] = field(default_factory=list)
    comm_stats: CommStats | None = None
    all_sessions: list[dict[str, Any]] = field(default_factory=list)
    collected_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "bots": [b.to_dict() for b in self.bots],
            "containers": [c.to_dict() for c in self.containers],
            "mcps": [m.to_dict() for m in self.mcps],
            "comm_stats": self.comm_stats.to_dict() if self.comm_stats else None,
            "sessions": self.all_sessions,
            "collected_at": self.collected_at,
        }


# ── PostgreSQL connection ──────────────────────────────────────────────────

LINA_DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina_dev@localhost:5432/lina",
)


# ─────────────────────────────────────────────────────────────────────────────
# Collectors
# ─────────────────────────────────────────────────────────────────────────────


class DashboardCollector:
    """Async poller that gathers all dashboard metrics."""

    def __init__(
        self,
        bot_ports: list[int] | None = None,
        docker_url: str = "http://localhost:2375",
        mcp_host: str = "localhost",
        bot_host: str = "localhost",
        timeout: float = 5.0,
        db_url: str = "",
    ):
        self.bot_ports = bot_ports or [m["port"] for m in BOT_METADATA]
        self.docker_url = docker_url
        self.mcp_host = mcp_host
        self.bot_host = bot_host
        self.timeout = timeout
        self.db_url = db_url or LINA_DB_URL
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout, verify=False)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Bots ─────────────────────────────────────────────────────────────────

    async def collect_bots(self) -> list[BotStatus]:
        """Poll all bot observe ports for sessions + status.

        Returns bots in BOT_METADATA order (Goose, LINA, CLINE, Gemma),
        filtered by self.bot_ports.
        """
        client = await self._get_client()
        results: list[BotStatus] = []
        enabled_ports = set(self.bot_ports)

        for meta in BOT_METADATA:
            if meta["port"] not in enabled_ports:
                continue
            port = meta["port"]
            username = meta.get("username", "")
            name = BOT_NAMES.get(port, f"Bot:{port}")
            bot = BotStatus(name=name, port=port, online=False, username=username)

            try:
                # Get sessions list
                resp = await client.get(f"http://{self.bot_host}:{port}/api/sessions")
                if resp.status_code == 200:
                    sessions = resp.json()
                    bot.online = True
                    bot.sessions = sessions if isinstance(sessions, list) else []
                    bot.session_count = len(bot.sessions)
                else:
                    bot.error = f"HTTP {resp.status_code}"
                    results.append(bot)
                    continue
            except httpx.ConnectError:
                bot.error = "conexión rechazada"
                results.append(bot)
                continue
            except httpx.TimeoutException:
                bot.error = "timeout"
                results.append(bot)
                continue
            except Exception as e:
                bot.error = str(e)[:80]
                results.append(bot)
                continue

            # Try status endpoint (optional)
            try:
                resp2 = await client.get(f"http://{self.bot_host}:{port}/api/status")
                if resp2.status_code == 200:
                    data = resp2.json()
                    bot.clients = data.get("clients", 0)
            except Exception:
                pass

            # Extract last event from sessions
            if bot.sessions:
                latest = max(
                    (s.get("last_event") or s.get("first_event", "") for s in bot.sessions if s),
                    default=None,
                )
                bot.last_event = latest

            results.append(bot)

        return results

    # ── Containers ───────────────────────────────────────────────────────────

    async def collect_containers(self) -> list[ContainerStatus]:
        """Poll Docker socket proxy for container list."""
        client = await self._get_client()
        results: list[ContainerStatus] = []

        try:
            resp = await client.get(f"{self.docker_url}/containers/json?all=true")
            if resp.status_code != 200:
                logger.warning("Docker proxy returned %s", resp.status_code)
                return results

            containers = resp.json()
            for c in containers:
                names = c.get("Names", [])
                name = (names[0] if names else c.get("Id", "?")[:12]).lstrip("/")
                state = c.get("State", "unknown")
                status = c.get("Status", "")

                # Parse ports
                port_str = ""
                ports = c.get("Ports", [])
                if ports:
                    port_str = ", ".join(
                        f"{p.get('PrivatePort', '?')}→{p.get('PublicPort', '?')}"
                        for p in ports
                        if p.get("PublicPort")
                    )

                results.append(
                    ContainerStatus(
                        name=name,
                        state=state,
                        status=status,
                        ports=port_str,
                        image=c.get("Image", "").split("/")[-1] if c.get("Image") else "",
                    )
                )
        except httpx.ConnectError:
            logger.warning("Docker proxy no disponible en %s", self.docker_url)
        except Exception as e:
            logger.warning("Error coleccionando contenedores: %s", e)

        return results

    # ── MCPs ─────────────────────────────────────────────────────────────────

    async def collect_mcps(self) -> list[MCPStatus]:
        """Health-check all MCP endpoints via nginx gateway."""
        client = await self._get_client()
        results: list[MCPStatus] = []

        for mcp in MCP_REGISTRY:
            url = f"http://{self.mcp_host}:{mcp['port']}{mcp['url']}"
            start = time.monotonic()
            mcp_status = MCPStatus(
                name=mcp["name"],
                port=mcp["port"],
                online=False,
                used_by=mcp.get("used_by"),
            )

            try:
                resp = await client.get(url)
                elapsed = (time.monotonic() - start) * 1000
                mcp_status.response_time_ms = round(elapsed, 1)
                if resp.status_code < 500:
                    mcp_status.online = True
                else:
                    mcp_status.error = f"HTTP {resp.status_code}"
            except httpx.ConnectError:
                mcp_status.error = "conexión rechazada"
            except httpx.TimeoutException:
                mcp_status.error = "timeout"
            except Exception as e:
                mcp_status.error = str(e)[:80]

            results.append(mcp_status)

        return results

    # ── Comm stats ───────────────────────────────────────────────────────────

    async def collect_comm_stats(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> CommStats:
        """Query PostgreSQL for comm_messages statistics.

        Args:
            date_from: ISO date string (e.g. '2026-06-05') or empty for all.
            date_to:   ISO date string (e.g. '2026-06-06') or empty for all.

        Returns:
            CommStats with counts by status, sender breakdown, latest errors.
        """
        stats = CommStats()
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # Build WHERE clause from date filter
            where = ""
            params: list[str] = []
            if date_from:
                params.append(date_from)
                where = " WHERE created_at >= %s::timestamptz"
            if date_to:
                params.append(date_to)
                where += " AND created_at <= %s::timestamptz" if where else " WHERE created_at <= %s::timestamptz"

            # Counts by status
            cur.execute(
                f"SELECT status, COUNT(*) AS cnt FROM comm_messages{where} GROUP BY status ORDER BY status",
                params,
            )
            for row in cur.fetchall():
                s = row["status"]
                c = row["cnt"]
                stats.total += c
                if s == "delivered":
                    stats.delivered = c
                elif s == "failed":
                    stats.failed = c
                elif s == "ignored":
                    stats.ignored = c
                elif s == "sent":
                    stats.sent = c
                elif s == "retrying":
                    stats.retrying = c

            # By sender
            cur.execute(
                f"SELECT sender, COUNT(*) AS cnt FROM comm_messages{where} GROUP BY sender ORDER BY cnt DESC",
                params,
            )
            stats.by_sender = {row["sender"]: row["cnt"] for row in cur.fetchall()}

            # Latest errors (top 10)
            error_where = " WHERE error IS NOT NULL AND error != ''"
            error_params: list[str] = []
            if date_from:
                error_params.append(date_from)
                error_where += " AND created_at >= %s::timestamptz"
            if date_to:
                error_params.append(date_to)
                error_where += " AND created_at <= %s::timestamptz"

            cur.execute(
                f"SELECT id, sender, destination, error, created_at"
                f" FROM comm_messages{error_where}"
                f" ORDER BY created_at DESC LIMIT 10",
                error_params,
            )
            stats.latest_errors = [
                {
                    "id": r["id"],
                    "sender": r["sender"],
                    "destination": r["destination"],
                    "error": (r["error"] or "")[:120],
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                }
                for r in cur.fetchall()
            ]

            stats.date_from = date_from
            stats.date_to = date_to
            conn.close()

        except Exception as e:
            logger.warning("Error fetching comm stats: %s", e)
            stats.db_available = False

        return stats

    # ── Full snapshot ────────────────────────────────────────────────────────

    async def collect_all(self) -> DashboardSnapshot:
        """Collect all metrics concurrently."""
        bots_task = self.collect_bots()
        containers_task = self.collect_containers()
        mcps_task = self.collect_mcps()
        comm_task = self.collect_comm_stats()

        bots, containers, mcps, comm_stats = await asyncio.gather(
            bots_task, containers_task, mcps_task, comm_task
        )

        # Aggregate all sessions across bots
        all_sessions: list[dict[str, Any]] = []
        for bot in bots:
            for s in bot.sessions:
                entry = dict(s) if isinstance(s, dict) else {"session_id": str(s)}
                entry.setdefault("bot", bot.name)
                all_sessions.append(entry)

        return DashboardSnapshot(
            bots=bots,
            containers=containers,
            mcps=mcps,
            comm_stats=comm_stats,
            all_sessions=all_sessions,
            collected_at=time.time(),
        )
