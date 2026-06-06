"""server.py — HTTP server for LINA Dashboard.

Endpoints:
  GET /          → HTML dashboard (auto-refresh 30s)
  GET /api/json  → JSON snapshot (for external consumers)
  GET /health    → health check
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from .collectors import DashboardCollector, DashboardSnapshot, BotStatus, ContainerStatus, MCPStatus, CommStats

logger = logging.getLogger(__name__)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>LINA Dashboard — Monitoreo</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;background:#0d1117;color:#e6edf3;padding:16px}}
h1{{color:#58a6ff;font-size:1.3em;display:flex;align-items:center;gap:6px}}
.subtitle{{color:#8b949e;font-size:0.82em;margin-bottom:12px}}
.alerts{{background:#3d1e1e;border:1px solid #f8514966;border-radius:6px;padding:6px 10px;margin-bottom:10px;font-size:0.82em;color:#f85149;display:none}}
.alerts.active{{display:block}}

/* ── 4-column per-bot layout ── */
.bot-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}}
.bot-col{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px}}
.bot-col h3{{color:#f78166;font-size:0.85em;margin-bottom:2px;display:flex;align-items:center;gap:4px}}
.bot-col .username{{color:#8b949e;font-size:0.72em;margin-bottom:4px}}
.bot-col .row{{display:flex;justify-content:space-between;font-size:0.78em;padding:3px 0;border-bottom:1px solid #21262d}}
.bot-col .row:last-child{{border-bottom:none}}
.bot-col .label{{color:#8b949e}}
.bot-col .val{{font-weight:600}}
.bot-col .dot{{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px}}
.bot-col .dot.online{{background:#3fb950;box-shadow:0 0 5px #3fb95066}}
.bot-col .dot.offline{{background:#f85149;box-shadow:0 0 5px #f8514966}}
.bot-col .count-s{{font-size:0.85em;color:#58a6ff;font-weight:bold}}
.bot-col.offline h3{{color:#f85149}}
.col-sessions{{max-height:80px;overflow-y:auto;margin-top:4px;font-size:0.72em}}
.col-sessions .si{{display:flex;gap:4px;padding:1px 0}}
.col-sessions .si .sid{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8b949e}}
.col-sessions .si .cnt{{color:#484f58;flex-shrink:0}}
.section-title{{font-size:0.82em;font-weight:600;color:#8b949e;text-transform:uppercase;letter-spacing:.04em;margin:10px 0 4px}}
table{{width:100%;border-collapse:collapse;font-size:0.78em}}
th,td{{text-align:left;padding:4px 6px;border-bottom:1px solid #21262d}}
th{{color:#8b949e;font-weight:600;font-size:0.72em;text-transform:uppercase;letter-spacing:.04em}}
.status-running{{color:#3fb950}}
.status-exited{{color:#f85149}}
.status-paused{{color:#d29922}}
.mcp-online{{color:#3fb950}}
.mcp-offline{{color:#f85149}}
.bot-badge{{display:inline-block;background:#1f6feb22;color:#58a6ff;padding:0 4px;border-radius:3px;font-size:0.75em}}
.status-bar{{display:flex;gap:10px;align-items:center;margin-top:12px;padding-top:8px;border-top:1px solid #21262d;font-size:0.78em;color:#8b949e;flex-wrap:wrap}}
.status-bar .live{{color:#3fb950;display:flex;align-items:center;gap:4px}}
.status-bar .live::before{{content:"";width:5px;height:5px;border-radius:50%;background:#3fb950;animation:pulse 2s infinite}}
@keyframes pulse{{0%{{opacity:1}}50%{{opacity:0.4}}100%{{opacity:1}}}}
.refresh-btn{{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:2px 8px;border-radius:4px;cursor:pointer;font-size:0.78em}}

/* ── Comm Stats ── */
.comm-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:6px 0}}
.comm-card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px;text-align:center}}
.comm-card .num{{font-size:1.4em;font-weight:700}}
.comm-card .label{{font-size:0.7em;color:#8b949e;text-transform:uppercase;letter-spacing:.04em}}
.comm-card.c-delivered .num{{color:#3fb950}}
.comm-card.c-failed .num{{color:#f85149}}
.comm-card.c-ignored .num{{color:#d29922}}
.comm-card.c-sent .num{{color:#58a6ff}}
.comm-card.c-retrying .num{{color:#db6d28}}
.comm-total{{font-size:0.82em;color:#8b949e;margin:4px 0 8px}}
.comm-bar{{height:6px;background:#21262d;border-radius:3px;margin:6px 0 10px;overflow:hidden}}
.comm-bar-inner{{height:100%;border-radius:3px;transition:width .5s}}
.comm-bar-inner.delivered{{background:#3fb950}}
.comm-bar-inner.failed{{background:#f85149}}
.comm-bar-inner.ignored{{background:#d29922}}
.comm-bar-inner.sent{{background:#58a6ff}}
.filter-row{{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:6px 0}}
.filter-row label{{font-size:0.75em;color:#8b949e}}
.filter-row input[type=date],.filter-row input[type=time]{{background:#0d1117;border:1px solid #30363d;color:#e6edf3;padding:2px 6px;border-radius:4px;font-size:0.78em}}
.filter-btn{{background:#1f6feb;border:none;color:#fff;padding:3px 12px;border-radius:4px;cursor:pointer;font-size:0.78em}}
.filter-btn:hover{{background:#388bfd}}
.comm-err{{font-size:0.72em;margin:4px 0}}
.comm-err .row{{display:flex;gap:6px;padding:2px 0;border-bottom:1px solid #21262d}}
.comm-err .ts{{color:#484f58;flex-shrink:0}}
.comm-err .txt{{color:#f85149}}
.comm-err .dest{{color:#8b949e}}
.comm-db-fallback{{color:#d29922;font-size:0.78em;padding:8px}}
@media(max-width:1024px){{.bot-grid{{grid-template-columns:repeat(2,1fr)}}}}
@media(max-width:640px){{.bot-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<h1>🔭 LINA Dashboard</h1>
<div class="subtitle">Monitoreo en vivo · bots · sesiones · contenedores · MCPs</div>
<div id="alerts" class="alerts"></div>

<!-- ── 4-Column Per-Bot Layout ── -->
<div class="bot-grid" id="botGrid">
  <div style="color:#484f58;font-size:0.85em;grid-column:1/-1">Cargando...</div>
</div>

<!-- ── Contenedores ── -->
<div class="section-title">📦 Contenedores</div>
<table>
<thead><tr><th>Nombre</th><th>Estado</th><th>CPU</th><th>Memoria</th><th>Puertos</th></tr></thead>
<tbody id="containerBody">
  <tr><td colspan="5" style="color:#484f58">Cargando...</td></tr>
</tbody>
</table>

<!-- ── MCPs ── -->
<div class="section-title" style="margin-top:12px">🔌 MCPs — Acceso por Bot</div>
<table>
<thead><tr><th>Nombre</th><th>Bot</th><th>Estado</th><th>Latencia</th></tr></thead>
<tbody id="mcpBody">
  <tr><td colspan="4" style="color:#484f58">Cargando...</td></tr>
</tbody>
</table>

<!-- ── Comm Bridge Stats ── -->
<div class="section-title" style="margin-top:12px">🔌 Estadísticas de Mensajería (Comm Bridge)</div>
<div id="commSection">
  <div style="color:#484f58;font-size:0.82em">Cargando...</div>
</div>

<div class="status-bar">
  <span class="live">Actualizando</span>
  <span id="updateTime">—</span>
  <button class="refresh-btn" onclick="fetchData()">⟳ Recargar</button>
  <span>⏱ 30s</span>
</div>

<script>
let data=null;
function esc(s){{if(s==null)return"";return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}}
function ft(ts){{if(!ts)return"—";var d=new Date(ts*1000);return d.toLocaleTimeString([],{{hour:"2-digit",minute:"2-digit",second:"2-digit"}});}}
function ago(ts){{if(!ts)return"";var sec=Math.floor(Date.now()/1000-ts);if(sec<5)return"ahora";if(sec<60)return sec+"s";if(sec<3600)return Math.floor(sec/60)+"m";return Math.floor(sec/3600)+"h";}}
function sid(s){{return s&&s.length>20?s.slice(0,20)+"…":s||"?";}}

function renderBotGrid(bots,sessions){{
  var el=document.getElementById("botGrid");
  if(!bots||bots.length===0){{el.innerHTML='<div style="color:#484f58;grid-column:1/-1">Sin datos</div>';return;}}
  var h="";
  for(var b of bots){{
    var on=b.online;
    var bs=(sessions||[]).filter(function(s){{return s.bot===b.name;}});
    h+='<div class="bot-col'+(on?"":" offline")+'">';
    h+='<h3><span class="dot '+(on?"online":"offline")+'"></span>'+esc(b.name)+'</h3>';
    h+='<div class="username">@'+(b.username||"…")+'</div>';
    h+='<div class="row"><span class="label">Estado</span><span class="val">'+(on?"🟢 Online":"🔴 Offline")+(b.error?" ("+esc(b.error)+")":"")+'</span></div>';
    h+='<div class="row"><span class="label">Sesiones</span><span class="val count-s">'+b.session_count+'</span></div>';
    if(b.last_event)h+='<div class="row"><span class="label">Última</span><span class="val">'+ago(b.last_event)+'</span></div>';
    if(bs.length>0){{
      h+='<div class="col-sessions">';
      for(var j=0;j<Math.min(bs.length,8);j++){{
        var s=bs[j];
        h+='<div class="si"><span class="sid">'+sid(s.session_id)+'</span><span class="cnt">'+(s.event_count||s.events||0)+'</span></div>';
      }}
      h+='</div>';
    }}
    h+='</div>';
  }}
  el.innerHTML=h;
}}

function renderContainers(containers){{
  var el=document.getElementById("containerBody");
  if(!containers||containers.length===0){{el.innerHTML='<tr><td colspan="5" style="color:#484f58">Sin contenedores</td></tr>';return;}}
  var alerts=[],h="";
  for(var c of containers){{
    var isDown=!c.healthy;
    if(isDown)alerts.push(c.name);
    var sc="status-"+((c.state||"unknown").toLowerCase());
    h+='<tr><td>'+esc(c.name)+'</td><td class="'+sc+'"><b>'+esc(c.state||"?")+'</b></td><td>—</td><td>—</td><td>'+esc(c.ports||"—")+'</td></tr>';
  }}
  el.innerHTML=h;
  var ae=document.getElementById("alerts");
  if(alerts.length>0){{ae.innerHTML="⚠️ Contenedores caídos: <b>"+alerts.join(", ")+"</b>";ae.className="alerts active";}}
  else{{ae.className="alerts";}}
}}

function renderMcps(mcps){{
  var el=document.getElementById("mcpBody");
  if(!mcps||mcps.length===0){{el.innerHTML='<tr><td colspan="4" style="color:#484f58">Sin MCPs</td></tr>';return;}}
  var h="";
  for(var m of mcps){{
    var ok=m.online;
    var sc=ok?"mcp-online":"mcp-offline";
    var used=m.used_by||[];
    var bots=used.join(", ");
    h+='<tr><td>'+esc(m.name.replace("lina-",""))+'</td>';
    h+='<td>'+(bots?'<span class="bot-badge">'+esc(bots)+'</span>':"—")+'</td>';
    h+='<td class="'+sc+'">'+(ok?"✅":"❌ "+(m.error||"down"))+'</td>';
    h+='<td>'+(m.response_time_ms?m.response_time_ms+"ms":"—")+'</td></tr>';
  }}
  el.innerHTML=h;
}}

/* ── Comm Stats rendering ── */

function renderCommStats(s){{
  var el=document.getElementById("commSection");
  if(!s||!s.db_available){{
    el.innerHTML='<div class="comm-db-fallback">⚠️ BD no disponible (PostgreSQL no responde)</div>';
    return;
  }}
  var max=Math.max(s.delivered,s.failed,s.ignored,s.sent,s.retrying,1);
  function pct(v){{return Math.round(v/max*100);}}
  var h='<div class="filter-row">';
  h+='<label>📅 Desde</label><input type="date" id="fdFrom">';
  h+='<label>Hasta</label><input type="date" id="fdTo">';
  h+='<button class="filter-btn" onclick="fetchCommStats()">Filtrar</button>';
  h+='<button class="refresh-btn" onclick="fetchCommStats()">⟳</button>';
  if(s.date_from)h+='<span style="color:#8b949e;font-size:0.78em">Filtro: '+esc(s.date_from)+' → '+(s.date_to||'ahora')+'</span>';
  h+='</div>';
  h+='<div class="comm-grid">';
  h+='<div class="comm-card c-delivered"><div class="num">'+s.delivered+'</div><div class="label">✅ Entregados</div></div>';
  h+='<div class="comm-card c-failed"><div class="num">'+s.failed+'</div><div class="label">❌ Failed</div></div>';
  h+='<div class="comm-card c-ignored"><div class="num">'+s.ignored+'</div><div class="label">⚠️ Ignorados</div></div>';
  h+='<div class="comm-card c-sent"><div class="num">'+s.sent+'</div><div class="label">📤 Enviados</div></div>';
  if(s.retrying>0)h+='<div class="comm-card c-retrying"><div class="num">'+s.retrying+'</div><div class="label">🔄 Reintentando</div></div>';
  h+='</div>';
  h+='<div class="comm-total">Total: <b>'+s.total+'</b> mensajes';
  if(s.by_sender&&Object.keys(s.by_sender).length>0){{
    var parts=[];
    for(var k in s.by_sender)parts.push('<b>'+esc(k)+'</b>: '+s.by_sender[k]);
    h+=' · Por sender: '+parts.join(' · ');
  }}
  h+='</div>';
  // Latest errors
  if(s.latest_errors&&s.latest_errors.length>0){{
    h+='<div class="comm-err"><b style="color:#f85149">Últimos errores:</b>';
    for(var e of s.latest_errors){{
      h+='<div class="row"><span class="ts">'+(e.created_at?e.created_at.slice(11,19):"")+'</span><span class="dest">['+esc(e.destination)+']</span><span class="txt">'+esc(e.error)+'</span></div>';
    }}
    h+='</div>';
  }}
  el.innerHTML=h;
  // Set date inputs from current values
  if(s.date_from)document.getElementById("fdFrom").value=s.date_from;
  if(s.date_to)document.getElementById("fdTo").value=s.date_to;
}}

async function fetchCommStats(){{
  var from=document.getElementById("fdFrom")?document.getElementById("fdFrom").value:"";
  var to=document.getElementById("fdTo")?document.getElementById("fdTo").value:"";
  var url="/api/comm-stats";
  var q=[];
  if(from)q.push("from="+encodeURIComponent(from));
  if(to)q.push("to="+encodeURIComponent(to));
  if(q.length)url+="?"+q.join("&");
  try{{
    var r=await fetch(url);
    if(!r.ok)throw new Error("HTTP "+r.status);
    var s=await r.json();
    renderCommStats(s);
  }}catch(e){{
    document.getElementById("commSection").innerHTML='<div class="comm-db-fallback">⚠️ Error: '+esc(e.message)+'</div>';
  }}
}}

async function fetchData(){{
  try{{
    var r=await fetch("/api/json");
    if(!r.ok)throw new Error("HTTP "+r.status);
    data=await r.json();
    document.getElementById("updateTime").textContent=ft(data.collected_at);
    renderBotGrid(data.bots,data.sessions);
    renderContainers(data.containers);
    renderMcps(data.mcps);
    renderCommStats(data.comm_stats);
  }}catch(e){{document.getElementById("updateTime").textContent="Error: "+e.message;}}
}}

fetchData();
setInterval(fetchData,30000);
fetchCommStats();
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
            elif path.startswith("/api/comm-stats"):
                # Parse query params: ?from=2026-06-05&to=2026-06-06
                from_str = None
                to_str = None
                if "?" in path:
                    qs = path.split("?", 1)[1]
                    for part in qs.split("&"):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            if k == "from":
                                from_str = v
                            elif k == "to":
                                to_str = v
                comm_stats = await self.collector.collect_comm_stats(
                    date_from=from_str, date_to=to_str,
                )
                body = json.dumps(comm_stats.to_dict(), default=str).encode()
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
                html = HTML_TEMPLATE
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
        await self._refresh_snapshot()

        async def _refresh_loop() -> None:
            while True:
                await asyncio.sleep(30)
                await self._refresh_snapshot()

        loop.create_task(_refresh_loop(), name="dashboard-refresh")

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
