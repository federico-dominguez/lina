"""LINA /pipeline command — gestiona pipelines multi-bot desde Telegram.

Uso:
    /pipeline                       → ayuda
    /pipeline list                  → lista pipelines guardados
    /pipeline run <nombre> [-p]     → ejecuta pipeline
    /pipeline info <nombre>         → info detallada

La ejecución del pipeline envía mensajes al grupo "Comms" vía Comm account.
Los resultados son visibles en el grupo automáticamente.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parents[3]
BIN_PIPELINE = BASE / "bin" / "pipeline"


async def handle_pipeline(text: str, tg, chat_id: int) -> None:
    """Handle /pipeline command from Telegram."""
    parts = text.strip().split()
    subcmd = parts[1] if len(parts) > 1 else "help"
    name = parts[2] if len(parts) > 2 else ""
    parallel = "-p" in parts or "--parallel" in parts

    if subcmd in ("help", ""):
        await tg.send_message(
            chat_id,
            "📋 Pipeline Manager\n"
            "Uso:\n"
            "  /pipeline list          — listar pipelines\n"
            "  /pipeline run <nombre>  — ejecutar pipeline\n"
            "  /pipeline info <nombre> — info detallada\n"
            "Ej: /pipeline run duo -p",
        )
        return

    if subcmd == "list":
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(BIN_PIPELINE),
                "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            out = stdout.decode() if stdout else ""
            await tg.send_message(chat_id, f"📂 Pipelines:\n<pre>{out}</pre>")
        except TimeoutError:
            await tg.send_message(chat_id, "⚠️ Timeout al listar pipelines")
        except Exception as e:
            await tg.send_message(chat_id, f"❌ Error: {e}")
        return

    if subcmd == "run" and name:
        try:
            await tg.send_message(chat_id, f"🚀 Ejecutando pipeline '{name}'...")
            cmd = [sys.executable, str(BIN_PIPELINE), "run", name]
            if parallel:
                cmd.append("--parallel")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
            out = stdout.decode() if stdout else ""
            summary = out[-400:] if len(out) > 400 else out
            await tg.send_message(
                chat_id, f"✅ Pipeline '{name}' completado.\n<pre>{summary}</pre>"
            )
        except TimeoutError:
            await tg.send_message(chat_id, f"⚠️ Pipeline '{name}' excedió 5 min de timeout")
        except Exception as e:
            await tg.send_message(chat_id, f"❌ Error: {e}")
        return

    if subcmd == "info" and name:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(BIN_PIPELINE),
                "info",
                name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            out = stdout.decode() if stdout else ""
            await tg.send_message(chat_id, f"📋 Pipeline '{name}':\n<pre>{out}</pre>")
        except TimeoutError:
            await tg.send_message(chat_id, f"⚠️ Timeout obteniendo info de '{name}'")
        except Exception as e:
            await tg.send_message(chat_id, f"❌ Error: {e}")
        return

    await tg.send_message(chat_id, f"❌ Subcomando desconocido: /pipeline {subcmd}")
