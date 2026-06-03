"""
observe.py — Agent Observability Module for lina-gateway.

Arquitectura:
  bot.py → push_event() → ObserveServer → WS broadcast + PostgreSQL persist + Dashboard HTTP

Sin necesidad de proxy HTTP — el gateway ya tiene los eventos SSE en vivo.
Solo se agregan ~3 líneas en bot.py para pushear los eventos acá.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import struct
import time
from typing import Any

try:
    import asyncpg
except ImportError:
    asyncpg = None

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# EventStore — persistencia en PostgreSQL
# ═══════════════════════════════════════════════════════════════════════════


class EventStore:
    """Persiste eventos intermedios de sesiones goosed en PostgreSQL."""

    def __init__(self, db_url: str | None = None):
        self._pool: asyncpg.Pool | None = None
        self._db_url = db_url

    async def connect(self):
        if not self._db_url or not asyncpg:
            logger.info("Observe: sin DB (observabilidad desactivada)")
            return
        try:
            self._pool = await asyncpg.create_pool(self._db_url, min_size=1, max_size=3, timeout=5)
            logger.info("Observe: EventStore conectado a PostgreSQL")
        except Exception as e:
            logger.warning("Observe: no se pudo conectar a PG: %s", e)

    async def close(self):
        if self._pool:
            await self._pool.close()

    async def save_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict,
        parent_id: int | None = None,
    ) -> int | None:
        if not self._pool:
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO session_events (session_id, event_type, payload, parent_event_id)
                       VALUES ($1, $2, $3::jsonb, $4) RETURNING id""",
                    session_id,
                    event_type,
                    json.dumps(payload),
                    parent_id,
                )
                return row["id"] if row else None
        except Exception as e:
            logger.debug("Observe: save_event error: %s", e)
            return None

    async def get_recent_sessions(self, limit: int = 20) -> list[dict]:
        if not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT session_id,
                              count(*) as event_count,
                              min(created_at) as first_event,
                              max(created_at) as last_event
                       FROM session_events
                       GROUP BY session_id
                       ORDER BY max(created_at) DESC
                       LIMIT $1""",
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.debug("Observe: get_recent_sessions error: %s", e)
            return []

    async def get_timeline(self, session_id: str, limit: int = 500) -> list[dict]:
        if not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, event_type, payload, parent_event_id, created_at
                       FROM session_events
                       WHERE session_id = $1
                       ORDER BY id ASC
                       LIMIT $2""",
                    session_id,
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.debug("Observe: get_timeline error: %s", e)
            return []

    async def get_stats(self, session_id: str) -> dict:
        if not self._pool:
            return {}
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT event_type, count(*) as cnt
                       FROM session_events
                       WHERE session_id = $1
                       GROUP BY event_type ORDER BY cnt DESC""",
                    session_id,
                )
                total = sum(r["cnt"] for r in rows)
                return {
                    "total_events": total,
                    "by_type": {r["event_type"]: r["cnt"] for r in rows},
                }
        except Exception as e:
            logger.debug("Observe: get_stats error: %s", e)
            return {}


# ═══════════════════════════════════════════════════════════════════════════
# WebSocket helpers (RFC 6455)
# ═══════════════════════════════════════════════════════════════════════════


def _ws_encode(text: str) -> bytes:
    data = text.encode("utf-8")
    frame = bytearray()
    frame.append(0x81)  # FIN + text opcode
    length = len(data)
    if length < 126:
        frame.append(length)
    elif length < 65536:
        frame.append(126)
        frame.extend(length.to_bytes(2, "big"))
    else:
        frame.append(127)
        frame.extend(length.to_bytes(8, "big"))
    frame.extend(data)
    return bytes(frame)


async def _ws_send(writer: asyncio.StreamWriter, text: str):
    try:
        writer.write(_ws_encode(text))
        await writer.drain()
    except Exception:
        pass


async def _ws_recv(reader: asyncio.StreamReader) -> str | None:
    try:
        first = await asyncio.wait_for(reader.read(2), timeout=600)
        if len(first) < 2:
            return None
        opcode = first[0] & 0x0F
        masked = bool(first[1] & 0x80)
        length = first[1] & 0x7F
        if length == 126:
            ext = await reader.read(2)
            length = struct.unpack("!H", ext)[0]
        elif length == 127:
            ext = await reader.read(8)
            length = struct.unpack("!Q", ext)[0]
        mask_key = await reader.read(4) if masked else b""
        data = await reader.read(length)
        if masked:
            data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
        if opcode == 0x8:  # Close
            return None
        if opcode == 0x9:  # Ping
            return None  # simplified
        return data.decode("utf-8")
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# ObserveServer — HTTP + WS + EventStore
# ═══════════════════════════════════════════════════════════════════════════


class ObserveServer:
    """Servidor HTTP + WS que muestra live stream y persistencia.

    Se integra en lina-gateway como background task.
    El bot llama push_event() y el server broadcast a WS + persiste a DB.
    """

    def __init__(
        self,
        port: int = 9090,
        db_url: str | None = None,
        goosed_url: str = "",
    ):
        self.port = port
        self.goosed_url = goosed_url
        self.store = EventStore(db_url)
        self._rooms: dict[str, set[asyncio.StreamWriter]] = {}
        self._current_text: dict[str, str] = {}
        self._event_count: dict[str, int] = {}
        self._last_tool_req_id: dict[str, int] = {}
        self._agent_sessions: dict[str, str] = {}

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        await self.store.connect()
        self._server = await asyncio.start_server(
            self._handle_connection,
            "0.0.0.0",
            self.port,
        )
        addr = self._server.sockets[0].getsockname()
        logger.info("Observe: http://localhost:%d  ws://localhost:%d", addr[1], addr[1])
        self._agent_poll_task = asyncio.create_task(self._poll_agent_events())
        self._goosed_db_task = asyncio.create_task(self._poll_goosed_db())
        asyncio.create_task(self._server.serve_forever(), name="observe-server")

    async def stop(self) -> None:
        if hasattr(self, "_server"):
            self._server.close()
        await self.store.close()
        logger.info("Observe: detenido")

    # ── Push event (called from bot.py) ────────────────────────────────

    def push_event(self, session_id: str, event_type: str, event: Any) -> None:
        """Envía un evento a WebSocket + DB.

        Llamado desde bot.py para cada evento SSE del stream.
        """
        ts = time.time()
        payload = {"type": event_type, "session": session_id, "ts": ts}

        # Extraer texto/datos según tipo de evento
        db_payload = {}
        if event_type == "user_message":
            text = str(event)
            payload["text"] = text
            db_payload = {"text": text, "ts": ts}
        elif event_type == "thinking":
            text = str(event)
            payload["text"] = text
            db_payload = {"text": text, "full": text}
        elif event_type == "text":
            text = str(event)
            prev = self._current_text.get(session_id, "")
            self._current_text[session_id] = prev + text
            payload["text"] = text
            db_payload = {"text": text}
        elif event_type == "tool_request":
            tool = getattr(event, "tool_name", str(event))
            args = getattr(event, "args_preview", "")
            payload["tool"] = tool
            payload["args"] = args
            db_payload = {"tool": tool, "args": args}
        elif event_type == "tool_response":
            text = getattr(event, "result_preview", str(event))[:300]
            payload["text"] = text
            db_payload = {"text": text}
        elif event_type == "finish":
            ts_obj = getattr(event, "token_state", None)
            tokens = ts_obj.total_tokens if ts_obj else 0
            reason = getattr(event, "finish_reason", "stop")
            payload["tokens"] = tokens
            payload["reason"] = reason
            db_payload = {"tokens": tokens, "reason": reason}
        elif event_type == "error":
            text = str(event)
            payload["text"] = text
            db_payload = {"text": text}
        else:
            payload["raw"] = str(event)

        # Broadcast a WebSocket (async, fire-and-forget)
        asyncio.create_task(self._broadcast(session_id, payload))

        # Persistir a DB (async, fire-and-forget)
        if db_payload:
            parent_id = (
                self._last_tool_req_id.get(session_id) if event_type == "tool_response" else None
            )
            asyncio.create_task(self._save_and_track(session_id, event_type, db_payload, parent_id))

        # Contar
        self._event_count[session_id] = self._event_count.get(session_id, 0) + 1

    async def _save_and_track(self, session_id, event_type, db_payload, parent_id=None):
        event_id = await self.store.save_event(
            session_id, event_type, db_payload, parent_id=parent_id
        )
        if event_type == "tool_request" and event_id:
            self._last_tool_req_id[session_id] = event_id

    async def _broadcast(self, session_id: str, payload: dict) -> None:
        text = json.dumps(payload, default=str)
        writers = self._rooms.get(session_id, set())
        dead = set()
        for w in writers:
            try:
                await _ws_send(w, text)
            except Exception:
                dead.add(w)
        if dead and session_id in self._rooms:
            self._rooms[session_id] -= dead

    async def _save_event(self, session_id, event_type, db_payload):
        """Save to session_events (fire-and-forget)."""
        if self.store and self.store._pool:
            asyncio.create_task(self._save_and_track(session_id, event_type, db_payload))

    # ── Connection handler ─────────────────────────────────────────────

    async def _handle_connection(self, reader, writer):
        try:
            data = await asyncio.wait_for(reader.read(8192), timeout=10)
        except TimeoutError:
            writer.close()
            return
        request = data.decode("utf-8", errors="replace")
        if "Upgrade: websocket" in request or "upgrade: websocket" in request:
            await self._handle_ws(reader, writer, request)
        else:
            await self._handle_http(reader, writer, data)

    async def _handle_ws(self, reader, writer, request: str):
        key = ""
        session_id = "default"
        for line in request.split("\r\n"):
            low = line.lower()
            if low.startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
            elif low.startswith("x-session-id:"):
                session_id = line.split(":", 1)[1].strip()
        if not key:
            writer.close()
            return

        accept_key = hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        accept_b64 = base64.b64encode(accept_key).decode()

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_b64}\r\n"
            "Access-Control-Allow-Origin: *\r\n\r\n"
        )
        writer.write(response.encode())
        await writer.drain()

        self._rooms.setdefault(session_id, set()).add(writer)
        await _ws_send(
            writer, json.dumps({"type": "connected", "session": session_id, "ts": time.time()})
        )

        try:
            while True:
                msg = await _ws_recv(reader)
                if msg is None:
                    break
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                t = data.get("type")
                if t == "ping":
                    await _ws_send(writer, json.dumps({"type": "pong"}))
                elif t == "subscribe":
                    new_sid = data.get("session", "")
                    if new_sid and new_sid != session_id:
                        self._rooms.get(session_id, set()).discard(writer)
                        session_id = new_sid
                        self._rooms.setdefault(session_id, set()).add(writer)
                        await _ws_send(
                            writer, json.dumps({"type": "subscribed", "session": session_id})
                        )
        except Exception:
            pass
        finally:
            self._rooms.get(session_id, set()).discard(writer)
            try:
                writer.close()
            except Exception:
                pass

    async def _handle_http(self, reader, writer, raw_data):
        request = raw_data.decode("utf-8", errors="replace")
        lines = request.split("\r\n")
        if not lines:
            writer.close()
            return
        try:
            method, path, _ = lines[0].split(" ", 2)
        except ValueError:
            writer.close()
            return

        if method == "GET" and path == "/":
            await self._serve_dashboard(writer)
        elif method == "GET" and path.startswith("/api/"):
            await self._serve_api(writer, path)
        else:
            await self._send_http(writer, 404, b'{"error":"not found"}')

    async def _send_http(self, writer, status, body: bytes, content_type="application/json"):
        resp = (
            f"HTTP/1.1 {status} OK\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Connection: close\r\n\r\n"
        )
        try:
            writer.write(resp.encode() + body)
            await writer.drain()
        except Exception:
            pass
        try:
            writer.close()
        except Exception:
            pass

    async def _serve_api(self, writer, path):
        parts = path.strip("/").split("/")
        if len(parts) < 2:
            await self._send_http(writer, 404, b'{"error":"not found"}')
            return
        resource = parts[1]
        if resource == "status":
            ws_count = sum(len(v) for v in self._rooms.values())
            await self._send_http(
                writer,
                200,
                json.dumps(
                    {
                        "rooms": len(self._rooms),
                        "clients": ws_count,
                        "sessions": {s: len(w) for s, w in self._rooms.items()},
                    }
                ).encode(),
            )
            return
        if resource == "sessions":
            if len(parts) == 2:
                sessions = await self.store.get_recent_sessions()
                await self._send_http(writer, 200, json.dumps(sessions, default=str).encode())
                return
            session_id = parts[2]
            if len(parts) == 3:
                timeline = await self.store.get_timeline(session_id)
                stats = await self.store.get_stats(session_id)
                await self._send_http(
                    writer,
                    200,
                    json.dumps(
                        {
                            "session_id": session_id,
                            "events": timeline,
                            "stats": stats,
                        },
                        default=str,
                    ).encode(),
                )
                return
        # Direct session ID lookup
        session_id = resource
        timeline = await self.store.get_timeline(session_id)
        if not timeline:
            await self._send_http(writer, 404, b'{"error":"session not found"}')
            return
        stats = await self.store.get_stats(session_id)
        await self._send_http(
            writer,
            200,
            json.dumps(
                {
                    "session_id": session_id,
                    "events": timeline,
                    "stats": stats,
                },
                default=str,
            ).encode(),
        )

    async def _serve_dashboard(self, writer):
        html = self._dashboard_html()
        await self._send_http(writer, 200, html.encode(), "text/html; charset=utf-8")

    def _dashboard_html(self):
        return """<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache,no-store,must-revalidate">
