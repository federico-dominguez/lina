#!/usr/bin/env python3
"""
pipeline-dashboard.py — Dashboard web para el Pipeline Manager.

Puerto: 9097
Acceso: http://192.168.1.13:9097  (o desde el celular en la misma red)

Muestra:
- Pipelines guardados y su estado
- Ejecuciones en vivo con pasos (✅ 🟡 ❌ ⏳)
- Costo y tokens por paso
- Logs en tiempo real vía WebSocket del Observe
"""

import asyncio, json, os, subprocess, sys, time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
YAML_DIR = BASE / "config" / "pipelines"
BIN_PIPELINE = BASE / "bin" / "pipeline"
DB_DSN = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")

PORT = 9097

HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>📊 Pipeline Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;font-size:14px;padding:0}
.container{max-width:800px;margin:0 auto;padding:12px}
.header{display:flex;justify-content:space-between;align-items:center;padding:10px 0 8px;border-bottom:1px solid #21262d;margin-bottom:12px}
.header h1{font-size:18px;color:#58a6ff}
.header .badge{font-size:10px;background:#1f6feb22;color:#58a6ff;padding:2px 8px;border-radius:10px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:12px}
.stat-card{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:8px;text-align:center}
.stat-num{font-size:22px;font-weight:700;color:#f0f6fc}
.stat-label{font-size:10px;color:#8b949e;margin-top:2px}
.card{background:#161b22;border:1px solid #21262d;border-radius:8px;margin-bottom:8px;overflow:hidden}
.card-header{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;cursor:pointer;background:#1c2128;border-bottom:1px solid #21262d}
.card-header:hover{background:#21262d}
.card-header .name{font-weight:600;font-size:13px}
.card-header .time{font-size:10px;color:#8b949e}
.card-body{padding:0;display:none}
.card-body.open{display:block}
.step{display:flex;align-items:center;padding:6px 10px;border-bottom:1px solid #161b22;font-size:12px}
.step:last-child{border-bottom:none}
.step .icon{width:20px;text-align:center;margin-right:6px;font-size:13px}
.step .bot{font-weight:500;min-width:40px}
.step .msg{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8b949e;margin:0 6px}
.step .tokens{font-size:10px;color:#484f58;white-space:nowrap;margin-left:4px}
.step .cost{font-size:10px;color:#484f58;white-space:nowrap;margin-left:4px}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}
.status-dot.done{background:#3fb950}
.status-dot.running{background:#d29922;animation:pulse 1.5s infinite}
.status-dot.error{background:#f85149}
.status-dot.waiting{background:#484f58}
@keyframes pulse{0%{opacity:1}50%{opacity:.4}100%{opacity:1}}
.log-area{background:#0d1117;padding:8px 10px;font-family:'SF Mono',monospace;font-size:10px;color:#8b949e;max-height:150px;overflow-y:auto;line-height:1.5}
.log-line{white-space:pre-wrap;word-break:break-all}
.log-line.info{color:#8b949e}
.log-line.error{color:#f85149}
.log-line.finish{color:#3fb950}
.empty{text-align:center;padding:30px;color:#484f58}
.empty .icon{font-size:32px;margin-bottom:8px}
.btn{display:inline-flex;align-items:center;gap:4px;padding:6px 12px;border-radius:8px;font-size:12px;cursor:pointer;border:none;font-weight:500}
.btn-primary{background:#1f6feb;color:#fff}
.btn-primary:active{background:#388bfd}
.btn-small{padding:3px 8px;font-size:10px}
.toolbar{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap}
@media(max-width:500px){.stats{grid-template-columns:repeat(3,1fr)}.stat-num{font-size:18px}}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>📊 Pipeline Dashboard</h1>
<span class="badge" id="statusBadge">🔄 conectando...</span>
</div>

<div class="stats">
<div class="stat-card"><div class="stat-num" id="totalPipelines">0</div><div class="stat-label">Pipelines</div></div>
<div class="stat-card"><div class="stat-num" id="totalRuns">0</div><div class="stat-label">Ejecuciones</div></div>
<div class="stat-card"><div class="stat-num" id="totalCost" style="color:#d29922">$0</div><div class="stat-label">Costo total</div></div>
</div>

<div class="toolbar">
<button class="btn btn-primary btn-small" onclick="refreshPipelines()">🔄 Refrescar</button>
<button class="btn btn-primary btn-small" onclick="showLog(event)">📋 Últimos logs</button>
</div>

<div id="pipelineList"></div>
<div id="logPanel" style="display:none" class="card">
<div class="card-header" onclick="this.nextElementSibling.classList.toggle('open')">
<span>📋 Logs recientes</span>
<span class="time" id="logCount">0</span>
</div>
<div class="card-body open"><div class="log-area" id="logArea"></div></div>
</div>
</div>

<script>
const API = '';
let pipelines = [];
let logs = [];

function esc(s){return String(s||'').replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}

async function refreshPipelines(){
document.getElementById('statusBadge').textContent='🔄 cargando...';
try{
const r=await fetch(API+'/api/pipelines');
const data=await r.json();
pipelines=data.pipelines||[];
renderPipelines();
updateStats();
}catch(e){
document.getElementById('statusBadge').textContent='❌ error';
}}

function renderPipelines(){
const el=document.getElementById('pipelineList');
if(!pipelines.length){
el.innerHTML='<div class="empty"><div class="icon">📂</div>No hay ejecuciones recientes</div>';
return;
}
let h='';
for(const p of pipelines){
const status=p.status||{state:'waiting',steps:[]};
const stateDot=status.state==='done'?'🟢':status.state==='running'?'🟡':'🔴';
h+=`<div class="card">
<div class="card-header" onclick="this.nextElementSibling.classList.toggle('open')">
<span class="name">${stateDot} ${esc(p.name||'?')}</span>
<span class="time">${esc(p.time||'')} · $${(p.cost||0).toFixed(4)} · ${(p.tokens||0)} tok</span>
</div>
<div class="card-body open">`;
for(const s of (status.steps||[])){
const sd=s.status||'waiting';
const icon=s.icon||(sd==='done'?'✅':sd==='running'?'🟡':sd==='error'?'❌':'⏳');
const tokStr=s.tokens?`<span class="tokens">${s.tokens} tok</span>`:'';
const costStr=s.cost?`<span class="cost">$${s.cost.toFixed(4)}</span>`:'';
h+=`<div class="step"><span class="icon">${icon}</span><span class="bot">${esc(s.bot||'')}</span><span class="msg">${esc(s.msg||'')}</span>${tokStr}${costStr}</div>`;
}
h+=`</div></div>`;
}
el.innerHTML=h;
}

function updateStats(){
let total=0,cost=0,tokens=0;
for(const p of pipelines){total++;cost+=p.cost||0;tokens+=p.tokens||0}
document.getElementById('totalPipelines').textContent=pipelines.length;
document.getElementById('totalRuns').textContent=total;
document.getElementById('totalCost').textContent='$'+cost.toFixed(4);
document.getElementById('statusBadge').textContent='✅ '+pipelines.length+' ejecuciones';
}

function addLog(line,type){
const ts=new Date().toLocaleTimeString();
logs.push({ts,line,type});
if(logs.length>200)logs.shift();
document.getElementById('logCount').textContent=logs.length;
const el=document.getElementById('logArea');
let h='';
for(const l of logs.slice(-50)){
h+=`<div class="log-line ${l.type||'info'}">[${l.ts}] ${esc(l.line)}</div>`;
}
el.innerHTML=h;
el.scrollTop=el.scrollHeight;
}

function showLog(e){
const el=document.getElementById('logPanel');
el.style.display=el.style.display==='none'?'block':'none';
if(el.style.display==='block')addLog('Dashboard abierto','info');
}

async function connectWS(){
try{
const ws=new WebSocket('ws://'+location.host+'/ws');
ws.onopen=()=>addLog('WS conectado','info');
ws.onmessage=(e)=>{
try{const d=JSON.parse(e.data);onEvent(d)}catch(e){}
};
ws.onclose=()=>{addLog('WS desconectado. Reconectando...','error');setTimeout(connectWS,3000)};
ws.onerror=()=>{ws.close()};
window._ws=ws;
}catch(e){setTimeout(connectWS,5000)}
}

function onEvent(d){
if(d.type==='finish'){
addLog('🏁 '+d.session+' reason='+(d.reason||'stop')+' tokens='+(d.tokens||0),'finish');
refreshPipelines();
}
if(d.type==='thinking')addLog('💭 '+d.session+' '+(d.text||'').slice(0,50),'info');
if(d.type==='error')addLog('❌ '+d.session+' '+(d.text||''),'error');
}

refreshPipelines();
connectWS();
setInterval(refreshPipelines,5000);
</script>
</body>
</html>"""


async def fetch_pipelines():
    """Obtiene pipelines recientes desde los eventos finish en session_events."""
    import asyncpg
    try:
        conn = await asyncpg.connect(DB_DSN, timeout=5)
        try:
            rows = await conn.fetch("""
                SELECT se.id, se.session_id, se.agent, se.created_at, se.payload
                FROM session_events se
                WHERE se.event_type = 'finish'
                  AND se.created_at > NOW() - INTERVAL '2 hours'
                ORDER BY se.id DESC
                LIMIT 20
            """)
            pipelines = []
            for r in rows:
                p = json.loads(r["payload"]) if isinstance(r["payload"], str) else (r["payload"] or {})
                pipelines.append({
                    "name": r["agent"] or "unknown",
                    "session_id": r["session_id"],
                    "time": r["created_at"].strftime("%H:%M") if r["created_at"] else "",
                    "cost": p.get("cost", p.get("tokens", 0) * 0.000002 + (p.get("tokens", 0) // 3) * 0.000002),
                    "tokens": p.get("tokens", 0),
                    "status": {"state": "done", "steps": [{"bot": r["agent"], "msg": f"session {r['session_id']}", "status": "done", "tokens": p.get("tokens", 0), "cost": p.get("cost", 0)}]}
                })
            return pipelines
        finally:
            await conn.close()
    except Exception as e:
        return []


async def handle_request(reader, writer):
    try:
        data = await asyncio.wait_for(reader.read(65536), timeout=10)
    except asyncio.TimeoutError:
        writer.close()
        return

    request = data.decode("utf-8", errors="replace")
    lines = request.split("\r\n")
    if not lines:
        writer.close()
        return

    try:
        method, path, _ = lines[0].split(" ", 2)
    except ValueError:
        writer.close()
        return

    if "Upgrade: websocket" in request or "upgrade: websocket" in request:
        # WebSocket handler
        key = ""
        for line in lines:
            low = line.lower()
            if low.startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        if not key:
            writer.close()
            return

        import hashlib, base64
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "Access-Control-Allow-Origin: *\r\n\r\n"
        )
        writer.write(resp.encode())
        await writer.drain()
        # Keep WS alive for a bit then close
        try:
            while True:
                msg = await asyncio.wait_for(reader.read(1024), timeout=30)
                if not msg:
                    break
        except:
            pass
        writer.close()
        return

    if method == "GET" and path == "/":
        body = HTML.encode("utf-8")
        resp = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(resp.encode() + body)
        await writer.drain()
    elif method == "GET" and path == "/api/pipelines":
        pipes = await fetch_pipelines()
        body = json.dumps({"pipelines": pipes}).encode()
        resp = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(resp.encode() + body)
        await writer.drain()
    else:
        writer.close()
        return
    writer.close()


async def main():
    server = await asyncio.start_server(handle_request, "0.0.0.0", PORT)
    print(f"📊 Pipeline Dashboard: http://0.0.0.0:{PORT}", flush=True)
    print(f"   Celular: http://192.168.1.13:{PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("⏹️ Dashboard detenido")
