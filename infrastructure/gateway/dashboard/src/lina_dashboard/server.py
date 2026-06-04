"""
server.py — HTTP server for LINA Dashboard.

Endpoints:
  GET /          → HTML dashboard (auto-refresh 30s)
  GET /api/json  → JSON snapshot (for external consumers)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from .collectors import DashboardCollector, DashboardSnapshot

logger = logging.getLogger(__name__)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>LINA Dashboard — Monitoreo</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;background:#0d1117;color:#e6edf3;padding:20px;min-height:100vh}}
h1{{color:#58a6ff;font-size:1.4em;margin-bottom:4px;display:flex;align-items:center;gap:8px}}
.subtitle{{color:#8b949e;font-size:0.85em;margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(480px,1fr));gap:16px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;position:relative}}
.card h2{{color:#f78166;font-size:1em;margin-bottom:10px;display:flex;align-items:center;gap:6px}}
.card h2 .count{{background:#30363d33;color:#8b949e;padding:0 6px;border-radius:4px;font-size:0.8em;margin-left:auto}}

/* Bot cards */
.bot-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}}
.bot-card{{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px;text-align:center}}
.bot-card .dot{{width:10px;height:10px;border-radius:50%;display:inline-block;margin-bottom:4px}}
.bot-card .dot.online{{background:#3fb950;box-shadow:0 0 6px #3fb95066}}
.bot-card .dot.offline{{background:#f85149;box-shadow:0 0 6px #f8514966}}
.bot-card .bot-name{{font-weight:600;font-size:0.95em}}
.bot-card .bot-status{{font-size:0.8em;color:#8b949e;margin:2px 0}}
.bot-card .bot-metric{{font-size:1.2em;font-weight:bold;color:#58a6ff}}
.bot-card .bot-label{{font-size:0.7em;color:#8b949e}}
.bot-card.down .bot-metric{{color:#f85149}}

/* Table */
table{{width:100%;border-collapse:collapse;font-size:0.82em;margin-top:4px}}
th,td{{text-align:left;padding:5px 6px;border-bottom:1px solid #21262d}}
th{{color:#8b949e;font-weight:600;font-size:0.78em;text-transform:uppercase;letter-spacing:.04em}}
.status-running,.status-healthy{{color:#3fb950}}
.status-exited,.status-offline,.status-down{{color:#f85149}}
.status-paused,.status-created{{color:#d29922}}
.status-warning{{color:#d29922}}

/* Container row coloring */
.container-row{{}}
.container-row.down td{{color:#f85149!important}}
.container-row td:first-child{{font-weight:500}}

/* MCP grid */
.mcp-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(100px,1fr));gap:6px}}
.mcp-item{{background:#0d1117;border:1px solid #30363d;border-radius:5px;padding:6px;text-align:center;font-size:0.78em}}
.mcp-item .dot{{width:8px;height:8px;border-radius:50%;display:inline-block;margin-bottom:2px}}
.mcp-item .mcp-name{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.mcp-item .mcp-time{{font-size:0.75em;color:#484f58}}
.mcp-item.online .dot{{background:#3fb950;box-shadow:0 0 4px #3fb95066}}
.mcp-item.offline .dot{{background:#f85149;box-shadow:0 0 4px #f8514966}}

/* Session list - compact */
.session-list{{max-height:200px;overflow-y:auto;font-size:0.8em}}
.session-item{{display:flex;align-items:center;gap:4px;padding:3px 6px;border-radius:4px;margin:1px 0}}
.session-item:hover{{background:#1c2128}}
.session-item .sid{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}}
.session-item .bot-badge{{background:#1f6feb22;color:#58a6ff;padding:0 5px;border-radius:3px;font-size:0.75em}}
.session-item .count{{color:#8b949e;font-size:0.75em}}

/* Status bar */
.status-bar{{display:flex;gap:12px;align-items:center;margin-top:14px;padding-top:10px;border-top:1px solid #21262d;font-size:0.8em;color:#8b949e;flex-wrap:wrap}}
.status-bar .live{{color:#3fb950;display:flex;align-items:center;gap:4px}}
.status-bar .live::before{{content:"";width:6px;height:6px;border-radius:50%;background:#3fb950;animation:pulse 2s infinite}}
@keyframes pulse{{0%{{opacity:1}}50%{{opacity:0.4}}100%{{opacity:1}}}}
.refresh-btn{{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:3px 10px;border-radius:4px;cursor:pointer;font-size:0.82em}}
.refresh-btn:hover{{background:#30363d}}

/* Alert banner */
.alert-banner{{background:#3d1e1e;border:1px solid #f8514966;border-radius:6px;padding:8px 12px;margin-bottom:12px;font-size:0.85em;color:#f85149;display:none}}
.alert-banner.active{{display:block}}

/* Responsive */
@media(max-width:768px){{.grid{{grid-template-columns:1fr}}.bot-row{{grid-template-columns:1fr 1fr}}}}
</style>
</head>
<body>
<h1>🔭 LINA Dashboard</h1>
<div class="subtitle">Monitoreo unificado · bots · sesiones · contenedores · MCPs</div>

<div id="alerts" class="alert-banner"></div>

<div class="grid">

<!-- Block 1: Bots -->
<div class="card">
<h2>🤖 Bots <span class="count" id="botCount">0</span></h2>
<div class="bot-row" id="botRow">
  <div style="color:#484f58;font-size:0.85em;grid-column:1/-1">Cargando...</div>
</div>
</div>

<!-- Block 2: Sesiones activas -->
<div class="card" style="grid-column:span 1">
<h2>💬 Sesiones activas <span class="count" id="sessionCount">0</span></h2>
<div class="session-list" id="sessionList">
  <div style="color:#484f58;font-size:0.85em">Cargando...</div>
</div>
</div>

<!-- Block 3: Contenedores -->
<div class="card">
<h2>📦 Contenedores <span class="count" id="containerCount">0</span></h2>
<table>
<thead><tr><th>Nombre</th><th>Estado</th><th>Puertos</th><th>Imagen</th></tr></thead>
<tbody id="containerBody">
  <tr><td colspan="4" style="color:#484f58">Cargando...</td></tr>
</tbody>
</table>
</div>

<!-- Block 4: MCPs -->
<div class="card">
<h2>🔌 MCPs <span class="count" id="mcpCount">0</span></h2>
<div class="mcp-grid" id="mcpGrid">
  <div style="color:#484f58;font-size:0.85em;grid-column:1/-1">Cargando...</div>
</div>
</div>

</div>

<div class="status-bar">
  <span class="live">Actualizando</span>
  <span id="updateTime">—</span>
  <button class="refresh-btn" onclick="fetchData()">⟳ Recargar</button>
  <span>⏱ 30s auto</span>
</div>

<script>
let data = null;

function esc(s) {
  if (s == null) return "";
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function ft(ts) {
  if (!ts) return "—";
  var d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  return d.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"});
}

function ago(ts) {
  if (!ts) return "";
  var now = Date.now() / 1000;
  var then = typeof ts === "number" ? ts : new Date(ts).getTime() / 1000;
  var sec = Math.floor(now - then);
  if (sec < 5) return "ahora";
  if (sec < 60) return sec + "s";
  if (sec < 3600) return Math.floor(sec/60) + "m";
  return Math.floor(sec/3600) + "h";
}

// ── Bots ──
function renderBots(bots) {
  var el = document.getElementById("botRow");
  if (!bots || bots.length === 0) {
    el.innerHTML = '<div style="color:#484f58;font-size:0.85em;grid-column:1/-1">Sin datos</div>';
    return;
  }
  document.getElementById("botCount").textContent = bots.length;
  var h = "";
  for (var b of bots) {
    var online = b.online;
    h += '<div class="bot-card' + (online ? "" : " down") + '">';
    h += '<div class="dot ' + b.dot_class + '"></div>';
    h += '<div class="bot-name">' + esc(b.name) + '</div>';
    h += '<div class="bot-status">' + (online ? "🟢 Online" : "🔴 Offline") + (b.error ? ": " + esc(b.error) : "") + '</div>';
    h += '<div class="bot-metric">' + b.session_count + '</div>';
    h += '<div class="bot-label">sesiones</div>';
    if (b.last_event) h += '<div style="font-size:0.7em;color:#484f58;margin-top:2px">' + ago(b.last_event) + '</div>';
    h += '</div>';
  }
  el.innerHTML = h;
}

// ── Sessions ──
function renderSessions(sessions) {
  var el = document.getElementById("sessionList");
  if (!sessions || sessions.length === 0) {
    el.innerHTML = '<div style="color:#484f58;font-size:0.85em">Sin sesiones activas</div>';
    document.getElementById("sessionCount").textContent = "0";
    return;
  }
  document.getElementById("sessionCount").textContent = sessions.length;
  var h = "";
  var limit = Math.min(sessions.length, 50);
  for (var i = 0; i < limit; i++) {
    var s = sessions[i];
    var sid = s.session_id || "?";
    var bot = s.bot || "?";
    var ev = s.event_count || s.events || 0;
    var short = sid.length > 28 ? sid.slice(0,28) + "…" : sid;
    h += '<div class="session-item">';
    h += '<span class="sid" title="' + esc(sid) + '">' + esc(short) + '</span>';
    h += '<span class="bot-badge">' + esc(bot) + '</span>';
    h += '<span class="count">' + ev + '</span>';
    h += '</div>';
  }
  if (sessions.length > 50) {
    h += '<div style="color:#484f58;font-size:0.75em;padding:4px 6px">+ ' + (sessions.length - 50) + ' más</div>';
  }
  el.innerHTML = h;
}

// ── Containers ──
function renderContainers(containers) {
  var el = document.getElementById("containerBody");
  if (!containers || containers.length === 0) {
    el.innerHTML = '<tr><td colspan="4" style="color:#484f58">Sin contenedores LINA</td></tr>';
    document.getElementById("containerCount").textContent = "0";
    return;
  }
  document.getElementById("containerCount").textContent = containers.length;
  var alerts = [];
  var h = "";
  for (var c of containers) {
    var isDown = !c.healthy;
    var stateClass = "status-" + (c.state || "unknown").toLowerCase();
    if (isDown) {
      alerts.push(c.name);
    }
    h += '<tr class="container-row' + (isDown ? " down" : "") + '">';
    h += '<td>' + esc(c.name) + '</td>';
    h += '<td class="' + stateClass + '"><b>' + esc(c.state || "?") + '</b></td>';
    h += '<td>' + esc(c.ports || "—") + '</td>';
    h += '<td>' + esc(c.image || "—") + '</td>';
    h += '</tr>';
  }
  el.innerHTML = h;

  // Show alerts
  var alertEl = document.getElementById("alerts");
  if (alerts.length > 0) {
    alertEl.innerHTML = "⚠️ Contenedores caídos: <b>" + alerts.join(", ") + "</b>";
    alertEl.className = "alert-banner active";
  } else {
    alertEl.className = "alert-banner";
  }
}

// ── MCPs ──
function renderMcps(mcps) {
  var el = document.getElementById("mcpGrid");
  if (!mcps || mcps.length === 0) {
    el.innerHTML = '<div style="color:#484f58;font-size:0.85em;grid-column:1/-1">Sin MCPs registrados</div>';
    document.getElementById("mcpCount").textContent = "0";
    return;
  }
  document.getElementById("mcpCount").textContent = mcps.length;
  var h = "";
  for (var m of mcps) {
    var online = m.online;
    h += '<div class="mcp-item ' + (online ? "online" : "offline") + '" title="' + esc(m.name) + ': ' + (online ? "OK" : esc(m.error || "down")) + '">';
    h += '<span class="dot"></span> ';
    h += '<span class="mcp-name">' + esc(m.name.replace("lina-","")) + '</span>';
    h += '<span class="mcp-time">' + (m.response_time_ms ? m.response_time_ms + "ms" : esc(m.error || "?")) + '</span>';
    h += '</div>';
  }
  el.innerHTML = h;
}

// ── Fetch ──
async function fetchData() {
  try {
    var resp = await fetch("/api/json");
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    data = await resp.json();
    document.getElementById("updateTime").textContent = ft(data.collected_at);
    renderBots(data.bots);
    renderSessions(data.sessions);
    renderContainers(data.containers);
    renderMcps(data.mcps);
  } catch (e) {
    document.getElementById("updateTime").textContent = "Error: " + e.message;
  }
}

// ── Auto-refresh ──
fetchData();
setInterval(fetchData, 30000);
</script>
</body>
</html>
"""


