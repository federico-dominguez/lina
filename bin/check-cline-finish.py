#!/usr/bin/env python3
"""
check-cline-finish.py — Cada 5 min: revisa si Cline completó tarea sin finish.
Si detecta completion sin finish → marca tarea como completada y SIGUE.
Ya no spamea a Cline.
"""
import psycopg2, subprocess, os, sys
from datetime import datetime, timezone

DB = dict(host='lina-db', port=5432, user='lina', password='lina_dev', dbname='lina')
ENV_FILE = '/home/user/lina/tests/e2e/telegram/.env'
CLINE_SEND = '/home/user/lina/bin/cline-send.py'
SESION_CLINE = '20260601_3'
SESION_LINA = '20260601_1'
STATE_FILE = '/tmp/assign-next-task.state'
FINISH_WORDS = ['✅', 'completado', 'implementado', 'mergeado', 'hecho', 'terminado', 'cerrado', 'finish']

def load_env():
    if not os.path.exists(ENV_FILE):
        return False
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip().strip('"\' '))
    return True

def main():
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, content, created_at FROM session_messages
        WHERE session_id = %s AND role = 'assistant'
        ORDER BY id DESC LIMIT 1
    """, (SESION_CLINE,))
    msg = cur.fetchone()
    if not msg:
        print('OK — sin mensajes')
        cur.close(); conn.close(); return

    msg_id, msg_content, msg_time = msg
    msg_lower = msg_content.lower()
    is_done = any(w in msg_lower for w in FINISH_WORDS)
    cur.execute("""
        SELECT MAX(created_at) FROM cline_logs WHERE content ILIKE '%finish%'
    """)
    last_finish = cur.fetchone()[0]
    has_finish_after_msg = last_finish and msg_time and last_finish > msg_time

    current_task = ""
    try:
        with open(STATE_FILE) as f:
            current_task = f.read().strip()
    except:
        pass

    if is_done:
        if has_finish_after_msg:
            print(f'OK — #{msg_id}: finish ejecutado tras completar ✅')
        else:
            print(f'🟡 #{msg_id}: completado sin finish')

        # Marcar done + notificar a LINA
        with open(STATE_FILE + '.done', 'w') as f:
            f.write(f'DONE:{datetime.now(timezone.utc).isoformat()}')
        notif_key = STATE_FILE + f'.n_{current_task[:15].replace(" ","_")}'
        if not os.path.exists(notif_key):
            cur.execute("INSERT INTO session_messages (session_id, role, content) VALUES (%s, 'user', %s)",
                (SESION_LINA, f'🤖 [Cline] completó: {current_task}. Revisá su trabajo (branch, commits, CI, tests).'))
            conn.commit()
            with open(notif_key, 'w') as f: f.write('1')
            print(f'📨 Notificación en sesión LINA')
        else:
            print('🔄 Ya notificado, esperando revisión')
    else:
        print(f'OK — #{msg_id}: Cline trabaja en {current_task}')
    cur.close(); conn.close()

if __name__ == '__main__':
    main()
