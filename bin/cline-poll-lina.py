#!/usr/bin/env python3
"""Polling loop: espera instrucciones de LINA via DB de lina-db.

Como Cline no es un sub-agente registrado en lina-orchestrator, busca
en agent_events o en la tabla agent_commands para ver si LINA dejó
instrucciones.

Modo de uso: python3 bin/cline-poll-lina.py [--timeout 300]
"""

import os
import sys
import time
import json
import subprocess
import argparse


def docker_psql(query: str) -> str:
    """Ejecuta una query via docker exec en lina-db."""
    result = subprocess.run(
        ["docker", "exec", "-i", "docker-lina-db-1", "psql", "-U", "lina", "-d", "lina",
         "-t", "-A", "-F", "|", "-c", query],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        print(f"⚠️ DB error: {result.stderr.strip()}")
        return ""
    return result.stdout.strip()


def check_instructions() -> list[dict]:
    """Busca instrucciones recientes en agent_commands."""
    rows = docker_psql(
        """SELECT id, kind, args_json, sent_at
           FROM agent_commands
           WHERE kind = 'send_instruction'
             AND ack_at IS NULL
           ORDER BY sent_at ASC
           LIMIT 5"""
    )
    if not rows:
        return []

    instructions = []
    for line in rows.split("\n"):
        line = line.strip()
        if not line or line.startswith("("):
            continue
        parts = line.split("|")
        if len(parts) == 4:
            instructions.append({
                "id": parts[0].strip(),
                "kind": parts[1].strip(),
                "args": parts[2].strip(),
                "sent_at": parts[3].strip(),
            })
    return instructions


def check_gateway_events() -> list[dict]:
    """Busca eventos recientes del gateway (Telegram) con respuestas."""
    rows = docker_psql(
        """SELECT id, event_type, payload_json, created_at
           FROM gateway_events
           WHERE event_type IN ('telegram_message', 'agent_response')
           ORDER BY created_at DESC
           LIMIT 5"""
    )
    if not rows:
        return []

    events = []
    for line in rows.split("\n"):
        line = line.strip()
        if not line or line.startswith("("):
            continue
        parts = line.split("|")
        if len(parts) == 4:
            events.append({
                "id": parts[0].strip(),
                "type": parts[1].strip(),
                "payload": parts[2].strip(),
                "created_at": parts[3].strip(),
            })
    return events


def main():
    parser = argparse.ArgumentParser(description="Poll LINA for instructions")
    parser.add_argument("--timeout", type=int, default=300, help="Max wait in seconds")
    parser.add_argument("--interval", type=int, default=10, help="Poll interval in seconds")
    args = parser.parse_args()

    deadline = time.time() + args.timeout
    last_check = time.time()

    print(f"🔍 Polleando instrucciones de LINA cada {args.interval}s (timeout {args.timeout}s)...")
    print("   Presiona Ctrl+C para salir.\n")

    try:
        while time.time() < deadline:
            # Check agent_commands
            commands = check_instructions()
            if commands:
                print("📥 INSTRUCCIONES RECIBIDAS DE LINA:")
                print("─" * 60)
                for cmd in commands:
                    print(f"  ID: {cmd['id']}")
                    print(f"  Args: {cmd['args']}")
                    print(f"  Sent: {cmd['sent_at']}")
                    print()
                print("─" * 60)
                return commands

            # Every 30s check gateway_events too
            if time.time() - last_check > 30:
                events = check_gateway_events()
                if events:
                    for ev in events:
                        print(f"  [{ev['type']}] {ev['payload'][:200]}...")
                last_check = time.time()

            sys.stdout.write(".")
            sys.stdout.flush()
            time.sleep(args.interval)

        print("\n⏰ Timeout alcanzado sin recibir instrucciones.")
        return []

    except KeyboardInterrupt:
        print("\n⛔ Polling interrumpido.")
        return []


if __name__ == "__main__":
    result = main()
    if result:
        print(f"\n✅ {len(result)} instrucción(es) recibida(s).")
    else:
        print("\nℹ️ No se recibieron instrucciones de LINA en este ciclo.")