class DashboardServer:
    """Async HTTP server serving the LINA Dashboard."""

    def __init__(
        self,
        collector: DashboardCollector,
        host: str = "0.0.0.0",
        port: int = 8090,
    ):
        self.collector = collector
        self.host = host
        self.port = port
        self._server: asyncio.Server | None = None
        self._snapshot: DashboardSnapshot | None = None
        self._snapshot_lock = asyncio.Lock()

    async def _refresh_snapshot(self) -> None:
        """Collect fresh data from all sources."""
        try:
            snapshot = await self.collector.collect_all()
            async with self._snapshot_lock:
                self._snapshot = snapshot
        except Exception as e:
            logger.error("Error refreshing snapshot: %s", e)

    async def _get_snapshot(self) -> DashboardSnapshot:
        async with self._snapshot_lock:
            if self._snapshot is None:
                return DashboardSnapshot()
            return self._snapshot

    async def _handle_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                raw += chunk
            request_line = raw.split(b"\r\n")[0].decode("utf-8", errors="replace")
            method, path, _ = request_line.split(" ", 2) if " " in request_line else ("GET", "/", "")

            if path == "/api/json":
                snapshot = await self._get_snapshot()
                body = json.dumps(snapshot.to_dict(), default=str).encode()
                headers = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Access-Control-Allow-Origin: *\r\n"
                    b"Cache-Control: no-cache\r\n"
                )
                resp = headers + b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                writer.write(resp)
                await writer.drain()
            elif path == "/":
                snapshot = await self._get_snapshot()
                html = HTML_TEMPLATE  # Template formatted via JS
                body = html.encode()
                headers = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/html; charset=utf-8\r\n"
                    b"Cache-Control: no-cache\r\n"
                )
                resp = headers + b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                writer.write(resp)
                await writer.drain()
            elif path == "/health":
                body = b'{"status":"ok"}'
                resp = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                )
                writer.write(resp)
                await writer.drain()
            else:
                body = b"Not Found"
                resp = (
                    b"HTTP/1.1 404 Not Found\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                )
                writer.write(resp)
                await writer.drain()
        except Exception as e:
            logger.error("Error handling request: %s", e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def start(self) -> None:
        """Start the HTTP server and snapshot refresh loop."""
        loop = asyncio.get_running_loop()

        # Immediate first snapshot
        await self._refresh_snapshot()

        # Background refresh every 30s
        async def _refresh_loop() -> None:
            while True:
                await asyncio.sleep(30)
                await self._refresh_snapshot()

        loop.create_task(_refresh_loop(), name="dashboard-refresh")

        # Start HTTP server
        self._server = await asyncio.start_server(
            self._handle_request,
            host=self.host,
            port=self.port,
        )
        logger.info(
            "Dashboard server listening on http://%s:%s",
            self.host,
            self.port,
        )

        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        await self.collector.close()
