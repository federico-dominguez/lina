#!/usr/bin/env python3
"""pipeline-dashboard.py — Dashboard enfocado en healthchecks."""
import asyncio, json, os, sys, time
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent
RUNS_FILE = "/tmp/pipeline-runs.jsonl"
PORT = 9097
DB_DSN = "postgresql://lina:lina_dev@localhost:5432/lina"

SESSION_PAGE = r"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0">
<title>Sesión · Healthchecks</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0d1117;color:#e6edf3;font-size:14px;line-height:1.5}
.topnav{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid #21262d;background:#161b22;position:sticky;top:0;z-index:10}
.topnav h1{font-size:16px;font-weight:600;color:#f0f6fc;display:flex;align-items:center;gap:8px}
.topnav .back{color:#58a6ff;font-size:13px;padding:4px 10px;border-radius:6px;border:1px solid #21262d;background:#21262d;cursor:pointer;text-decoration:none}
.topnav .back:hover{background:#30363d}
.content{max-width:800px;margin:0 auto;padding:16px}
.chat{display:flex;flex-direction:column;gap:6px;padding-bottom:40px}
.msg{animation:fadeSlide .2s ease}
.label{font-size:11px;color:#8b949e;margin-bottom:2px;font-weight:500}
.bubble{display:inline-block;padding:8px 14px;border-radius:12px;font-size:14px;line-height:1.6;max-width:100%;word-wrap:break-word;white-space:pre-wrap}
.bubble.user{background:#1f6feb22;color:#c9d1d9;border:1px solid #1f6feb33;border-top-left-radius:2px}
.bubble.bot{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-top-right-radius:2px}
.time{font-size:10px;color:#484f58;margin:2px 0 6px}
.thinking-block{background:#0d1117;padding:8px 14px;border-radius:12px;font-size:13px;color:#d29922;font-style:italic;border-left:3px solid #d29922;line-height:1.6;white-space:pre-wrap;font-family:'SF Mono',monospace;margin:2px 0}
.finish-block{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;background:#23863622;border:1px solid #23863644;border-radius:12px;font-size:13px;color:#3fb950;margin:2px 0}
.finish-reason{color:#8b949e;font-size:11px;font-family:'SF Mono',monospace}
.tool-block{background:#0d1117;padding:8px 14px;border-radius:12px;font-size:12px;color:#bc8cff;border-left:3px solid #bc8cff;line-height:1.5;white-space:pre-wrap;overflow-x:auto;font-family:'SF Mono',monospace;margin:2px 0}
.error-block{background:#da363322;color:#f85149;padding:8px 14px;border-radius:12px;font-size:13px;margin:2px 0;border-left:3px solid #f85149}
.empty{text-align:center;padding:60px 20px;color:#484f58}
.loading{text-align:center;padding:60px 20px;color:#8b949e}
@keyframes fadeSlide{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:640px){.topnav{padding:10px 12px;flex-wrap:wrap}.content{padding:12px}.bubble{font-size:13px;padding:6px 10px}}
</style></head><body>
<div class="topnav"><a class="back" href="/">&larr; Dashboard</a><h1 id="pageTitle">📋 Sesión</h1></div>
<div class="content" id="sessionContent"><div class="loading">Cargando...</div></div>
<script>
async function load(){
  const p=window.location.pathname.split('/');
  const agent=decodeURIComponent(p[2]||'lina'),start=p[3]||'0',end=p[4]||'9999999999';
  document.getElementById('pageTitle').textContent='📋 @'+agent+' session';
  try{
    const r=await fetch('/api/session/'+encodeURIComponent(agent)+'/'+start+'/'+end);
    const d=await r.json(),ev=d.events||[];
    const el=document.getElementById('sessionContent');
    if(!ev.length){el.innerHTML='<div class="empty">Sin eventos</div>';return}
    let h='<div class="chat">';
    for(const e of ev){
      const t=e.type||'',ts=e.ts||'',msg=e.msg||'';
      if(t==='user_message'){h+='<div class="msg"><div class="label">\uD83D\uDC41 User</div><div class="bubble user">'+esc(msg)+'</div><div class="time">'+esc(ts)+'</div></div>'}
      else if(t==='thinking'){h+='<div class="msg"><div class="label">\uD83D\uDCAD Thinking</div><div class="thinking-block">'+esc(msg.slice(0,5000))+'</div></div>'}
      else if(t==='text'){h+='<div class="msg"><div class="label">\uD83E\uDD16 '+esc(agent)+'</div><div class="bubble bot">'+esc(msg)+'</div><div class="time">'+esc(ts)+'</div></div>'}
      else if(t==='finish'){h+='<div class="msg"><div class="finish-block">\u2705 Completado <span class="finish-reason">'+esc(msg.slice(0,80))+'</span></div></div>'}
      else if(t==='tool_request'){h+='<div class="msg"><div class="label">\uD83D\uDD27 Tool</div><pre class="tool-block">'+esc(msg.slice(0,500))+'</pre></div>'}
      else if(t==='tool_response'){h+='<div class="msg"><div class="label">\uD83D\uDCE4 Result</div><pre class="tool-block">'+esc(msg.slice(0,500))+'</pre></div>'}
      else if(t==='error'){h+='<div class="msg"><div class="label">\u274C Error</div><div class="error-block">'+esc(msg.slice(0,500))+'</div></div>'}
    }
    h+='</div>';el.innerHTML=h;
  }catch(e){document.getElementById('sessionContent').innerHTML='<div class="empty" style="color:#f85149">\u274C Error</div>'}
}
function esc(s){return String(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'})[c])}
load();
</script></body></html>"""

INDEX = r"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0">
<title>Healthchecks · LINA</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif;background:#0d1117;color:#e6edf3;font-size:14px;line-height:1.5}
.app{max-width:960px;margin:0 auto}
.topnav{display:flex;justify-content:space-between;align-items:center;padding:16px 24px;border-bottom:1px solid #21262d;background:#161b22;flex-wrap:wrap;gap:8px}
.topnav h1{font-size:20px;font-weight:600;color:#f0f6fc;display:flex;align-items:center;gap:8px}
.topnav h1 span{font-weight:400;color:#8b949e;font-size:14px}
.topnav .status{display:flex;align-items:center;gap:6px;font-size:12px;color:#8b949e}
.status-dot{width:8px;height:8px;border-radius:50%;background:#3fb950;box-shadow:0 0 6px #3fb95066}
.content{padding:24px}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:20px}
.stat{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:10px 14px;text-align:left}
.stat-num{font-size:22px;font-weight:600;color:#f0f6fc}
.stat-label{font-size:10px;color:#8b949e;margin-top:1px}
.stat-sub{font-size:9px;color:#484f58;margin-top:2px}
.run{background:#161b22;border:1px solid #21262d;border-radius:8px;margin-bottom:10px;overflow:hidden;transition:all .15s;animation:fadeSlide .25s ease}
.run:hover{border-color:#30363d}
.run-header{display:flex;padding:10px 14px;cursor:pointer;gap:8px;align-items:center}
.run-header:hover{background:#1c2128}
.run-icon{flex-shrink:0;width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700}
.run-icon.done{background:#238636;color:#fff}.run-icon.error{background:#da3633;color:#fff}
.run-name{font-weight:600;font-size:13px;color:#e6edf3;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.run-meta{display:flex;align-items:center;gap:8px;font-size:10px;color:#8b949e;flex-shrink:0}
.run-badge{font-size:9px;padding:1px 5px;border-radius:8px}
.run-badge.done{background:#23863633;color:#3fb950}.run-badge.error{background:#da363333;color:#f85149}
.run-body{transition:max-height .25s ease,opacity .2s;max-height:0;opacity:0;overflow:hidden}
.run-body.open{max-height:3000px;opacity:1}
.run-inner{padding:0 14px 10px}
.run-footer{padding:5px 14px;border-top:1px solid #21262d;font-size:10px;color:#8b949e;display:flex;gap:12px;background:#0d1117;flex-wrap:wrap}
.step{display:flex;align-items:center;padding:5px 0;gap:8px;font-size:12px;cursor:pointer;text-decoration:none;color:inherit}
.step:hover .step-msg{color:#58a6ff}
.step-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.step-dot.done{background:#3fb950}.step-dot.error{background:#f85149}
.step-bot{font-weight:500;min-width:32px;font-size:10px;text-align:right}
.step-bot.cline{color:#f0883e}.step-bot.lina{color:#58a6ff}
.step-msg{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8b949e;font-size:11px}
.step-meta{font-size:9px;color:#484f58}
@keyframes pulse{0%{opacity:1}50%{opacity:.3}100%{opacity:1}}
@keyframes fadeSlide{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:640px){.topnav{padding:12px 16px}.stats{grid-template-columns:repeat(2,1fr);gap:8px}.content{padding:12px}}
</style></head><body>
<div class="app">
<div class="topnav"><h1>🩺 Healthchecks <span>by LINA</span></h1><div class="status"><span class="status-dot"></span><span id="sts">Cargando...</span></div></div>
<div class="content">
  <div class="stats">
    <div class="stat"><div class="stat-num" id="s0">—</div><div class="stat-label">Healthchecks hoy</div><div class="stat-sub" id="s0sub">de 24 posibles</div></div>
    <div class="stat"><div class="stat-num" id="s1">—</div><div class="stat-label">Tasa de éxito</div><div class="stat-sub" id="s1sub">últimas 24h</div></div>
    <div class="stat"><div class="stat-num" id="s2">—</div><div class="stat-label">Último</div><div class="stat-sub" id="s2sub">hace</div></div>
    <div class="stat"><div class="stat-num" id="s3">—</div><div class="stat-label">Próximo</div><div class="stat-sub" id="s3sub">en</div></div>
    <div class="stat"><div class="stat-num" id="s4" style="color:#d29922">—</div><div class="stat-label">Costo hoy</div><div class="stat-sub" id="s4sub">estimado</div></div>
  </div>
  <div class="timeline" id="tl"></div>
</div></div>
<script>
let A=[];
function es(s){return String(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'})[c])}
function ag(s){if(!s)return'';const m=Math.floor((Date.now()/1000-s));if(m<60)return m+'s';if(m<3600)return Math.floor(m/60)+'m';return Math.floor(m/3600)+'h'+Math.floor((m%3600)/60)+'m'}
function ct(t){return'$'+((t||0)*3/1e6*2).toFixed(4)}
function today(){const d=new Date();return d.getFullYear()*10000+(d.getMonth()+1)*100+d.getDate()}
async function ld(){try{const r=await fetch('/api/runs'),d=await r.json();A=(d.runs||[]).filter(r=>(r.name||'').includes('healthcheck'));re();us()}catch(e){}const n=document.getElementById('sts');if(n)n.textContent=A.length+' healthchecks'}
function re(){const el=document.getElementById('tl'),ff=A;if(!ff.length){el.innerHTML='<div class="run" style="padding:30px;text-align:center;color:#484f58">Aún no hay healthchecks hoy</div>';return}
let h='<div style="font-size:11px;color:#484f58;margin-bottom:8px">Últimos healthchecks:</div>';
for(const r of ff){const st=r.status||'done',si=st==='done'?'done':st==='error'?'error':'running',steps=r.steps||[],tTok=steps.reduce((a,s)=>a+(s.tokens||0),0);let sl='';for(const s of steps){const sd=s.status||'done',sc=sd==='done'?'done':sd==='error'?'error':'waiting';sl+='<a class="step" href="/s/'+encodeURIComponent(es(s.bot||''))+'/'+(r.started_at||0)+'/'+(r.completed_at||0)+'"><div class="step-dot '+sc+'"></div><div class="step-bot '+(s.bot||'default')+'">'+es(s.bot||'')+'</div><div class="step-msg">'+es((s.msg||'').slice(0,100))+'</div><div class="step-meta">'+tTok+' tok</div></a>'}h+='<div class="run"><div class="run-header" onclick="this.nextElementSibling.classList.toggle(\'open\')"><div class="run-icon '+si+'">'+(si==='done'?'\u2713':'\u2717')+'</div><span class="run-name">'+es(r.name||'')+' '+ag(r.started_at||0)+' ago</span><div class="run-meta"><span class="run-badge '+si+'">'+(si==='done'?'\u2705 OK':'\u274C Fail')+'</span><span>'+tTok+' tok</span></div></div><div class="run-body"><div class="run-inner">'+sl+'</div><div class="run-footer"><span>\uD83D\uDD04 '+ag(r.started_at||0)+' ago</span><span>\u26A1 '+Math.floor((r.completed_at||0)-(r.started_at||0))+'s</span></div></div></div>'}el.innerHTML=h}
function us(){const ok=A.filter(r=>r.status==='done').length,total=A.length,rate=total?Math.round(ok/total*100):0,tok=A.reduce((a,r)=>a+(r.steps||[]).reduce((b,s)=>b+(s.tokens||0),0),0);document.getElementById('s0').textContent=total;document.getElementById('s1').textContent=rate+'%';document.getElementById('s1').style.color=rate>=80?'#3fb950':rate>=50?'#d29922':'#f85149';if(A.length){const last=A[0];document.getElementById('s2').textContent=ag(last.started_at||0);const dur=Math.floor((last.completed_at||0)-(last.started_at||0));document.getElementById('s2sub').textContent=dur+'s'+(last.status==='done'?' \u2705':' \u274C')}document.getElementById('s4').textContent=ct(tok)}
ld();setInterval(ld,10000);
</script></body></html>"""

def load_runs():
    if not os.path.exists(RUNS_FILE): return []
    runs = []
    try:
        with open(RUNS_FILE) as f:
            for line in f:
                line = line.strip()
                if line: runs.append(json.loads(line))
        runs.sort(key=lambda r: r.get("completed_at", 0), reverse=True)
        return runs[:50]
    except Exception: return []

async def query_session_events(agent, start_ts, end_ts):
    try:
        import asyncpg
        conn = await asyncpg.connect(DB_DSN, timeout=5)
        try:
            rows = await conn.fetch(
                "SELECT event_type, created_at, payload FROM session_events WHERE agent=$1 "
                "AND created_at>=to_timestamp($2)-INTERVAL'30s' AND created_at<=to_timestamp($3)+INTERVAL'50min' "
                "ORDER BY id ASC LIMIT 500", agent, start_ts, end_ts)
            if not rows and start_ts < 1800000000:
                rows = await conn.fetch(
                    "SELECT event_type, created_at, payload FROM session_events WHERE agent=$1 ORDER BY id DESC LIMIT 500", agent)
                rows = list(reversed(rows))
            events = []
            for r in rows:
                p = json.loads(r["payload"]) if isinstance(r["payload"], str) else (r["payload"] or {})
                text = p.get("text","") or p.get("reason","") or p.get("tool","") or ""
                if events and r["event_type"]==events[-1]["type"] and r["event_type"] in ("thinking","text"):
                    if len(events[-1]["msg"]) < 2000: events[-1]["msg"] += text
                else:
                    events.append({"ts":r["created_at"].strftime("%H:%M:%S")if r["created_at"] else"","type":r["event_type"],"msg":text[:2000]})
            return events
        finally: await conn.close()
    except Exception: return []

async def handle(reader, writer):
    try: data = await asyncio.wait_for(reader.read(65536), timeout=10)
    except asyncio.TimeoutError: writer.close(); return
    try: method, path, _ = data.decode("utf-8",errors="replace").split("\r\n")[0].split(" ", 2)
    except ValueError: writer.close(); return
    if method != "GET": writer.close(); return
    if path.startswith("/s/"):
        b=SESSION_PAGE.encode("utf-8")
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(b)}\r\nAccess-Control-Allow-Origin: *\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n".encode()+b)
        await writer.drain(); writer.close(); return
    if path=="/":
        b=INDEX.encode("utf-8")
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(b)}\r\nAccess-Control-Allow-Origin: *\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n".encode()+b)
        await writer.drain(); writer.close(); return
    if path=="/api/runs":
        runs=load_runs()
        b=json.dumps({"runs":runs}).encode()
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: {len(b)}\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n".encode()+b)
        await writer.drain(); writer.close(); return
    if path.startswith("/api/session/"):
        p=path.split("/"); a=p[3]if len(p)>3 else"lina";s=float(p[4])if len(p)>4 and p[4]else 0;e=float(p[5])if len(p)>5 and p[5]else time.time()
        ev=await query_session_events(a,s,e)
        b=json.dumps({"events":ev,"agent":a}).encode()
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: {len(b)}\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n".encode()+b)
        await writer.drain(); writer.close(); return
    writer.close()

async def main():
    s=await asyncio.start_server(handle,"0.0.0.0",PORT)
    print(f"Healthchecks: http://0.0.0.0:{PORT}",flush=True);print(f"  Celular: http://192.168.1.13:{PORT}",flush=True)
    async with s: await s.serve_forever()
if __name__=="__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: print("Detenido")
