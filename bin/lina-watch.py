#!/usr/bin/env python3
"""
LINA Watch — Monitoreo bidireccional de actividad entre LINA y Goose.
Lee agent_messages y session_events para mostrar qué está pasando.
"""
import psycopg2, json, sys, time

DB_DSN = "postgresql://lina:lina_dev@172.19.0.15:5432/lina"

def fmt_time(dt):
    return str(dt)[:19] if dt else "—"

def check():
    conn = psycopg2.connect(DB_DSN, connect_timeout=3)
    cur = conn.cursor()

    print("=" * 60)
    print("📊 LINA WATCH — MONITOREO BIDIRECCIONAL")
    print(f"   {time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)

    # 1. AGENTES EN session_events
    cur.execute("SELECT agent, COUNT(*) FROM session_events GROUP BY agent ORDER BY agent")
    agents = cur.fetchall()
    print(f"\n📦 SESSION_EVENTS POR AGENTE:")
    for a in agents:
        print(f"   {a[0]:15s}: {a[1]:>8,} eventos")

    # 2. MENSAJES RECIENTES
    cur.execute("""
        SELECT id, sender, recipient, text, created_at, read_at 
        FROM agent_messages 
        WHERE created_at > NOW() - INTERVAL '1 hour'
        ORDER BY id DESC LIMIT 10
    """)
    msgs = cur.fetchall()
    print(f"\n📨 MENSAJES ENTRE AGENTES (última hora):")
    if not msgs:
        print("   (sin mensajes)")
    for m in msgs:
        status = "✅ leído" if m[5] else "⏳ pendiente"
        print(f"   #{m[0]} | {m[1]:6s}→{m[2]:6s} | {status} | {fmt_time(m[4])}")
        print(f"          {str(m[3])[:80]}")

    # 3. EVENTOS DE GATEWAY
    cur.execute("SELECT id, event_type, created_at FROM gateway_events ORDER BY id DESC LIMIT 5")
    gw = cur.fetchall()
    print(f"\n🔌 EVENTOS DE GATEWAY:")
    for g in gw:
        print(f"   #{g[0]} | {g[1]:15s} | {g[2]}")

    # 4. ÚLTIMOS EVENTOS DE CADA AGENTE
    cur.execute("""
        SELECT agent, session_id, event_type, created_at 
        FROM session_events 
        WHERE id IN (
            SELECT MAX(id) FROM session_events WHERE agent IS NOT NULL GROUP BY agent
        )
        ORDER BY created_at DESC
    """)
    last = cur.fetchall()
    print(f"\n🕐 ÚLTIMO EVENTO POR AGENTE:")
    for l in last:
        print(f"   {l[0]:10s} | {str(l[1])[:25]:25s} | {l[2]:15s} | {l[3]}")

    conn.close()

if __name__ == "__main__":
    follow = "--follow" in sys.argv or "-f" in sys.argv
    if follow:
        try:
            while True:
                check()
                time.sleep(10)
        except KeyboardInterrupt:
            print("\n👋 Monitoreo detenido.")
    else:
        check()
