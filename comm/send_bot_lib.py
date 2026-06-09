#!/usr/bin/env python3
"""send_bot_lib.py — Librería para enviar mensajes a bots en grupos de Telegram."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, MessageEntityMention

API_ID = 12663248
API_HASH = "57a7b9ec3cd607e64b73dbae1240af24"
SESSION = os.environ.get("COMM_SESSION") or str(Path(__file__).resolve().parent / "comm_session")


def find_group(match: str = "Comm"):
    async def _find():
        client = TelegramClient(SESSION, API_ID, API_HASH)
        client.parse_mode = None
        await client.start()
        try:
            dialogs = await client(GetDialogsRequest(
                offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(), limit=200, hash=0,
            ))
            for d in dialogs.chats:
                title = getattr(d, "title", "") or ""
                if match.lower() in title.lower():
                    return d
            return None
        finally:
            await client.disconnect()
    return asyncio.run(_find())


def send_message(group, target: str, message: str):
    async def _send():
        client = TelegramClient(SESSION, API_ID, API_HASH)
        client.parse_mode = None
        await client.start()
        try:
            text = f"@{target} {message}"
            entities = [MessageEntityMention(offset=0, length=len(target.lstrip("@")) + 1)]
            await client.send_message(group, text, formatting_entities=entities)
            return True, f"@{target} en '{getattr(group, 'title', '?')}'"
        except Exception as e:
            return False, str(e)
        finally:
            await client.disconnect()
    return asyncio.run(_send())


def send_plain(group, text: str):
    async def _send():
        client = TelegramClient(SESSION, API_ID, API_HASH)
        client.parse_mode = None
        await client.start()
        try:
            await client.send_message(group, text)
            return True, f"plain → '{getattr(group, 'title', '?')}'"
        except Exception as e:
            return False, str(e)
        finally:
            await client.disconnect()
    return asyncio.run(_send())
