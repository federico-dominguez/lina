"""
collectors.py — Data collection layer for LINA Dashboard.

Polling targets:
1. Bot observe ports (9091=Goose, 9092=CLINE, 9093=LINA)
2. Docker socket proxy (container list + health)
3. MCP nginx gateway (health check per MCP endpoint)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────────

BOT_NAMES = {
    9091: "Goose",
    9092: "CLINE",
    9093: "LINA",
}

MCP_REGISTRY: list[dict[str, Any]] = [
    {"name": "lina-secrets", "port": 8101, "url": "/"},
    {"name": "lina-fs-safe", "port": 8102, "url": "/"},
    {"name": "lina-shell-policy", "port": 8103, "url": "/"},
    {"name": "lina-systemd-user", "port": 8104, "url": "/"},
    {"name": "lina-moodle", "port": 8105, "url": "/"},
    {"name": "lina-db", "port": 8106, "url": "/"},
    {"name": "lina-github", "port": 8107, "url": "/"},
    {"name": "lina-gitlab", "port": 8108, "url": "/"},
    {"name": "lina-gcalendar", "port": 8109, "url": "/"},
    {"name": "lina-gns3", "port": 8110, "url": "/"},
    {"name": "lina-orchestrator", "port": 8111, "url": "/"},
]


@dataclass
class BotStatus:
    name: str
    port: int
    online: bool
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
    error: str | None = None
    response_time_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "port": self.port,
            "online": self.online,
            "error": self.error,
            "response_time_ms": self.response_time_ms,
        }


@dataclass
class DashboardSnapshot:
    bots: list[BotStatus] = field(default_factory=list)
    containers: list[ContainerStatus] = field(default_factory=list)
    mcps: list[MCPStatus] = field(default_factory=list)
    all_sessions: list[dict[str, Any]] = field(default_factory=list)
    collected_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "bots": [b.to_dict() for b in self.bots],
            "containers": [c.to_dict() for c in self.containers],
            "mcps": [m.to_dict() for m in self.mcps],
            "sessions": self.all_sessions,
            "collected_at": self.collected_at,
        }


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
        timeout: float = 5.0,
    ):
        self.bot_ports = bot_ports or [9091, 9092, 9093]
        self.docker_url = docker_url
        self.mcp_host = mcp_host
        self.timeout = timeout
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
        """Poll all bot observe ports for sessions + status."""
        client = await self._get_client()
        results: list[BotStatus] = []

        for port in self.bot_ports:
            name = BOT_NAMES.get(port, f"Bot:{port}")
            bot = BotStatus(name=name, port=port, online=False)

            try:
                # Get sessions list
                resp = await client.get(f"http://localhost:{port}/api/sessions")
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
                resp2 = await client.get(f"http://localhost:{port}/api/status")
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
            mcp_status = MCPStatus(name=mcp["name"], port=mcp["port"], online=False)

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

    # ── Full snapshot ────────────────────────────────────────────────────────

    async def collect_all(self) -> DashboardSnapshot:
        """Collect all metrics concurrently."""
        bots_task = self.collect_bots()
        containers_task = self.collect_containers()
        mcps_task = self.collect_mcps()

        bots, containers, mcps = await asyncio.gather(
            bots_task, containers_task, mcps_task
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
            all_sessions=all_sessions,
            collected_at=time.time(),
        )
