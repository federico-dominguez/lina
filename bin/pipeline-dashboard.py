#!/usr/bin/env python3
"""
pipeline-dashboard.py — Dashboard web para el Pipeline Manager.

Muestra ejecuciones de pipelines en vivo, con pasos, tokens y costos.
Lee los registros de /tmp/pipeline-runs.jsonl (generados por pipeline-runner.py).

Puerto: 9097
"""

import asyncio, json, os, subprocess, sys, time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_DSN = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")
PORT = 9097
RUNS_FILE = "/tmp/pipeline-runs.jsonl"

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
.card-header .name{font-weight:600;font-size:13px;display:flex;align-items:center;gap:4px}
.card-header .meta{font-size:10px;color:#8b949e;text-align:right}
.card-body{padding:0;display:none}
.card-body.open{display:block}
.step{display:flex;align-items:center;padding:6px 10px;border-bottom:1px solid #161b22;font-size:12px;gap:6px}
.step:last-child{border-bottom:none}
.step .icon{width:18px;text-align:center;font-size:12px}
.step .bot{font-weight:500;min-width:36px;color:#c9d1d9}
.step .msg{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8b949e}
.step .meta{font-size:10px;color:#484f58;white-space:nowrap;display:flex;gap:4px}
.empty{text-align:center;padding:40px 20px;color:#484f58}
.empty .icon{font-size:48px;margin-bottom:12px}
.toolbar{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:4px;padding:6px 12px;border-radius:8px;font-size:12px;cursor:pointer;border:none;font-weight:500}
.btn-primary{background:#1f6feb;color:#fff}
.btn-primary:active{background:#388bfd}
.log-area{background:#0d1117;padding:8px 10px;font-family:monospace;font-size:9px;color:#8b949e;max-height:120px;overflow-y:auto;line-height:1.5;white-space:pre-wrap}
.pipeline-name{display:inline-flex;align-items:center;gap:3px;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:500}
.pipeline-name.lina{color:#58a6ff;background:#1f6feb22}
.pipeline-name.cline{color:#f0883e;background:#f0883e22}
.pipeline-name.goose{color:#7ee787;background:#7ee78722}
.pipeline-name.default{color:#8b949e;background:#21262d}
.phase-bar{display:flex;gap:3px;align-items:center;margin:6px 10px}
.phase{flex:1;height:4px;border-radius:2px}
.phase.done{background:#3fb950}
.phase.running{background:#d29922;animation:pulse 1s infinite}
.phase.error{background:#f85149}
.phase.waiting{background:#21262d}
@keyframes pulse{0%{opacity:1}50%{opacity:.4}100%{opacity:1}}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>📊 Pipeline Dashboard</h1>
<span class="badge" id="statusBadge">🔄 cargando...</span>
</div>

<div class="stats">
<div class="stat-card"><div class="stat-num" id="totalRuns">0</div><div class="stat-label">Ejecuciones</div></div>
<div class="stat-card"><div class="stat-num" id="totalSteps">0</div><div class="stat-label">Pasos</div></div>
<div class="stat-card"><div class="stat-num" id="totalCost" style="color:#d29922">$0</div><div class="stat-label">Costo est.</div></div>
</div>

<div class="toolbar">
<button class="btn btn-primary" onclick="refresh()">🔄 Refrescar</button>
<button class="btn btn-primary" onclick="document.getElementById('logPanel').style.display=document.getElementById('logPanel').style.display==='none'?'block':'none'">📋 Logs</button>
</div>

<div id="logPanel" style="display:none">
<div class="card"><div class="card-header"><span>📋 Logs de eventos</span><span class="meta" id="logCount">0</span></div>
<div class="card-body open"><div class="log-area" id="logArea"></div></div></div></div>

<div id="pipelineList"><div class="empty"><div class="icon">📂</div>Cargando ejecuciones...</div></div>
</div>

<script>
let runs = [];

function esc(s){return String(s||'').replace(/[<>]/g,c=>({'<':'&lt;','>':'&gt;'})[c])}
function ts(sec){const d=new Date((sec||0)*1000);return d.toLocaleTimeString()}
function ago(sec){if(!sec)return'';const s=Math.floor((Date.now()/1000-sec));if(s<60)return s+'s';if(s<3600)return Math.floor(s/60)+'m';return Math.floor(s/3600)+'h'+Math.floor((s%3600)/60)+'m'}
function cost(t){return((t||0)*3/1e6*2).toFixed(4)}

async function refresh(){
document.getElementById('statusBadge').textContent='🔄 cargando...';
try{
const r=await fetch('/api/runs');
const d=await r.json();
runs=d.runs||[];
render();
updateStats();
document.getElementById('statusBadge').textContent='✅ '+(runs.length||'0')+' ejecuciones';
}catch(e){document.getElementById('statusBadge').textContent='❌ error';}
}

function render(){
const el=document.getElementById('pipelineList');
if(!runs.length){el.innerHTML='<div class="empty"><div class="icon">🚀</div>Sin ejecuciones aún<br><span style="font-size:12px;color:#8b949e">Corré un pipeline con @s_pipelines_bot</span></div>';return}
let h='';
for(const r of runs){
const n=r.name||'?';
const st=r.status||'waiting';
const stateIcon=st==='done'?'✅':st==='error'?'❌':'🟡';
const stateLabel=st==='done'?'Completado':st==='error'?'Error':'Ejecutando';
const timeStr=ago(r.started_at);
const steps=r.steps||[];
const totalTok=steps.reduce((a,s)=>a+((s.tokens||0)+(s.tokens||0)),0);
const totalCost=cost(totalTok);
let stepsHtml='';
let phaseHtml='<div class="phase-bar">';
for(const s of steps){
const sd=s.status||'waiting';
const si=sd==='done'?'✅':sd==='error'?'❌':'🟡';
const botClass='pipeline-name '+(s.bot||'default');
const tokStr=s.tokens?'<span class="meta">'+s.tokens+' tok</span>':'';
const costStr=s.tokens?'<span class="meta">$'+cost(s.tokens)+'</span>':'';
stepsHtml+='<div class="step"><span class="icon">'+si+'</span><span class="bot '+botClass+'">'+esc(s.bot||'')+'</span><span class="msg">'+esc((s.msg||'').slice(0,120))+'</span>'+tokStr+costStr+'</div>';
const phaseClass=sd==='done'?'done':sd==='running'?'running':sd==='error'?'error':'waiting';
phaseHtml+='<div class="phase '+phaseClass+'"></div>';
}
phaseHtml+='</div>';
h+=`<div class="card">
<div class="card-header" onclick="this.nextElementSibling.classList.toggle('open')">
<span class="name">${stateIcon} ${esc(n)} <span style="font-size:10px;color:#8b949e;font-weight:400">${timeStr}</span></span>
<span class="meta">${stateLabel} · ${steps.length} pasos · ${totalTok} tok · $${totalCost}</span>
</div>
<div class="card-body open">${phaseHtml}${stepsHtml}</div>
</div>`;
}
el.innerHTML=h;
}

function updateStats(){
const total=runs.length;
const tSteps=runs.reduce((a,r)=>a+(r.steps||[]).length,0);
const tTok=runs.reduce((a,r)=>a+(r.steps||[]).reduce((b,s)=>b+((s.tokens||0)),0),0);
document.getElementById('totalRuns').textContent=total;
document.getElementById('totalSteps').textContent=tSteps;
document.getElementById('totalCost').textContent='$'+cost(tTok);
}

function addLog(line){
const el=document.getElementById('logArea');
const ts=new Date().toLocaleTimeString();
el.innerHTML+=`<div>[${ts}] ${esc(line)}</div>`;
el.scrollTop=el.scrollHeight;
const c=document.getElementById('logCount');
if(c)c.textContent=el.children.length;
}

refresh();
setInterval(refresh,3000);
</script>
</body>
</html>"""


def load_runs():
    """Lee /tmp/pipeline-runs.jsonl y devuelve las últimas 50 ejecuciones."""
    if not os.path.exists(RUNS_FILE):
        return []
    runs = []
    try:
        with open(RUNS_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    runs.append(json.loads(line))
        # Últimas 50, ordenadas por completed_at descendente
        runs.sort(key=lambda r: r.get("completed_at", 0), reverse=True)
        return runs[:50]
    except Exception:
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

    if "Upgrade: websocket" in request.lower():
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
        resp = "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" \
               f"Sec-WebSocket-Accept: {accept}\r\n" \
               "Access-Control-Allow-Origin: *\r\n\r\n"
        writer.write(resp.encode())
        await writer.drain()
        try:
            while True:
                await asyncio.wait_for(reader.read(1024), timeout=60)
        except:
            pass
        writer.close()
        return

    if method == "GET":
        if path == "/":
            body = HTML.encode("utf-8")
            resp = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\n" \
                   "Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n"
            writer.write(resp.encode() + body)
            await writer.drain()
            writer.close()
            return

        if path == "/api/runs":
            runs = load_runs()
            body = json.dumps({"runs": runs}).encode()
            resp = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n" \
                   f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            writer.write(resp.encode() + body)
            await writer.drain()
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
