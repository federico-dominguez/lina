"""
manager.py — Multi-group pipeline manager.

Spawnea un worker process por grupo definido en groups.yaml.
Cada worker corre pipeline_orchestrator.py --issue <N> --group "<name>"
Los workers se comunican con los mismos 3 agentes (LINA, Cline, Gemma)
pero en grupos de Telegram distintos.
"""

import os, sys, time, json, yaml, signal, subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
GROUPS_FILE = BASE / "groups.yaml"
LOGS_DIR = BASE / "logs"
STATE_DIR = BASE / "state"
SESSIONS_DIR = BASE / "sessions"
WORKERS = {}  # name -> {process, state, issue, logfile}

# ─── Ensure dirs ────────────────────────────────────────────────────────────
for d in [LOGS_DIR, STATE_DIR, SESSIONS_DIR]:
    d.mkdir(exist_ok=True)


def load_groups():
    with open(GROUPS_FILE) as f:
        return yaml.safe_load(f).get("groups", [])


def load_issues():
    """Fetch open issues from repo, return list of (number, title)."""
    r = subprocess.run(
        ["gh", "issue", "list", "--repo", "federico-dominguez/lina",
         "--state", "open", "--json", "number,title,labels",
         "--limit", "30"],
        capture_output=True, text=True, timeout=15
    )
    if r.returncode != 0:
        return []
    return json.loads(r.stdout)


def get_next_issue(current_issues):
    """Find the oldest unassigned issue from the list."""
    assigned = set()
    for w in WORKERS.values():
        if w.get("issue"):
            assigned.add(w["issue"])
    for issue in current_issues:
        if issue["number"] not in assigned:
            return issue
    return None


def spawn_worker(name, session, issue_num):
    """Start a pipeline worker process."""
    logfile = LOGS_DIR / f"{name.lower().replace(' ','_')}.log"
    with open(logfile, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"🚀 Worker {name} iniciado en issue #{issue_num}\n")
        f.write(f"{'='*60}\n")

    p = subprocess.Popen(
        [sys.executable, "-u", str(BASE / "pipeline_orchestrator.py"),
         "--issue", str(issue_num), "--group", name],
        stdout=open(logfile, "a"), stderr=subprocess.STDOUT,
        cwd=BASE.parent
    )
    WORKERS[name] = {
        "process": p,
        "state": "running",
        "issue": issue_num,
        "logfile": logfile,
        "started": time.time(),
    }
    print(f"  ✅ {name}: PID {p.pid}, issue #{issue_num}")
    save_state(name)


def save_state(name=None):
    """Write worker state to JSON file."""
    if name:
        w = WORKERS.get(name)
        if w:
            state = {
                "name": name,
                "pid": w["process"].pid,
                "state": w["state"],
                "issue": w["issue"],
                "running": w["process"].poll() is None,
                "uptime": int(time.time() - w["started"]),
            }
            (STATE_DIR / f"{name.lower().replace(' ','_')}.json").write_text(json.dumps(state, indent=2))

def check_workers(loop=True):
    """Check all workers and spawn new ones if auto_next."""
    while True:
        issues = load_issues()
        for name, w in list(WORKERS.items()):
            ret = w["process"].poll()
            if ret is not None:
                print(f"  {'❌' if ret != 0 else '✅'} {name} terminó (exit={ret})")
                w["state"] = "completed" if ret == 0 else "failed"
                save_state(name)
                # Auto-next: assign new issue
                group_cfg = next((g for g in load_groups() if g["name"] == name), None)
                if group_cfg and group_cfg.get("auto_next", False):
                    next_issue = get_next_issue(issues)
                    if next_issue:
                        print(f"  🔄 {name} → issue #{next_issue['number']}")
                        spawn_worker(name, group_cfg["session"], next_issue["number"])
        if not loop:
            break
        time.sleep(15)


def stop_all():
    """Stop all workers gracefully."""
    print("🛑 Deteniendo todos los workers...")
    for name, w in WORKERS.items():
        if w["process"].poll() is None:
            w["process"].terminate()
            time.sleep(2)
            if w["process"].poll() is None:
                w["process"].kill()
            w["state"] = "stopped"
            save_state(name)
            print(f"  🛑 {name} detenido")

def status():
    """Print status of all workers."""
    print(f"\n📊 Workers: {len(WORKERS)}")
    print(f"{'─'*60}")
    for name, w in WORKERS.items():
        running = w["process"].poll() is None
        uptime = int(time.time() - w["started"]) if running else 0
        icon = "🟢" if running else ("🔴" if w["state"] == "failed" else "⚫")
        print(f"  {icon} {name:15s} | {'RUNNING' if running else 'STOPPED':8s} | "
              f"#{w['issue']} | {uptime}s | {w['state']}")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"

    if cmd == "start":
        groups = load_groups()
        issues = load_issues()
        print(f"🚀 Manager iniciando {len(groups)} grupos...\n")
        
        for g in groups:
            if g["issue"] == "auto":
                issue = get_next_issue(issues)
                if not issue:
                    print(f"  ⚠️ No hay issues disponibles para {g['name']}")
                    continue
            else:
                issue = {"number": int(g["issue"]), "title": f"Issue #{g['issue']}"}
            
            spawn_worker(g["name"], g["session"], issue["number"])
        
        print(f"\n📊 Workers activos: {len(WORKERS)}")
        print("Monitoreando (Ctrl+C para detener)...\n")
        
        try:
            check_workers(loop=True)
        except KeyboardInterrupt:
            print("\n")
            stop_all()

    elif cmd == "stop":
        stop_all()
    elif cmd == "status":
        status()
    else:
        print(f"Uso: {sys.argv[0]} start|stop|status")
