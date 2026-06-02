#!/usr/bin/env python3
"""Envía un mensaje a LINA via Telegram y espera respuesta en un archivo.

Usa TELEGRAM_BOT_TOKEN (del secrets.env de LINA).
Como el bot ya está siendo polleado por lina-gateway, no podemos usar
getUpdates — en su lugar escribimos el mensaje y LINA responde al chat.

Modo de uso:
  1. export TELEGRAM_BOT_TOKEN=<token>
  2. export LINA_CHAT_ID=<chat_id>  (opcional, se puede pasar como arg)
  3. python3 cline-ask-lina.py "mensaje"
"""

import os
import sys
import time
import json
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("LINA_CHAT_ID", "7966401870")  # Federico's chat_id


def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": False,
    }
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def main():
    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
    else:
        message = sys.stdin.read().strip()
    if not message:
        print("❌ No message provided. Usage: cline-ask-lina.py <message>")
        sys.exit(1)

    print(f"📤 Enviando a chat_id={CHAT_ID}...")
    try:
        send_message(message)
        print("✅ Mensaje enviado. LINA lo recibirá en Telegram.")
        print("   La respuesta llegará al chat de Telegram.")
        print("   Podés verla en Telegram o esperar a que LINA la procese.")
    except Exception as e:
        print(f"❌ Error sending: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()