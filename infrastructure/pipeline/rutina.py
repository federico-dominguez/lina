#!/usr/bin/env python3
"""LINA Rutina — Check-in matutino y cierre nocturno.

Lee perfil de Fede, Rueda de la Vida y contexto temporal para
enviar un mensaje personalizado a LINA (vía Telegram group Comm).

Modos:
    morning  — check-in matutino (8:30 AM)
    evening  — cierre nocturno (10:00 PM)
    test     — modo manual para pruebas

Uso:
    python3 rutina.py morning
    python3 rutina.py evening
    python3 rutina.py test "Mensaje opcional"
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime

import asyncpg

DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina_dev@localhost:5432/lina",
)
SEND_SCRIPT = "/home/fede/lina/comm/send-lina.py"


async def read_profile(db: asyncpg.Connection) -> dict[str, str]:
    """Lee todos los campos del perfil de Fede."""
    rows = await db.fetch("SELECT key, value FROM lina.user_profile ORDER BY key")
    return {r["key"]: r["value"] for r in rows}


async def read_wheel(db: asyncpg.Connection) -> list[dict]:
    """Lee la Rueda de la Vida actual."""
    rows = await db.fetch("""
        SELECT DISTINCT ON (area)
            area, score, notes,
            evaluated_at AT TIME ZONE 'America/Montevideo' AS evaluated_at
        FROM lina.wheel_of_life
        ORDER BY area, evaluated_at DESC
    """)
    return [{"area": r["area"], "score": r["score"], "notes": r["notes"]} for r in rows]


def format_wheel_summary(wheel: list[dict]) -> str:
    """Formatea la rueda actual para el prompt."""
    if not wheel:
        return "Rueda de la Vida: sin datos aún."
    areas_text = " | ".join(f"{w['area']}={w['score']}/10" for w in wheel)
    scores = [w["score"] for w in wheel]
    avg = round(sum(scores) / len(scores), 1)
    highs = [w["area"] for w in wheel if w["score"] >= 8]
    lows = [w["area"] for w in wheel if w["score"] <= 4]
    lines = [f"Rueda actual ({len(wheel)}/8 áreas): {areas_text}"]
    lines.append(f"Promedio: {avg}/10")
    if highs:
        lines.append(f"Fuerte: {', '.join(highs)}")
    if lows:
        lines.append(f"Atención: {', '.join(lows)}")
    return "\n".join(lines)


def format_profile_summary(profile: dict[str, str]) -> str:
    """Extrae datos clave del perfil."""
    nombre = profile.get("nombre", "Fede")
    edad = profile.get("edad", "")
    pais = profile.get("pais", "Uruguay")
    idioma = profile.get("idioma", "español rioplatense")
    return f"Nombre: {nombre}" + (f", Edad: {edad}" if edad else "") + f", País: {pais}, Idioma: {idioma}"


def send_to_lina(message: str) -> bool:
    """Envía mensaje a LINA vía Telegram (send-lina.py)."""
    try:
        subprocess.run(
            [sys.executable, SEND_SCRIPT, message],
            timeout=30,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


async def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"
    custom_msg = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""

    now = datetime.now()
    hora = now.strftime("%H:%M")
    dia = now.strftime("%A %d de %B de %Y")
    # Traducir días al español
    dias_es = {"Monday": "lunes", "Tuesday": "martes", "Wednesday": "miércoles",
               "Thursday": "jueves", "Friday": "viernes", "Saturday": "sábado", "Sunday": "domingo"}
    meses_es = {"January": "enero", "February": "febrero", "March": "marzo", "April": "abril",
                "May": "mayo", "June": "junio", "July": "julio", "August": "agosto",
                "September": "septiembre", "October": "octubre", "November": "noviembre", "December": "diciembre"}
    for en, es in dias_es.items():
        dia = dia.replace(en, es)
    for en, es in meses_es.items():
        dia = dia.replace(en, es)
    dia = dia.lstrip("0").replace(" 0", " ")

    # Conectar a DB
    db = await asyncpg.connect(DB_URL)

    try:
        profile = await read_profile(db)
        wheel = await read_wheel(db)
    finally:
        await db.close()

    pf_summary = format_profile_summary(profile)
    wheel_summary = format_wheel_summary(wheel)

    if mode == "morning":
        prompt = f"""[CHECK-IN MATUTINO — {dia} {hora}]

Perfil: {pf_summary}
{wheel_summary}

LINA: es tu check-in matutino de las {hora}. Dale los buenos días a Fede con calidez rioplatense.
Mencioná algo del contexto (día, hora) y preguntale cómo durmió.
Si hay datos de la rueda, mencioná brevemente lo que ves (sin abrumar).
Si no hay datos, sugerí hacer la rueda cuando él quiera, sin presión.
Ofrecé revisar su agenda del día si quiere.
Recordale que estás para lo que necesite — sin lista de pendientes, sin presionar.

IMPORTANTE: respondé como LINA directamente, sin decir "Check-in matutino" ni etiquetas."""
    elif mode == "evening":
        prompt = f"""[CIERRE NOCTURNO — {dia} {hora}]

Perfil: {pf_summary}
{wheel_summary}

LINA: es el cierre de la noche, {hora}. Acompañá a Fede en la reflexión del día.
Preguntale cómo estuvo su día, qué fue lo mejor, si quiere compartir algo.
No le pases lista de tareas pendientes. No le exijas.
Si hay áreas bajas en la rueda, no las uses para presionar.
Sugerí gratitud o journaling solo si viene al caso.
Terminá con un deseo de buen descanso.

IMPORTANTE: respondé como LINA directamente, sin decir "Cierre nocturno" ni etiquetas."""
    else:
        prompt = custom_msg if custom_msg else f"LINA: check-in de prueba, {dia} {hora}. Perfil: {pf_summary}"

    print(f"[RUTINA] {mode} — {dia} {hora}")
    print(f"  Perfil: {len(profile)} campos, Rueda: {len(wheel)} áreas")
    print(f"  Enviando a LINA...")

    ok = send_to_lina(prompt)
    if ok:
        print(f"  ✅ Mensaje enviado a LINA")
    else:
        print(f"  ❌ Error enviando mensaje")
        sys.exit(1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
