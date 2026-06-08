#!/usr/bin/env python3
"""
pipeline-dashboard.py — Dashboard estilo GitHub Actions.

Design System: GitHub Primer (#0d1117, #161b22, #21262d, #c9d1d9, #58a6ff)
Timeline de ejecuciones, DAG de pasos, costos en vivo.

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
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Pipeline Dashboard · LINA</title>
<style>
/* ── GitHub Primer Design System ─────────────────────────── */
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif;
     background:#0d1117;color:#e6edf3;font-size:14px;line-height:1.5;padding:0}
a{color:#58a6ff;text-decoration:none}
a:hover{text-decoration:underline}

/* ── Layout ────────────────────────────────────────────────── */
.app{max-width:960px;margin:0 auto;padding:0}
.topnav{display:flex;justify-content:space-between;align-items:center;padding:16px 24px;border-bottom:1px solid #21262d;background:#161b22}
.topnav h1{font-size:20px;font-weight:600;color:#f0f6fc;display:flex;align-items:center;gap:8px}
.topnav h1 span{font-weight:400;color:#8b949e;font-size:14px}
.topnav .status{display:flex;align-items:center;gap:6px;font-size:12px;color:#8b949e}
.status-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.status-dot.live{background:#3fb950;box-shadow:0 0 6px #3fb95066}
.content{padding:24px}
.empty-state{text-align:center;padding:80px 20px;color:#484f58}
.empty-state .icon{font-size:48px;margin-bottom:16px}
.empty-state h2{font-size:20px;color:#8b949e;margin-bottom:8px}
.empty-state p{font-size:14px;color:#484f58}

/* ── Stats bar ─────────────────────────────────────────────── */
.stats{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}
.stat{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 20px;min-width:140px;flex:1}
.stat-num{font-size:28px;font-weight:600;color:#f0f6fc}
.stat-label{font-size:12px;color:#8b949e;margin-top:2px}
.stat .change{font-size:11px;margin-top:4px}

/* ── Filter bar ────────────────────────────────────────────── */
.filter-bar{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;align-items:center}
.filter-bar .btn{padding:5px 12px;font-size:12px;border-radius:6px;border:1px solid #21262d;background:#21262d;color:#c9d1d9;cursor:pointer;transition:.2s}
.filter-bar .btn:hover{background:#30363d}
.filter-bar .btn.active{background:#1f6feb;border-color:#1f6feb;color:#fff}
.filter-bar .btn.primary{background:#238636;border-color:#238636;color:#fff;display:flex;align-items:center;gap:4px}
.filter-bar .btn.primary:hover{background:#2ea043}
.filter-bar .btn.primary:active{background:#238636}
.filter-bar input{background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:5px 10px;color:#c9d1d9;font-size:12px;flex:1;min-width:120px}
.filter-bar input:focus{border-color:#58a6ff;outline:none}

/* ── Pipeline timeline (como GitHub Actions) ──────────────── */
.timeline{position:relative}
.timeline::before{content:'';position:absolute;left:20px;top:0;bottom:0;width:2px;background:#21262d}
.run{position:relative;margin-bottom:16px;background:#161b22;border:1px solid #21262d;border-radius:8px;overflow:hidden}
.run:hover{border-color:#30363d}
.run-icon{position:absolute;left:-11px;top:14px;width:22px;height:22px;border-radius:50%;border:2px solid #0d1117;display:flex;align-items:center;justify-content:center;font-size:10px;z-index:1}
.run-icon.done{background:#238636;border-color:#238636}
.run-icon.error{background:#da3633;border-color:#da3633}
.run-icon.running{background:#d29922;border-color:#d29922}
.run-header{display:flex;justify-content:space-between;align-items:center;padding:12px 16px 12px 28px;cursor:pointer}
.run-header:hover{background:#1c2128}
.run-header .info{display:flex;align-items:center;gap:8px;min-width:0}
.run-header .name{font-weight:600;font-size:14px;color:#e6edf3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.run-header .name .actor{font-weight:400;color:#8b949e;font-size:12px}
.run-header .meta{display:flex;align-items:center;gap:12px;font-size:11px;color:#8b949e;white-space:nowrap}
.run-header .meta .badge{font-size:10px;padding:2px 6px;border-radius:10px;background:#21262d}
.run-header .meta .badge.done{background:#23863644;color:#3fb950}
.run-header .meta .badge.error{background:#da363344;color:#f85149}
.run-body{padding:0 16px 12px 28px;display:none}
.run-body.open{display:block}
.run-footer{padding:8px 16px 8px 28px;border-top:1px solid #21262d;font-size:11px;color:#8b949e;display:flex;gap:16px;background:#0d1117}

/* ── Step DAG ──────────────────────────────────────────────── */
.steps{position:relative;padding-left:28px}
.steps::before{content:'';position:absolute;left:9px;top:4px;bottom:4px;width:2px;background:#21262d}
.step{position:relative;padding:8px 0 8px 20px;border-bottom:1px solid #21262d33;display:flex;align-items:flex-start;gap:8px}
.step:last-child{border-bottom:none}
.step .dot{position:absolute;left:-17px;top:10px;width:12px;height:12px;border-radius:50%;border:2px solid #0d1117;flex-shrink:0}
.step .dot.done{background:#238636}
.step .dot.running{background:#d29922;animation:pulse 1.5s infinite}
.step .dot.error{background:#da3633}
.step .dot.waiting{background:#21262d}
.step .content{flex:1;min-width:0}
.step .content .step-header{font-size:12px;font-weight:500;color:#e6edf3}
.step .content .step-header .bot-tag{display:inline-block;padding:0 6px;border-radius:4px;font-size:10px;font-weight:500;margin-right:4px}
.step .content .step-header .bot-tag.lina{background:#1f6feb33;color:#58a6ff}
.step .content .step-header .bot-tag.cline{background:#f0883e33;color:#f0883e}
.step .content .step-header .bot-tag.goose{background:#3fb95033;color:#3fb950}
.step .content .step-detail{font-size:11px;color:#8b949e;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.step .meta{font-size:10px;color:#484f58;white-space:nowrap;display:flex;gap:6px;align-items:center}
.step .meta .tok{background:#21262d;padding:1px 5px;border-radius:4px;font-family:'SF Mono',monospace}
.step .meta .cost{font-family:'SF Mono',monospace}

/* ── Phase bar ─────────────────────────────────────────────── */
.phase-bar{display:flex;gap:2px;margin:4px 0 4px 0;height:4px}
.phase-bar .phase{flex:1;border-radius:2px;height:4px;min-width:4px}
.phase-bar .phase.done{background:#238636}
.phase-bar .phase.running{background:#d29922;animation:pulse 1s infinite}
.phase-bar .phase.error{background:#da3633}
.phase-bar .phase.waiting{background:#21262d}

/* ── Logs panel ────────────────────────────────────────────── */
.logs-panel{background:#161b22;border:1px solid #21262d;border-radius:8px;margin-bottom:16px;overflow:hidden}
.logs-panel .header{padding:10px 16px;display:flex;justify-content:space-between;cursor:pointer;background:#1c2128;border-bottom:1px solid #21262d}
.logs-panel .header:hover{background:#21262d}
.logs-panel .body{display:none;padding:8px}
.logs-panel .body.open{display:block}
.log-area{background:#0d1117;border-radius:6px;padding:8px;font-family:'SF Mono','Cascadia Code','Fira Code',monospace;font-size:10px;line-height:1.5;color:#8b949e;max-height:200px;overflow-y:auto;white-space:pre-wrap;word-break:break-all}

/* ── Footer ─────────────────────────────────────────────────── */
.footer{padding:24px;text-align:center;font-size:11px;color:#484f58;border-top:1px solid #21262d;margin-top:24px}
.footer a{color:#58a6ff}

/* ── Animations ─────────────────────────────────────────────── */
@keyframes pulse{0%{opacity:1}50%{opacity:.4}100%{opacity:1}}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
.run{animation:fadeIn .3s ease}

/* ── Responsive ─────────────────────────────────────────────── */
@media(max-width:640px){
  .topnav{padding:12px 16px;flex-direction:column;gap:8px;align-items:flex-start}
  .stats{flex-direction:column}
  .stat{min-width:auto}
  .content{padding:12px}
  .run-header{flex-direction:column;align-items:flex-start;gap:4px}
  .run-header .meta{flex-wrap:wrap;gap:6px}
}
</style>
</head>
<body>
<div class="app">
<div class="topnav">
  <h1>
    <span style="color:#58a6ff">⨁</span> Pipeline Manager
    <span>by LINA</span>
  </h1>
  <div class="status">
    <span class="status-dot live" id="statusDot"></span>
    <span id="statusText">Conectado</span>
  </div>
</div>

<div class="content">
  <div class="stats">
    <div class="stat">
      <div class="stat-num" id="totalRuns">—</div>
      <div class="stat-label">Ejecuciones</div>
    </div>
    <div class="stat">
      <div class="stat-num" id="totalSteps">—</div>
      <div class="stat-label">Pasos</div>
    </div>
    <div class="stat">
      <div class="stat-num" id="totalTokens">—</div>
      <div class="stat-label">Tokens totales</div>
    </div>
    <div class="stat">
      <div class="stat-num" id="totalCost" style="color:#d29922">—</div>
      <div class="stat-label">Costo estimado</div>
    </div>
  </div>

  <div class="filter-bar">
    <button class="btn active" data-filter="all" onclick="setFilter('all')">All pipelines</button>
    <button class="btn" data-filter="done" onclick="setFilter('done')">✅ Success</button>
    <button class="btn" data-filter="error" onclick="setFilter('error')">❌ Error</button>
    <input type="search" placeholder="Search pipelines…" id="searchInput" oninput="filterRuns()">
    <button class="btn" onclick="document.getElementById('logsPanel').style.display='block';loadLogs()">📋 Logs</button>
  </div>

  <div id="logsPanel" class="logs-panel" style="display:none">
    <div class="header" onclick="this.nextElementSibling.classList.toggle('open')">
      <span>📋 Event logs</span>
      <span style="color:#8b949e;font-size:11px" id="logCount">0</span>
    </div>
    <div class="body open"><div class="log-area" id="logArea"></div></div>
  </div>

  <div class="timeline" id="timeline"></div>
</div>

<div class="footer">
  Pipeline Manager · LINA · <a href="http://192.168.1.13:9097" target="_blank">Dashboard</a>
</div>
</div>

<script>
let allRuns = [];
let currentFilter = 'all';

function esc(s){return String(s||'').replace(/[&<>]/g,c=>{'&':'&amp;','<':'&lt;','>':'&gt;'}[c])}
function ago(sec){if(!sec)return'';const s=Math.floor((Date.now()/1000-sec));
  if(s<60)return s+'s';if(s<3600)return Math.floor(s/60)+'m';return Math.floor(s/3600)+'h'+Math.floor((s%3600)/60)+'m'}
function cost(tok){return ((tok||0)*3/1e6*2).toFixed(4)}
const ts=(sec)=>{const d=new Date((sec||0)*1000);return d.toLocaleTimeString()}

async function load(){
  try{
    const r=await fetch('/api/runs');
    const d=await r.json();
    allRuns=d.runs||[];
    render();
    updateStats();
    document.getElementById('statusText').textContent=allRuns.length+' ejecuciones';
  }catch(e){document.getElementById('statusText').textContent='❌ error';}
}

function render(){
  const el=document.getElementById('timeline');
  const filtered=filterRuns();
  if(!filtered.length){
    el.innerHTML='<div class="empty-state"><div class="icon">🚀</div><h2>Sin ejecuciones</h2><p>Corré un pipeline con @s_pipelines_bot y aparecerá acá.</p></div>';
    return;
  }
  let h='';
  for(const r of filtered){
    const st=r.status||'waiting';
    const icon=st==='done'?'✅':st==='error'?'❌':'🟡';
    const iconClass=st==='done'?'done':st==='error'?'error':'running';
    const steps=r.steps||[];
    const totalTok=steps.reduce((a,s)=>a+(s.tokens||0),0);
    const totalCost=cost(totalTok);
    const timeStr=ago(r.started_at);
    const stepCount=steps.length;
    let stepsHtml='';
    let phaseHtml='<div class="phase-bar">';
    for(const s of steps){
      const sd=s.status||'waiting';
      const botClass=s.bot||'default';
      const si=sd==='done'?'✅':sd==='error'?'❌':'🟡';
      const sc=sd==='done'?'done':sd==='error'?'error':sd==='running'?'running':'waiting';
      const msgShort=(s.msg||'').slice(0,120);
      stepsHtml+=`<div class="step"><div class="dot ${sc}"></div>`;
      stepsHtml+=`<div class="content"><div class="step-header"><span class="bot-tag ${botClass}">${esc(botClass)}</span>${esc(msgShort)}</div>`;
      if(s.tokens)stepsHtml+=`<div class="step-detail">${s.tokens} tok · $${cost(s.tokens)}</div>`;
      stepsHtml+=`</div><div class="meta"><span class="tok">${s.tokens||0}</span><span class="cost">$${cost(s.tokens)}</span></div></div>`;
      phaseHtml+=`<div class="phase ${sc}"></div>`;
    }
    phaseHtml+='</div>';
    h+=`<div class="run">
      <div class="run-icon ${iconClass}">${icon==='✅'?'✓':icon==='❌'?'✗':'›'}</div>
      <div class="run-header" onclick="this.nextElementSibling.classList.toggle('open')">
        <div class="info"><span class="name">${esc(r.name||'?')} <span class="actor">· ${timeStr}</span></span></div>
        <div class="meta">
          <span class="badge ${st}">${st==='done'?'✅ Success':st==='error'?'❌ Error':'🟡 Running'}</span>
          <span>${stepCount} pasos</span>
          <span>${totalTok} tok</span>
          <span style="color:#d29922">$${totalCost}</span>
        </div>
      </div>
      <div class="run-body open">
        ${phaseHtml}
        <div class="steps">${stepsHtml}</div>
      </div>
      <div class="run-footer">
        <span>🔄 ${ago(r.started_at)} ago</span>
        <span>⚡ ${Math.floor((r.completed_at||0)-(r.started_at||0))}s duration</span>
      </div>
    </div>`;
  }
  el.innerHTML=h;
}

function updateStats(){
  const total=allRuns.length;
  const tSteps=allRuns.reduce((a,r)=>a+(r.steps||[]).length,0);
  const tTok=allRuns.reduce((a,r)=>a+(r.steps||[]).reduce((b,s)=>b+(s.tokens||0),0),0);
  document.getElementById('totalRuns').textContent=total;
  document.getElementById('totalSteps').textContent=tSteps;
  document.getElementById('totalTokens').textContent=tTok.toLocaleString();
  document.getElementById('totalCost').textContent='$'+cost(tTok);
}

function filterRuns(){
  const q=document.getElementById('searchInput').value.toLowerCase();
  const filtered=allRuns.filter(r=>{
    if(currentFilter!=='all' && r.status!==currentFilter)return false;
    if(q && !(r.name||'').toLowerCase().includes(q))return false;
    return true;
  });
  render(); // re-render with filtered
  return filtered;
}

function setFilter(f){
  currentFilter=f;
  document.querySelectorAll('.filter-bar .btn[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter===f));
  filterRuns();
}

function addLog(line,type){
  const el=document.getElementById('logArea');
  const ts=new Date().toLocaleTimeString();
  const c=type==='finish'?'color:#3fb950':type==='error'?'color:#f85149':'color:#8b949e';
  el.innerHTML+=`<div style="${c}"><span style="color:#484f58">[${ts}]</span> ${esc(line)}</div>`;
  el.scrollTop=el.scrollHeight;
  document.getElementById('logCount').textContent=el.children.length;
}

function loadLogs(){
  const el=document.getElementById('logArea');
  if(el.children.length===0){addLog('Dashboard opened','info');}
}

load();
setInterval(load,5000);
</script>
</body>
</html>"""


def load_runs():
    if not os.path.exists(RUNS_FILE):
        return []
    runs = []
    try:
        with open(RUNS_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    runs.append(json.loads(line))
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
        resp = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\nAccess-Control-Allow-Origin: *\r\n\r\n")
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
            resp = (f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\n"
                    "Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n")
            writer.write(resp.encode() + body)
            await writer.drain()
            writer.close()
            return

        if path == "/api/runs":
            runs = load_runs()
            body = json.dumps({"runs": runs}).encode()
            resp = (f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n")
            writer.write(resp.encode() + body)
            await writer.drain()
            writer.close()
            return

    writer.close()


async def main():
    server = await asyncio.start_server(handle_request, "0.0.0.0", PORT)
    print(f"📊 Dashboard inspirado en GitHub: http://0.0.0.0:{PORT}", flush=True)
    print(f"   Celular: http://192.168.1.13:{PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("⏹️ Dashboard detenido")
