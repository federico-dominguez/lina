#!/usr/bin/env python3
"""
Dashboard web para LINA y CLINE.
Muestra sesiones activas, estado y órdenes recientes.
Corre en http://localhost:8080
"""

import json
import ssl
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen, Request
import asyncpg
import os

DB_URL = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")
LINA_URL = "https://localhost:3000/status"
CLINE_URL = "https://localhost:3001/status"
SECRET = os.environ.get("GOOSE_SERVER__SECRET_KEY", "cf5787c8fe1f97d14c2bec888013d2f46f22b0004ccfc50c2fcd224b2723e910")
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LINA Dashboard</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: system-ui, -apple-system, sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }
h1 {{ color: #58a6ff; margin-bottom: 20px; font-size: 1.5em; }
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
.card h2 {{ color: #f78166; font-size: 1.1em; margin-bottom: 10px; }
.agent {{ display: flex; align-items: center; gap: 8px; }
.dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.dot.online {{ background: #3fb950; }
.dot.offline {{ background: #f85149; }
table {{ width: 100%; border-collapse: collapse; font-size: 0.9em; margin-top: 8px; }
th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #21262d; }
th {{ color: #8b949e; font-weight: 600; }
.status-pending {{ color: #d2991d; }
.status-running {{ color: #58a6ff; }
.status-completed {{ color: #3fb950; }
.metrics {{ display: flex; gap: 16px; margin-top: 8px; flex-wrap: wrap; }
.metric {{ background: #0d1117; padding: 8px 12px; border-radius: 6px; }
.metric .val {{ font-size: 1.2em; font-weight: bold; color: #58a6ff; }
.metric .lbl {{ font-size: 0.75em; color: #8b949e; }
.http-error {{ color: #f85149; font-style: italic; }
.refresh {{ color: #8b949e; font-size: 0.8em; float: right; }
@media (max-width: 768px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<h1>🖥️ LINA Dashboard</h1>
<div class="grid">
<div class="card">
<h2>🩷 LINA (goosed:3000)</h2>
<div class="agent"><span class="dot {lina_online}"></span> {lina_status}</div>
<div class="metrics">
<div class="metric"><div class="val">{lina_sessions}</div><div class="lbl">sesiones</div></div>
</div>
</div>
<div class="card">
<h2>🤖 CLINE (goosed:3001)</h2>
<div class="agent"><span class="dot {cline_online}"></span> {cline_status}</div>
<div class="metrics">
<div class="metric"><div class="val">{cline_sessions}</div><div class="lbl">sesiones</div></div>
</div>
</div>
<div class="card" style="grid-column: 1/-1">
<h2>📋 Órdenes recientes</h2>
<table>
<tr><th>ID</th><th>Status</th><th>Orden</th><th>Respuesta</th><th>Creada</th><th>Dur (s)</th></tr>
{orders_rows}
</table>
</div>
</div>
<div class="refresh">Auto-refresh 10s | {now}</div>
<script>setTimeout(()=>location.reload(),10000);</script>
</body>
</html>"""


def check_goosed(url):
    try:
        req = Request(url, headers={"x-secret-key": SECRET})
        resp = urlopen(req, context=SSL_CTX, timeout=3)
        data = json.loads(resp.read())
        return True, data.get("session_count", "?")
    except Exception as e:
        return False, str(e)[:80]


async def get_orders():
    try:
        conn = await asyncpg.connect(DB_URL, timeout=3)
        rows = await conn.fetch(
            "SELECT id, status, substring(command,1,60) as cmd, "
            "substring(response,1,60) as resp, created_at, "
            "EXTRACT(EPOCH FROM (completed_at - started_at))::int as dur "
            "FROM cline_commands ORDER BY id DESC LIMIT 15"
        )
        await conn.close()
        return rows
    except Exception:
        return []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return

        lina_ok, lina_sessions = check_goosed(LINA_URL)
        cline_ok, cline_sessions = check_goosed(CLINE_URL)

        import asyncio
        orders = asyncio.run(get_orders())

        now = __import__('datetime').datetime.now().strftime("%H:%M:%S")

        rows_html = ""
        for r in orders:
            rows_html += (
                f'<tr><td>{r["id"]}</td>'
                f'<td class="status-{r["status"]}">{r["status"]}</td>'
                f'<td>{r["cmd"] or ""}</td>'
                f'<td>{r["resp"] or ""}</td>'
                f'<td>{r["created_at"].strftime("%H:%M") if r["created_at"] else ""}</td>'
                f'<td>{r["dur"] or ""}</td></tr>'
            )

        page = HTML.format(
            lina_online="online" if lina_ok else "offline",
            lina_status=f"Online ({lina_sessions})" if lina_ok else f"Offline: {lina_sessions}",
            lina_sessions=lina_sessions if lina_ok else "?",
            cline_online="online" if cline_ok else "offline",
            cline_status=f"Online ({cline_sessions})" if cline_ok else f"Offline: {cline_sessions}",
            cline_sessions=cline_sessions if cline_ok else "?",
            orders_rows=rows_html or '<tr><td colspan="6">Sin órdenes</td></tr>',
            now=now,
        )

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode())


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
