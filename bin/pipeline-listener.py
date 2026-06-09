#!/usr/bin/env python3
"""
pipeline-listener.py — Comandos de pipeline ahora por @s_pipelines_bot (privado).
Los comandos / en el grupo son ignorados.
"""

import asyncio, os, re, shutil, subprocess, sys, time
from pathlib import Path

import yaml
from telethon import TelegramClient, events
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty

BASE = Path(__file__).resolve().parent.parent
BIN_PIPELINE = BASE / "bin" / "pipeline"
COMM_DIR = BASE / "comm"
SESSION = str(COMM_DIR / "comm_session")
SESSION_PIPELINE = SESSION + "_pipeline"
API_ID = 12663248
API_HASH = "57a7b9ec3cd607e64b73dbae1240af24"

GROUP_ID = None


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def get_group_id(client):
    dialogs = await client(GetDialogsRequest(
        offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(),
        limit=200, hash=0,
    ))
    for d in dialogs.chats:
        title = getattr(d, "title", "") or ""
        if "Comm" in title:
            return d.id
    return None


async def main():
    global GROUP_ID

    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.parse_mode = None
    await client.start()
    log("✅ pipeline-listener conectado (comandos ignorados — usar @s_pipelines_bot en privado)")

    GROUP_ID = await get_group_id(client)
    if not GROUP_ID:
        log("❌ No se encontró grupo Comm")
        return
    log(f"👂 En grupo #{GROUP_ID} — solo escuchando, sin responder comandos")

    @client.on(events.NewMessage(chats=GROUP_ID))
    async def handler(event):
        text = (event.message.text or "").strip()
        # Ignorar todos los comandos — pipeline-bot los maneja en privado
        if text.startswith("/"):
            return

    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("⏹️ pipeline-listener detenido")
