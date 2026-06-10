#!/usr/bin/env python3
"""LINA Rutina — Check-in matutino y cierre nocturno via goosed API."""

import asyncio, json, os, sys, time, uuid
from datetime import datetime
import asyncpg, httpx

DB_URL = os.environ.get("LINA_DB_URL", "postgresql://lina:lina_dev@localhost:5432/lina")
GOOSED_URL = os.environ.get("GOOSED_URL", "https://localhost:3000")
GOOSED_SECRET = os.environ.get("GOOSE_SERVER_SECRET_KEY", "4bea469dd75fb2de8cf3f797c832813575bc5408fab3ae3a2b6734037971e564")
SESSION_ID = "telegram-fede-lina-checkin"

async def read_profile(db):
    rows = await db.fetch("SELECT key, value FROM lina.user_profile ORDER BY key")
    return {r["key"]: r["value"] for r in rows}

async def read_wheel(db):
    rows = await db.fetch("SELECT DISTINCT ON (area) area, score FROM lina.wheel_of_life ORDER BY area, evaluated_at DESC")
    return [{"area": r["area"], "score": r["score"]} for r in rows]

async def send_to_lina(prompt: str) -> str:
    headers = {"x-secret-key": GOOSED_SECRET, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120, verify=False) as c:
        r = await c.get(f"{GOOSED_URL}/sessions/{SESSION_ID}", headers=headers)
        if r.status_code == 200:
            await c.post(f"{GOOSED_URL}/agent/resume", json={"session_id": SESSION_ID, "load_model_and_extensions": True}, headers=headers)
        else:
            r2 = await c.post(f"{GOOSED_URL}/agent/start", json={"working_dir": "/tmp"}, headers=headers)
            new_id = r2.json().get("id") or SESSION_ID
            await c.post(f"{GOOSED_URL}/agent/resume", json={"session_id": new_id, "load_model_and_extensions": True}, headers=headers)
        active_id = new_id if "new_id" in dir() else SESSION_ID
        payload = {"session_id": active_id, "user_message": {"role": "user", "created": int(time.time()), "content": [{"type": "text", "text": prompt}], "metadata": {"userVisible": True, "agentVisible": True}}}
        r = await c.post(f"{GOOSED_URL}/reply", json=payload, headers=headers, timeout=120)
        if r.status_code != 200: return f"ERROR {r.status_code}"
        text_parts = []
        for line in r.text.split("\n"):
            if line.startswith("data: "):
                try:
                    d = json.loads(line[6:])
                    if d.get("type") == "Message" and "message" in d:
                        for cont in d.get("message", {}).get("content", []):
                            if cont.get("type") == "text": text_parts.append(cont["text"])
                    if d.get("type") == "text":
                        text_parts.append(d.get("text", ""))
                except: pass
        return "".join(text_parts)

async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"
    now = datetime.now()
    hora = now.strftime("%H:%M")
    dia_es = {"Monday":"lunes","Tuesday":"martes","Wednesday":"miércoles","Thursday":"jueves","Friday":"viernes","Saturday":"sábado","Sunday":"domingo"}
    mes_es = {"January":"enero","February":"febrero","March":"marzo","April":"abril","May":"mayo","June":"junio","July":"julio","August":"agosto","September":"septiembre","October":"octubre","November":"noviembre","December":"diciembre"}
    dia = now.strftime("%A %d de %B de %Y")
    for en, es in dia_es.items(): dia = dia.replace(en, es)
    for en, es in mes_es.items(): dia = dia.replace(en, es)

    db = await asyncpg.connect(DB_URL)
    try:
        profile = await read_profile(db)
        wheel = await read_wheel(db)
    finally:
        await db.close()

    pf_name = profile.get("nombre", "Fede")
    pf_edad = f", {profile.get('edad', '')} años" if profile.get("edad") else ""
    ws_text = "sin datos" if not wheel else " | ".join(f"{w['area']}={w['score']}/10" for w in wheel)

    if mode == "morning":
        prompt = f"""CHECK-IN MATUTINO — {dia} {hora}. Perfil: {pf_name}{pf_edad}. Rueda: {ws_text}. LINA: es tu check-in matutino. Dale los buenos días a Fede con calidez rioplatense. Preguntale cómo durmió. Si hay rueda, mencioná brevemente (sin abrumar). Ofrecé revisar su agenda. Sin presión, sin lista de pendientes. IMPORTANTE: respondé como LINA directamente, sin etiquetas."""
    elif mode == "evening":
        prompt = f"""CIERRE NOCTURNO — {dia} {hora}. Perfil: {pf_name}{pf_edad}. Rueda: {ws_text}. LINA: es tu cierre nocturno. Acompañá a Fede en la reflexión del día. Preguntale cómo estuvo. Terminá con un deseo de buen descanso. Sin etiquetas."""
    else:
        prompt = f"LINA: check-in de prueba, {dia} {hora}. Perfil: {pf_name}{pf_edad}. Confirmá si podés leer profile y wheel, y dale un saludo matutino a Fede."

    print(f"[RUTINA] {mode} — {dia} {hora}")
    print(f"  Perfil: {len(profile)} campos, Rueda: {len(wheel)} áreas")
    resp = await send_to_lina(prompt)
    print(f"  LINA: {resp[:400]}...")
    print("  ✅ Check-in completado")

if __name__ == "__main__":
    asyncio.run(main())
