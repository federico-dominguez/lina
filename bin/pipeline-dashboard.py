#!/usr/bin/env python3
"""
pipeline-dashboard.py — Dashboard estilo GitHub Actions para el Pipeline Manager.

Design System: GitHub Primer (#0d1117, #161b22, #21262d, #c9d1d9, #58a6ff)
Timeline de ejecuciones con DAG de pasos, costos, y logs por pipeline.

Puerto: 9097
"""

import asyncio, json, os, subprocess, sys, time, re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_DSN = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lana")
PORT = 9097
RUNS_FILE = "/tmp/pipeline-runs.jsonl"

HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Pipeline Dashboard · LINA</title>
<style>
/* ── GitHub Primer Design System ─────────────────────────── */
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif;background:#0d1117;color:#e6edf3;font-size:14px;line-height:1.5}
a{color:#58a6ff;text-decoration:none}
.app{max-width:960px;margin:0 auto}
.topnav{display:flex;justify-content:space-between;align-items:center;padding:16px 24px;border-bottom:1px solid #21262d;background:#161b22;flex-wrap:wrap;gap:8px}
.topnav h1{font-size:20px;font-weight:600;color:#f0f6fc;display:flex;align-items:center;gap:8px}
.topnav h1 span{font-weight:400;color:#8b949e;font-size:14px}
.topnav .status{display:flex;align-items:center;gap:6px;font-size:12px;color:#8b949e}
.status-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.status-dot.live{background:#3fb950;box-shadow:0 0 6px #3fb95066}
.content{padding:24px}
.empty-state{text-align:center;padding:80px 20px;color:#484f58}
.empty-state .icon{font-size:48px;margin-bottom:16px}

/* ── Stats ───────────────────────────────────────────────── */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}
.stat{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 16px;transition:opacity .2s}
.stat-num{font-size:24px;font-weight:600;color:#f0f6fc}
.stat-label{font-size:11px;color:#8b949e;margin-top:2px}

/* ── Filter bar ──────────────────────────────────────────── */
.filters{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;align-items:center}
.filters .btn{padding:5px 12px;font-size:12px;border-radius:6px;border:1px solid #21262d;background:#21262d;color:#c9d1d9;cursor:pointer;transition:all .15s;white-space:nowrap}
.filters .btn:hover{background:#30363d}
.filters .btn.active{background:#1f6feb;border-color:#1f6feb;color:#fff}
.filters .btn.run{background:#238636;border-color:#238636;color:#fff;display:flex;align-items:center;gap:4px}
.filters .btn.run:hover{background:#2ea043}
.filters input{background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:5px 10px;color:#c9d1d9;font-size:12px;flex:1;min-width:120px}
.filters input:focus{border-color:#58a6ff;outline:none}

/* ── Timeline ────────────────────────────────────────────── */
.timeline{position:relative}
.timeline:empty::after{content:'Cargando...';display:block;text-align:center;padding:40px;color:#484f58}
.run{background:#161b22;border:1px solid #21262d;border-radius:8px;margin-bottom:12px;overflow:hidden;transition:all .15s}
.run:hover{border-color:#30363d}
.run-header{display:flex;justify-content:space-between;align-items:center;padding:10px 14px 10px 14px;cursor:pointer;gap:8px}
.run-header:hover{background:#1c2128}
.run-header .left{display:flex;align-items:center;gap:8px;min-width:0;flex:1}
.run-icon{flex-shrink:0;width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700}
.run-icon.done{background:#238636;color:#fff}
.run-icon.error{background:#da3633;color:#fff}
.run-icon.running{background:#d29922;color:#fff}
.run-name{font-weight:600;font-size:14px;color:#e6edf3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.run-time{font-size:11px;color:#8b949e;flex-shrink:0}
.run-header .right{display:flex;align-items:center;gap:10px;flex-shrink:0;font-size:11px;color:#8b949e}
.run-badge{font-size:10px;padding:1px 6px;border-radius:10px}
.run-badge.done{background:#23863633;color:#3fb950}
.run-badge.error{background:#da363333;color:#f85149}
.run-badge.running{background:#d2992233;color:#d29922}
.run-body{transition:max-height .25s ease,opacity .2s;max-height:0;opacity:0;overflow:hidden}
.run-body.open{max-height:2000px;opacity:1}
.run-inner{padding:0 14px 10px 14px}
.run-footer{padding:6px 14px;border-top:1px solid #21262d;font-size:11px;color:#8b949e;display:flex;gap:16px;background:#0d1117;flex-wrap:wrap}

/* ── Step DAG ────────────────────────────────────────────── */
.phase-bar{display:flex;gap:2px;margin:4px 0 8px 0;height:4px}
.phase{flex:1;border-radius:2px;height:4px;transition:background .3s}
.phase.done{background:#238636}
.phase.running{background:#d29922;animation:pulse 1s infinite}
.phase.error{background:#da3633}
.phase.waiting{background:#21262d}
.step-list{border:1px solid #21262d;border-radius:6px;overflow:hidden}
.step{display:flex;align-items:center;padding:6px 10px;gap:8px;border-bottom:1px solid #21262d;transition:background .15s;font-size:12px}
.step:last-child{border-bottom:none}
.step:hover{background:#1c2128}
.step-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.step-dot.done{background:#3fb950}
.step-dot.error{background:#f85149}
.step-dot.running{background:#d29922;animation:pulse 1s infinite}
.step-dot.waiting{background:#484f58}
.step-bot{font-weight:500;min-width:36px;font-size:11px;text-align:right}
.step-bot.lina{color:#58a6ff}
.step-bot.cline{color:#f0883e}
.step-bot.goose{color:#3fb950}
.step-msg{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8b949e;font-size:11px}
.step-meta{font-size:10px;color:#484f58;display:flex;gap:4px;flex-shrink:0}
.step-meta .tok{background:#21262d;padding:0 4px;border-radius:3px;font-family:'SF Mono',monospace}

/* ── Logs modal ──────────────────────────────────────────── */
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:#00000088;z-index:100;justify-content:center;align-items:center;padding:16px}
.modal-overlay.open{display:flex}
.modal{background:#161b22;border:1px solid #30363d;border-radius:12px;max-width:700px;width:100%;max-height:80vh;display:flex;flex-direction:column}
.modal-header{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;border-bottom:1px solid #21262d}
.modal-header h2{font-size:16px;font-weight:600}
.modal-close{background:none;border:none;color:#8b949e;font-size:20px;cursor:pointer;padding:0 4px}
.modal-close:hover{color:#f0f6fc}
.modal-body{overflow-y:auto;padding:12px 16px;font-family:'SF Mono','Cascadia Code','Fira Code',monospace;font-size:11px;line-height:1.6;color:#8b949e;max-height:60vh}
.modal-body .log-line{padding:2px 0}
.modal-body .log-line.finish{color:#3fb950}
.modal-body .log-line.error{color:#f85149}
.modal-body .log-line.thinking{color:#d29922}
.modal-body .log-line.text{color:#c9d1d9}
.modal-loading{padding:40px;text-align:center;color:#8b949e}

/* ── Animations ──────────────────────────────────────────── */
@keyframes pulse{0%{opacity:1}50%{opacity:.3}100%{opacity:1}}
@keyframes fadeSlide{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.run{animation:fadeSlide .25s ease}

/* ── Responsive ─────────────────────────────────────────────── */
@media(max-width:640px){
  .topnav{padding:12px 16px}
  .stats{grid-template-columns:repeat(2,1fr);gap:8px}
  .stat{padding:10px 12px}
  .stat-num{font-size:20px}
  .content{padding:12px}
  .filters .btn{font-size:11px;padding:4px 8px}
  .run-header{flex-wrap:wrap}
  .run-header .right{width:100%;justify-content:flex-start;gap:8px}
}
</style>
</head>
<body>
<div class="app">
<div class="topnav">
  <h1>⨁ Pipeline Manager <span>by LINA</span></h1>
  <div class="status">
    <span class="status-dot live" id="statusDot"></span>
    <span id="statusText">Conectando...</span>
  </div>
</div>
<div class="content">
  <div class="stats" id="stats">
    <div class="stat"><div class="stat-num" id="totalRuns">—</div><div class="stat-label">Ejecuciones</div></div>
    <div class="stat"><div class="stat-num" id="totalSteps">—</div><div class="stat-label">Pasos</div></div>
    <div class="stat"><div class="stat-num" id="totalTokens">—</div><div class="stat-label">Tokens</div></div>
    <div class="stat"><div class="stat-num" id="totalCost" style="color:#d29922">—</div><div class="stat-label">Costo</div></div>
  </div>
  <div class="filters" id="filters">
    <button class="btn active" data-f="all" onclick="setF('all')">All</button>
    <button class="btn" data-f="done" onclick="setF('done')">✅ Success</button>
    <button class="btn" data-f="error" onclick="setF('error')">❌ Error</button>
    <input type="search" placeholder="Buscar pipeline..." id="q" oninput="render()">
    <button class="btn" onclick="document.getElementById('allLogs').style.display='flex';popAllLogs()">📋 Logs</button>
  </div>
  <div class="timeline" id="tl"></div>
</div>
</div>

<!-- Modal: Logs de una run -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <div class="modal-header">
      <h2 id="modalTitle">📋 Logs</h2>
      <button class="modal-close" onclick="closeModal()">&#x2715;</button>
    </div>
    <div class="modal-body" id="modalBody"><div class="modal-loading">Cargando...</div></div>
  </div>
</div>

<!-- Modal: Logs globales -->
<div class="modal-overlay" id="allLogs">
  <div class="modal">
    <div class="modal-header">
      <h2>📋 Event Logs</h2>
      <button class="modal-close" onclick="document.getElementById('allLogs').style.display='none'">&#x2715;</button>
    </div>
    <div class="modal-body" id="allLogsBody"><div class="modal-loading">Sin logs aún</div></div>
  </div>
</div>

<script>
let allRuns=[],filter='all',logs=[];

function esc(s){return String(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'})[c])}
function ago(s){if(!s)return'';const m=Math.floor((Date.now()/1000-s));if(m<60)return m+'s';if(m<3600)return Math.floor(m/60)+'m'+((m%60)?(m%60)+'s':'');return Math.floor(m/3600)+'h'+Math.floor((m%3600)/60)+'m'}
function cost(t){return '$'+((t||0)*3/1e6*2).toFixed(4)}
function tid(r){return 'r_'+((r.name||'')+'_'+(r.started_at||0)).replace(/[^a-z0-9]/gi,'_')}

function getF(){
  const q=(document.getElementById('q')||{}).value||'';
  return allRuns.filter(r=>{
    if(filter!=='all'&&r.status!==filter)return false;
    if(q&&!(r.name||'').toLowerCase().includes(q.toLowerCase()))return false;
    return true;
  });
}

function setF(f){
  filter=f;
  document.querySelectorAll('.filters .btn[data-f]').forEach(b=>b.classList.toggle('active',b.dataset.f===f));
  render();
}

async function load(){
  try{
    const r=await fetch('/api/runs'),d=await r.json();
    const nu=d.runs||[];
    // Smart update: only re-render if data changed
    const oldKey=allRuns.map(r=>r.name+'_'+r.started_at).join(',');
    const newKey=nu.map(r=>r.name+'_'+r.started_at).join(',');
    if(oldKey!==newKey){allRuns=nu;render();updStats();}
    const n=document.getElementById('statusText');
    if(n)n.textContent=allRuns.length+' ejecuciones';
  }catch(e){const n=document.getElementById('statusText');if(n)n.textContent='❌ error';}
}

function render(){
  const el=document.getElementById('tl'),ff=getF();
  if(!ff.length){
    el.innerHTML='<div class="empty-state"><div style="font-size:40px;margin-bottom:12px">🚀</div>Sin ejecuciones</div>';
    return;
  }
  let h='';
  for(const r of ff){
    const st=r.status||'waiting',si=st==='done'?'done':st==='error'?'error':'running';
    const steps=r.steps||[];
    const tTok=steps.reduce((a,s)=>a+(s.tokens||0),0);
    let ph='<div class="phase-bar">';
    let sl='<div class="step-list">';
    for(const s of steps){
      const sd=s.status||'waiting',sc=sd==='done'?'done':sd==='error'?'error':sd==='running'?'running':'waiting';
      ph+=`<div class="phase ${sc}"></div>`;
      sl+=`<div class="step"><div class="step-dot ${sc}"></div><div class="step-bot ${s.bot||'default'}">${esc(s.bot||'')}</div><div class="step-msg">${esc((s.msg||'').slice(0,140))}</div><div class="step-meta"><span class="tok">${s.tokens||0}</span><span>${cost(s.tokens)}</span></div></div>`;
    }
    ph+='</div>';sl+='</div>';
    const rid=tid(r);
    h+=`<div class="run">
      <div class="run-header" onclick="tog('${rid}')">
        <div class="left"><div class="run-icon ${si}">${si==='done'?'✓':si==='error'?'✗':'⏳'}</div><span class="run-name">${esc(r.name||'')}</span></div>
        <div class="right"><span class="run-badge ${si}">${si==='done'?'✅ Success':si==='error'?'❌ Error':'🟡 Run'}</span><span>${steps.length} pasos</span><span>${tTok} tok</span><span style="color:#d29922">${cost(tTok)}</span></div>
      </div>
      <div class="run-body" id="${rid}">
        <div class="run-inner">${ph}${sl}</div>
        <div class="run-footer"><span>🔄 ${ago(r.started_at||0)} ago</span><span>⚡ ${Math.floor((r.completed_at||0)-(r.started_at||0))}s</span><button class="btn" style="background:#21262d;color:#c9d1d9;border:none;padding:1px 6px;border-radius:4px;cursor:pointer;font-size:10px" onclick="viewLogs('${rid}','${esc(r.name||'')}')">📋 Ver logs</button></div>
      </div>
    </div>`;
  }
  el.innerHTML=h;
}

function updStats(){
  const t=allRuns.length,st=allRuns.reduce((a,r)=>a+(r.steps||[]).length,0);
  const tk=allRuns.reduce((a,r)=>a+(r.steps||[]).reduce((b,s)=>b+(s.tokens||0),0),0);
  document.getElementById('totalRuns').textContent=t;
  document.getElementById('totalSteps').textContent=st;
  document.getElementById('totalTokens').textContent=tk.toLocaleString();
  document.getElementById('totalCost').textContent=cost(tk);
}

function tog(id){
  const el=document.getElementById(id);
  if(!el)return;
  el.classList.toggle('open');
}

async function viewLogs(rid,name){
  document.getElementById('modal').style.display='flex';
  document.getElementById('modalTitle').textContent='📋 '+esc(name||'Logs');
  document.getElementById('modalBody').innerHTML='<div class="modal-loading">Cargando logs...</div>';
  try{
    const r=await fetch('/api/logs/'+encodeURIComponent(rid));
    const d=await r.json();
    const lines=d.logs||[];
    let h=lines.length?'':'<div style="text-align:center;padding:30px;color:#484f58">Sin logs disponibles</div>';
    for(const l of lines){
      const tc=l.type||'info';
      const tClass=tc==='finish'?'finish':tc==='error'?'error':tc==='thinking'?'thinking':tc==='text'?'text':'';
      h+=`<div class="log-line ${tClass}"><span style="color:#484f58">[${esc(l.ts||'')}]</span> ${esc(l.msg||'')}</div>`;
    }
    document.getElementById('modalBody').innerHTML=h;
  }catch(e){
    document.getElementById('modalBody').innerHTML='<div style="text-align:center;padding:30px;color:#f85149">❌ Error cargando logs</div>';
  }
}

function closeModal(){
  document.getElementById('modal').style.display='none';
  document.querySelectorAll('.modal-overlay').forEach(m=>m.style.display='none');
}

function popAllLogs(){
  document.getElementById('allLogsBody').innerHTML='<div class="modal-loading">Cargando...</div>';
  fetch('/api/logs').then(r=>r.json()).then(d=>{
    const lines=d.logs||[];
    let h=lines.length?'':'<div style="text-align:center;padding:30px;color:#484f58">Sin eventos recientes</div>';
    for(const l of lines){
      const tc=l.type||'info',tClass=tc==='finish'?'finish':tc==='error'?'error':tc==='thinking'?'thinking':tc==='text'?'text':'';
      h+=`<div class="log-line ${tClass}"><span style="color:#484f58">[${esc(l.ts||'')}]</span> ${esc(l.msg||'')}</div>`;
    }
    document.getElementById('allLogsBody').innerHTML=h;
  }).catch(()=>document.getElementById('allLogsBody').innerHTML='<div style="text-align:center;padding:30px;color:#f85149">❌ Error</div>');
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


def read_logs(limit=50):
    import random
    # Simula logs para cada run — en realidad viene de session_events
    logs = []
    try:
        import asyncpg
        conn = asyncio.run(asyncpg.connect(DB_DSN, timeout=3))
        rows = asyncio.run(conn.fetch(
            "SELECT agent, event_type, created_at, payload FROM session_events "
            "WHERE created_at > NOW() - INTERVAL '2 hours' "
            "ORDER BY id DESC LIMIT $1", limit
        ))
        asyncio.run(conn.close())
        for r in rows:
            p = json.loads(r["payload"]) if isinstance(r["payload"], str) else (r["payload"] or {})
            logs.append({
                "ts": r["created_at"].strftime("%H:%M:%S") if r["created_at"] else "",
                "type": r["event_type"],
                "msg": f"[{r['agent']}] {p.get('text','') or p.get('reason','') or ''}"[:200],
            })
    except Exception:
        pass
    return logs


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

    if method != "GET":
        writer.close()
        return

    if path == "/":
        body = HTML.encode("utf-8")
        resp = (f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\n"
                "Access-Control-Allow-Origin: *\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n")
        writer.write(resp.encode() + body)
        await writer.drain()
        writer.close()
        return

    if path == "/api/runs":
        runs = load_runs()
        body = json.dumps({"runs": runs}).encode()
        resp = (f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n"
                f"Content-Length: {len(body)}\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n")
        writer.write(resp.encode() + body)
        await writer.drain()
        writer.close()
        return

    if path.startswith("/api/logs/"):
        # Logs de una run específica (por ahora devuelve logs generales filtrados)
        logs = read_logs(30)
        body = json.dumps({"logs": logs}).encode()
        resp = (f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n"
                f"Content-Length: {len(body)}\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n")
        writer.write(resp.encode() + body)
        await writer.drain()
        writer.close()
        return

    if path == "/api/logs":
        logs = read_logs(50)
        body = json.dumps({"logs": logs}).encode()
        resp = (f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n"
                f"Content-Length: {len(body)}\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n")
        writer.write(resp.encode() + body)
        await writer.drain()
        writer.close()
        return

    writer.close()


async def main():
    server = await asyncio.start_server(handle_request, "0.0.0.0", PORT)
    print(f"📊 Dashboard: http://0.0.0.0:{PORT}", flush=True)
    print(f"   Celular: http://192.168.1.13:{PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("⏹️ Dashboard detenido")
