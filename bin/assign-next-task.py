#!/usr/bin/env python3
"""assign-next-task.py — Lee roadmap.yaml y asigna la siguiente tarea a Cline."""
import yaml, subprocess, sys, os, psycopg2
from datetime import datetime, timezone

ROADMAP = "/home/user/lina/roadmap.yaml"
CLINE_SEND = "/home/user/lina/bin/cline-send.py"
STATE_FILE = "/tmp/assign-next-task.state"
DONE_MARKER = STATE_FILE + ".done"
ENV_FILE = "/home/user/lina/tests/e2e/telegram/.env"
DB = dict(host='lina-db', port=5432, user='lina', password='lina_dev', dbname='lina')
FINISH_WORDS = ['✅', 'completado', 'implementado', 'mergeado', 'hecho', 'terminado', 'cerrado', 'finish', 'merged', 'taggeado']

def load_env(env_path):
    if not os.path.exists(env_path): return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip().strip('"\' '))

def load_roadmap():
    with open(ROADMAP) as f:
        return yaml.safe_load(f)

def is_task_completed():
    if os.path.exists(DONE_MARKER):
        print('🟢 Marker .done encontrado'); return True
    try:
        conn = psycopg2.connect(**DB); cur = conn.cursor()
        cur.execute("SELECT content FROM session_messages WHERE session_id = '20260601_3' AND role = 'assistant' ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        cur.execute("SELECT MAX(created_at) FROM cline_logs WHERE content ILIKE '%finish%'")
        last_finish = cur.fetchone()[0]
        cur.close(); conn.close()
        if row:
            msg = row[0].lower()
            if any(w in msg for w in FINISH_WORDS):
                print('🟢 Mensaje de Cline indica completado'); return True
        return False
    except Exception as e:
        print(f'⚠️ Error: {e}'); return False

def find_next_task(roadmap):
    # 1. next_actions con dependencias satisfechas
    completed_ms = {m["id"] for m in roadmap.get("milestones", []) if m["status"] == "completado"}
    for action in roadmap.get("next_actions", []):
        deps_ok = all(d in completed_ms for d in action.get("depends_on", []))
        if deps_ok:
            return {"desc": f"{action['id']}: {action['title']}", "type": "next_action", "action": f"Implementar {action['id']}: {action['title']}"}
    # 2. Transversales CI/CD
    for t in roadmap.get("transversal", []):
        if t["status"] == "pendiente":
            return {"desc": f"#{t['id']}: {t['title']}", "type": "ci/cd", "action": f"Implementar #{t['id']}: {t['title']}"}
    # 3. Milestones pendientes con issues abiertos
    for ms in roadmap.get("milestones", []):
        if ms["status"] in ("en_curso", "mergeado_parcial", "pendiente"):
            for issue in ms.get("issues", []):
                if issue["status"] == "abierto":
                    return {"desc": f"[{ms['id']}] #{issue['id']}: {issue['title']}", "type": "issue", "action": f"Implementar #{issue['id']}: {issue['title']}"}
    return None

def send_to_cline(message):
    load_env(ENV_FILE)
    result = subprocess.run(["python3", CLINE_SEND, message], capture_output=True, text=True, timeout=30)
    print(result.stdout)
    if result.stderr: print(f"⚠️ stderr: {result.stderr[:200]}")
    return result.returncode == 0

def main():
    old_done = os.path.exists(DONE_MARKER)
    if old_done:
        os.remove(DONE_MARKER)
        if os.path.exists(STATE_FILE + '.reminded'): os.remove(STATE_FILE + '.reminded')

    task_completed = old_done or is_task_completed()
    task = find_next_task(load_roadmap())
    if not task:
        print("✅ No hay tareas pendientes"); sys.exit(0)

    last_task = ""
    try:
        with open(STATE_FILE) as f: last_task = f.read().strip()
    except: pass

    if not task_completed:
        print(f"⏸️  Cline aún trabaja en: {last_task or 'su tarea'}"); sys.exit(0)
    if task["desc"] == last_task:
        print(f"🔄 Tarea completada: {last_task}\n   State limpiado. Próximo scheduler asignará la siguiente.")
        os.remove(STATE_FILE)
        sys.exit(0)

    print(f"🎯 Asignando nueva tarea: {task['desc']}")
    msg = f"🎀 Tarea asignada: {task['action']}. Cuando termines, ejecutá finish. 🚀"
    if send_to_cline(msg):
        with open(STATE_FILE, "w") as f: f.write(task["desc"])
        print(f"✅ Tarea asignada: {task['desc']}")
    else:
        print("❌ Error al enviar mensaje"); sys.exit(1)

if __name__ == "__main__":
    main()