<title>🦆 {agent_name} Pipeline</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;background:#0d1117;color:#e6edf3;height:100vh;overflow:hidden}
.layout{display:grid;grid-template-columns:220px 1fr;height:100vh}
.sidebar{background:#161b22;border-right:1px solid #30363d;overflow-y:auto;padding:10px}
.main{display:flex;flex-direction:column;overflow:hidden}
.pipe-header{padding:8px 14px;border-bottom:1px solid #30363d;background:#0d1117;display:flex;align-items:center;gap:8px;flex-shrink:0;font-size:13px}
.pipe-body{flex:1;overflow-y:auto;padding:8px 12px}
.turn{margin:6px 0;border:1px solid #30363d;border-radius:8px;overflow:hidden;background:#161b22;animation:fadeIn .2s}
.turn-header{display:flex;align-items:center;gap:6px;padding:6px 10px;background:#1c2128;border-bottom:1px solid #30363d;font-size:12px}
.turn-header .badge{font-size:9px;padding:1px 5px;border-radius:4px;margin-left:auto}
.turn-header .badge.done{background:#23863622;color:#3fb950}
.turn-header .badge.running{background:#d2992222;color:#d29922}
.step{border-left:3px solid #30363d;margin:0;padding:0}
.step-header{display:flex;align-items:center;gap:5px;padding:5px 10px;cursor:pointer;font-size:11.5px;transition:background .1s}
.step-header:hover{background:#1c2128}
.step-header .icon{width:16px;text-align:center;font-size:10px}
.step-header .label{font-weight:500;overflow:hidden;text-overflow:ellipsis}
.step-header .time{font-size:9px;color:#484f58;margin-left:auto}
.step-header .arrow{font-size:7px;color:#484f58;transition:transform .12s;display:inline-block;margin-right:2px}
.step-header.expanded .arrow{transform:rotate(90deg)}
.step-body{display:none;padding:6px 10px 8px 34px;background:#0d1117;border-top:1px solid #21262d;font-size:12px;line-height:1.6}
.step-body.open{display:block}
.step.think{border-left-color:#bc8cff66}
.step.think .step-body{color:#bc8cff;font-style:italic;max-height:300px;overflow-y:auto}
.step.think .step-body pre{font-family:inherit;font-size:12px;line-height:1.6;white-space:pre-wrap;word-wrap:break-word;color:#bc8cff}
.step.shell{border-left-color:#d2992266}
.step.shell .step-body{font-family:monospace;font-size:11px;padding:4px 8px 6px 34px;max-height:400px;overflow-y:auto}
.step.shell .io-box{border:1px solid #30363d;border-radius:5px;margin:2px 0;overflow:hidden}
.step.shell .io-label{padding:1px 6px;font-size:8px;font-weight:600;text-transform:uppercase;color:#8b949e;background:#1c2128;border-bottom:1px solid #30363d}
.step.shell .io-input{background:#0d1117;padding:3px 6px;color:#e6edf3;white-space:pre-wrap;word-break:break-all;font-size:11px}
.step.shell .io-output{background:#0d2818;padding:3px 6px;color:#7ee787;white-space:pre-wrap;font-size:10.5px;line-height:1.4;max-height:250px;overflow-y:auto}
.step.text{border-left-color:#58a6ff66}
.step.finish{border-left-color:#3fb95066}
.step.finish .step-body{font-size:10.5px;color:#3fb950;padding:3px 8px 3px 34px}
.step.text .step-body{color:#e6edf3}
.section-title{font-size:9px;font-weight:600;color:#8b949e;text-transform:uppercase;letter-spacing:.08em;margin:10px 0 4px 4px}
.session-item{padding:5px 8px;border-radius:5px;cursor:pointer;font-size:11px;margin:1px 0;display:flex;align-items:center;gap:4px;transition:background .15s}
.session-item:hover{background:#1c2128}
.session-item.active{background:#1f6feb22;border:1px solid #1f6feb44}
.session-item .count{background:#30363d33;color:#8b949e;padding:0 5px;border-radius:4px;font-size:9px;margin-left:auto}
#searchBox{background:#0d1117;border:1px solid #30363d;color:#e6edf3;padding:4px 7px;border-radius:5px;font-size:11px;width:100%;margin-bottom:4px;outline:none}
#searchBox:focus{border-color:#1f6feb}
.status-dot{width:6px;height:6px;border-radius:50%;display:inline-block}
.status-dot.live{background:#3fb950;box-shadow:0 0 4px #3fb95066}
.status-dot.idle{background:#8b949e}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
.empty-state{padding:30px;text-align:center;color:#484f58;font-size:13px}
</style></head><body>
<div class="layout"><div class="sidebar">
<div style="display:flex;align-items:center;gap:5px;margin-bottom:8px;padding-left:4px"><span style="font-size:14px">🦆</span><span style="font-weight:600;font-size:12px">Pipeline</span><span class="status-dot live" id="statusDot"></span></div>
<input id="searchBox" placeholder="Buscar..." oninput="filterSessions()">
<div class="section-title">Sesiones</div>
<div id="sessionList"><div style="color:#484f58;font-size:11px;padding:8px">Cargando...</div></div>
</div><div class="main">
<div class="pipe-header"><span class="status-dot live" id="liveDot"></span><span id="currentSession" style="font-weight:500">Seleccioná una sesión</span><span style="flex:1"></span><span style="font-size:10px;color:#484f58" id="eventCount"></span></div>
<div class="pipe-body" id="pipeline"><div class="empty-state"><div style="font-size:24px;margin-bottom:8px">🦆</div>Elegí una sesión</div></div>
</div></div>
<script>
let ws=null,cs="",sessions=[],ct=null,cq=null,cu=null;const P=document.getElementById("pipeline");
function conn(){ws=new WebSocket("ws://"+location.host+"?v=8");ws.onopen=()=>l(true);ws.onclose=()=>{l(false);setTimeout(conn,3000)};ws.onmessage=e=>{try{var d=JSON.parse(e.data);if(d.type==="connected"&&cs)sub();if(d.type==="pong"||d.type==="subscribed")return;if((d.session||"default")!==cs)return;on(d)}catch(e){}}}
function l(on){document.querySelectorAll(".status-dot").forEach(e=>e.className="status-dot "+(on?"live":"idle"))}
function sub(){if(ws)ws.send(JSON.stringify({type:"subscribe",session:cs}))}
function esc(s){return s?s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"):""}
function ft(ts){return ts?new Date(ts*1000).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",second:"2-digit"}):""}
function el(s){if(!s)return"";var x=Math.floor(Date.now()/1000-s);return x<1?"0s":x<60?x+"s":Math.floor(x/60)+"m "+x%60+"s"}
function sb(){requestAnimationFrame(()=>P.scrollTop=P.scrollHeight)}
function uc(){document.getElementById("eventCount").textContent=P.querySelectorAll(".step").length+" pasos"}
function rmE(){var e=P.querySelector(".empty-state");if(e)e.remove()}
function on(d){
 if(d.type==="user_message"){rmE();cq=null;cu=null;var dv=document.createElement("div");dv.className="turn fade-in";dv.innerHTML='<div class="turn-header"><span>🧑</span><span>'+esc((d.text||"").slice(0,200))+'</span><span class="ts" style="font-size:10px;color:#484f58;margin-left:6px">'+ft(d.ts)+'</span><span class="badge running">▶ ejecutando</span></div><div class="turn-body"></div>';P.appendChild(dv);ct=dv;sb();uc();return}
 if(!ct)return;var tb=ct.querySelector(".turn-body");if(!tb)return;
 if(d.type==="thinking"){if(!cq){var st=document.createElement("div");st.className="step think";st.innerHTML='<div class="step-header"><span class="arrow">▶</span><span class="icon">💭</span><span class="label">Razonando</span><span class="time">0s</span></div><div class="step-body"><pre></pre></div>';tb.appendChild(st);cq=st;cq.dataset.start=d.ts||Date.now()/1000;st.querySelector(".step-header").onclick=function(){this.classList.toggle("expanded");var n=this.nextElementSibling;if(n)n.classList.toggle("open")}}var pr=cq.querySelector(".step-body pre");if(pr)pr.textContent+=d.text||"";var ti=cq.querySelector(".time");if(ti)ti.textContent=el(parseFloat(cq.dataset.start));sb();return}
 if(d.type==="text"){if(!cu){var st=document.createElement("div");st.className="step text";st.innerHTML='<div class="step-header"><span class="arrow">▶</span><span class="icon">📝</span><span class="label">Respuesta</span><span class="time">0s</span></div><div class="step-body"><pre style="white-space:pre-wrap;word-wrap:break-word"></pre></div>';tb.appendChild(st);cu=st;st.querySelector(".step-header").onclick=function(){this.classList.toggle("expanded");var n=this.nextElementSibling;if(n)n.classList.toggle("open")}}var pr=cu.querySelector("pre");if(pr)pr.textContent+=d.text||"";uc();sb();return}
 if(d.type==="tool_request"){rmE();var st=document.createElement("div");st.className="step shell";var a=esc(d.args||""),a2=a.length>80?a.slice(0,80)+"…":a;st.innerHTML='<div class="step-header"><span class="arrow">▶</span><span class="icon">🔧</span><span class="label">'+esc(d.tool||"")+'</span><span style="font-size:11px;color:#8b949e;overflow:hidden;text-overflow:ellipsis;margin-left:4px">'+a2+'</span><span class="time">0s</span></div><div class="step-body"><div class="io-box"><div class="io-label">📥 Input</div><div class="io-input">$ '+a+'</div></div><div class="io-box"><div class="io-label">📤 Output</div><div class="io-output" style="color:#8b949e;font-style:italic">⌛ ejecutando...</div></div></div>';st.dataset.toolTime=d.ts||Date.now()/1000;var h=st.querySelector(".step-header");h.onclick=function(){this.classList.toggle("expanded");var n=this.nextElementSibling;if(n)n.classList.toggle("open")};tb.appendChild(st);sb();return}
 if(d.type==="tool_response"){var ss=tb.querySelectorAll(".step.shell");if(ss.length){var l=ss[ss.length-1];var b=l.querySelector(".step-body");if(b){var r=b.querySelector(".io-output");if(r)r.outerHTML='<div class="io-output" style="color:#7ee787;background:#0d2818;font-size:10.5px;line-height:1.4;white-space:pre-wrap;max-height:250px;overflow-y:auto">'+esc((d.text||"").slice(0,3000))+"</div>"}var ti=l.querySelector(".time");if(ti&&l.dataset.toolTime)ti.textContent=el(parseFloat(l.dataset.toolTime))}sb();return}
 if(d.type==="finish"){if(cq&&cq.querySelector(".step-body pre")&&!cq.querySelector(".step-body pre").textContent.trim()){cq.remove();cq=null}var bd=ct?ct.querySelector(".badge"):null;if(bd){bd.className="badge done";bd.textContent="✅ "+(d.reason||"stop")+" · +"+(d.tokens||0)+" tok"}var st=document.createElement("div");st.className="step finish";var tt=el(cq?parseFloat(cq.dataset.start):d.ts);st.innerHTML='<div class="step-header"><span class="icon">✅</span><span class="label">Completado</span><span style="font-size:11px;color:#3fb950">+'+(d.tokens||0)+' tok</span><span class="time">'+tt+'</span></div>';tb.appendChild(st);uc();return}
 if(d.type==="error"){var st=document.createElement("div");st.className="step error";st.innerHTML='<div class="step-header"><span class="icon">❌</span><span class="label">Error</span></div><div class="step-body open"><pre>'+esc(d.text)+"</pre></div>";tb.appendChild(st);return}}
function ls(){fetch("/api/sessions").then(r=>r.json()).then(d=>{sessions=d||[];var el=document.getElementById("sessionList");if(!sessions.length){el.innerHTML='<div style="color:#484f58;font-size:11px;padding:8px">Sin sesiones</div>';return}var h="";for(var i=0;i<sessions.length&&i<50;i++){var s=sessions[i],sid=s.session_id,a=sid===cs,dn=sid.length>30?sid.slice(0,30)+"…":sid;h+='<div class="session-item'+(a?" active":"")+'" data-sid="'+esc(sid)+'"><span>📋</span><span style="overflow:hidden;text-overflow:ellipsis">'+esc(dn)+'</span><span class="count">'+s.event_count+"</span></div>"}el.innerHTML=h;el.querySelectorAll(".session-item").forEach(function(e){e.onclick=function(){ss(e.dataset.sid)}})}).catch(function(){})}
function fs(){var q=document.getElementById("searchBox").value.toLowerCase();document.querySelectorAll(".session-item").forEach(function(e){e.style.display=e.textContent.toLowerCase().includes(q)?"":"none"})}
async function ss(sid){cs=sid;document.getElementById("currentSession").textContent=sid.length>50?sid.slice(0,50)+"…":sid;P.innerHTML='<div class="empty-state"><div style="font-size:24px">📡</div>Cargando...</div>';ct=null;cq=null;cu=null;sub();try{var r=await fetch("/api/"+encodeURIComponent(sid));var data=await r.json();P.innerHTML="";if(data.events&&data.events.length){for(var i=0;i<data.events.length;i++){var p=data.events[i].payload||{};on({type:data.events[i].event_type,ts:data.events[i].created_at?new Date(data.events[i].created_at.replace(" ","T")).getTime()/1000:Date.now()/1000,text:p.text||p.full||"",tool:p.tool||"",args:p.args||"",tokens:p.tokens,reason:p.reason})}}else{P.innerHTML='<div class="empty-state"><div style="font-size:24px">💬</div>Sin mensajes</div>'}}catch(e){P.innerHTML='<div class="empty-state"><div style="font-size:24px">❌</div>Error</div>'}document.querySelectorAll(".session-item").forEach(function(e){e.classList.toggle("active",e.dataset.sid===sid)});sb()}
function fs(){var q=document.getElementById("searchBox").value.toLowerCase();document.querySelectorAll(".session-item").forEach(function(e){e.style.display=e.textContent.toLowerCase().includes(q)?"":"none"})}
conn();ls();setInterval(ls,5000);setInterval(function(){if(ws)ws.send(JSON.stringify({type:"ping"}))},30000);
</script></body></html>
"""

    async def _poll_agent_events(self):
        """Background: poll agent_events for sub-agent tool calls and broadcast them."""
        await asyncio.sleep(2.0)
        last_id = 0
        try:
            if self.store and self.store._pool:
                async with self.store._pool.acquire() as conn:
                    r = await conn.fetchrow("SELECT COALESCE(max(id),0) FROM agent_events")
                    if r:
                        last_id = r[0]
        except Exception:
            pass
        while True:
            try:
                if not self.store or not self.store._pool:
                    await asyncio.sleep(5)
                    continue
                async with self.store._pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT id, session_id, payload_json FROM agent_events WHERE kind='process_started' AND id>$1 ORDER BY id LIMIT 20",
                        last_id,
                    )
                    for r in rows:
                        p = r["payload_json"]
                        if isinstance(p, str):
                            try:
                                p = __import__("json").loads(p)
                            except Exception:
                                p = {}
                        m = p.get("goosed_session_id", "")
                        if m:
                            self._agent_sessions[r["session_id"]] = m
                        if r["id"] > last_id:
                            last_id = r["id"]
                    rows = await conn.fetch(
                        "SELECT id, session_id, payload_json, ts FROM agent_events WHERE kind='tool_called' AND id>$1 ORDER BY id LIMIT 50",
                        last_id,
                    )
                    for r in rows:
                        sub = r["session_id"]
                        main = self._agent_sessions.get(sub, "")
                        if not main:
                            if r["id"] > last_id:
                                last_id = r["id"]
                                continue
                        p = r["payload_json"]
                        if isinstance(p, str):
                            try:
                                p = __import__("json").loads(p)
                            except Exception:
                                p = {}
                        cmds = p.get("commands", [])
                        act = p.get("action", "")
                        cmd = "; ".join(cmds) if cmds else (act or "shell")
                        ts_raw = r["ts"]
                        e = ts_raw.timestamp() if hasattr(ts_raw, "timestamp") else time.time()
                        asyncio.create_task(
                            self._broadcast(
                                main,
                                {
                                    "type": "tool_request",
                                    "session": main,
                                    "ts": e,
                                    "tool": "shell",
                                    "args": cmd,
                                },
                            )
                        )
                        # Also persist to session_events so they appear on reload
                        asyncio.create_task(
                            self._save_and_track(
                                main, "tool_request", {"tool": "shell", "args": cmd}
                            )
                        )
                        asyncio.create_task(
                            self._broadcast(
                                main,
                                {
                                    "type": "tool_response",
                                    "session": main,
                                    "ts": e + 0.1,
                                    "text": p.get("result", "✅ done"),
                                },
                            )
                        )
                        asyncio.create_task(
                            self._save_and_track(
                                main,
                                "tool_response",
                                {"text": (p.get("result") or "✅ done")[:300]},
                            )
                        )
                        if r["id"] > last_id:
                            last_id = r["id"]
                    rows = await conn.fetch(
                        "SELECT id FROM agent_events WHERE kind='process_ended' AND id>$1 ORDER BY id LIMIT 20",
                        last_id,
                    )
                    for r in rows:
                        if r["id"] > last_id:
                            last_id = r["id"]
            except Exception as e:
                logger.debug("Observe: agent_poll error: %s", e)
            await asyncio.sleep(3.0)

    async def _poll_goosed_db(self):
        """Background: poll goosed SQLite DB for tool/thinking events not in SSE."""
        await asyncio.sleep(5.0)
        db_paths = ["/goosed-sessions/sessions.db", "/root/.local/share/goose/sessions/sessions.db"]
        last_ids: dict[str, int] = {}
        import json

        while True:
            db_path = None
            for p in db_paths:
                if __import__("os").path.exists(p):
                    db_path = p
                    break
            if not db_path:
                logger.debug("Observe: goosed DB not found at any path - retry in 10s")
                await asyncio.sleep(10)
                continue
            try:
                import sqlite3

                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                for row in c.execute(
                    "SELECT id, session_id, role, content_json, created_timestamp FROM messages ORDER BY id"
                ):
                    mid = row["id"]
                    session_id = row["session_id"]
                    last_id = last_ids.get(db_path + session_id, 0)
                    if mid <= last_id:
                        continue
                    # role unused
                    content_json = row["content_json"]
                    for item in json.loads(content_json):
                        item_type = item.get("type", "")
                        ts = row["created_timestamp"]
                        if item_type == "thinking":
                            thinking_text = item.get("thinking", "")
                            payload = {
                                "type": "thinking",
                                "session": session_id,
                                "ts": ts,
                                "text": thinking_text,
                            }
                            asyncio.create_task(self._broadcast(session_id, payload))
                            db_payload = {"text": thinking_text, "full": thinking_text}
                            asyncio.create_task(
                                self._save_event(session_id, "thinking", db_payload)
                            )
                        elif item_type == "toolRequest":
                            pass  # SSE now includes toolRequest - skip to avoid duplicates and wrong ordering
                        elif item_type == "toolResponse":
                            pass  # SSE now includes toolResponse - skip to avoid duplicates and wrong ordering
                    last_ids[db_path + session_id] = mid
                conn.close()
            except Exception as e:
                logger.debug("Observe: goosed DB poll error: %s", e)
            await asyncio.sleep(5.0)


# ═══════════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════════


def create_observer(
    port: int = 9090,
    db_url: str | None = None,
    goosed_url: str = "",
) -> ObserveServer:
    """Crea y retorna un ObserveServer listo para start()."""
    return ObserveServer(port=port, db_url=db_url, goosed_url=goosed_url)
