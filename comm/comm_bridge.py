#!/usr/bin/env python3
"""Comm Bridge — DB ↔ goosed SSE bridge.

Polls comm_messages (status='sent') destined for lina/cline/gemma,
forwards to their goosed instances via SSE (/reply), and writes
responses back to the DB.

Usage:
    COMM_BRIDGE_LOG_LEVEL=DEBUG python comm_bridge.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

import asyncpg
import httpx

# ─── Config ──────────────────────────────────────────────────────────────────

# ─── Load secrets from ~/.config/goose/secrets.env ──────────────────────────
_secrets_path = os.path.expanduser("~/.config/goose/secrets.env")
if os.path.exists(_secrets_path):
    with open(_secrets_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _val = _line.split("=", 1)
            # Only set if not already set in environment
            if _key not in os.environ:
                os.environ[_key] = _val

DB_URL = os.environ.get(
    "LINA_DB_URL",
    "postgresql://lina:lina_dev@localhost:5432/lina",
)

BOTS = {
    "lina": {
        "url": os.environ.get("LINA_GOOSED_URL", "https://localhost:3000"),
        "secret": os.environ.get("LINA_GOOSED_SECRET", ""),
    },
    "cline": {
        "url": os.environ.get("CLINE_GOOSED_URL", "https://localhost:3001"),
        "secret": os.environ.get("CLINE_GOOSED_SECRET", ""),
    },
    "gemma": {
        "url": os.environ.get("GEMMA_GOOSED_URL", "https://localhost:3002"),
        "secret": os.environ.get("GEMMA_GOOSED_SECRET", ""),
    },
}

POLL_INTERVAL = float(os.environ.get("COMM_BRIDGE_POLL_INTERVAL", "2.0"))
LOG_LEVEL = os.environ.get("COMM_BRIDGE_LOG_LEVEL", "INFO")
COMM_SENDER = os.environ.get("COMM_SENDER", "comm-bridge")

# Track tool call IDs to names across SSE events.
_tool_call_names: dict[str, str] = {}

logger = logging.getLogger("comm-bridge")


# ─── Event types (mirrors goose_client.py) ───────────────────────────────────


class EventType(StrEnum):
    MESSAGE = "Message"
    FINISH = "Finish"
    ERROR = "Error"
    PING = "Ping"


@dataclass
class TokenState:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    accumulated_cost: float = 0.0


@dataclass
class MessageEvent:
    event_type: EventType
    role: str = ""
    text_content: str = ""
    finish_reason: str = ""
    token_state: TokenState | None = None
    error: str = ""
    # ── Structured detail (new) ──
    thinking: str = ""                      # bot's thinking/reasoning
    tool_call_name: str = ""                # e.g. "sh_run", "fs_write"
    tool_call_args: str = ""                # truncated args JSON
    tool_result_summary: str = ""           # truncated result text
    tool_success: bool = True               # whether tool succeeded
    log_line: str = ""                      # human-readable line for the trace


_call_depth: dict[str, int] = {}  # bot_name → current nesting depth


def _parse_event(data: dict[str, Any], bot_name: str = "") -> MessageEvent | None:
    """Parse SSE data into a MessageEvent, capturing ALL content types."""
    event_type_raw = data.get("type")
    try:
        etype = EventType(event_type_raw)
    except ValueError:
        return None

    if etype == EventType.PING:
        return MessageEvent(event_type=etype)

    if etype == EventType.ERROR:
        msg = data.get("error", "unknown error")
        log_line = f"❌ ERROR: {msg[:200]}"
        logger.warning("[%s] %s", bot_name, log_line)
        return MessageEvent(event_type=etype, error=msg, log_line=log_line)

    if etype == EventType.FINISH:
        ts_raw = data.get("token_state") or {}
        token_state = TokenState(
            input_tokens=int(ts_raw.get("inputTokens", 0)),
            output_tokens=int(ts_raw.get("outputTokens", 0)),
            total_tokens=int(ts_raw.get("totalTokens", 0)),
            accumulated_cost=float(ts_raw.get("accumulatedCost", 0.0)),
        ) if ts_raw else None
        reason = data.get("reason", "stop")
        log_line = f"🏁 Finish: {reason}"
        if token_state:
            log_line += f" ({token_state.input_tokens} in, {token_state.output_tokens} out, ${token_state.accumulated_cost:.6f})"
        logger.info("[%s] %s", bot_name, log_line)
        return MessageEvent(
            event_type=etype,
            finish_reason=reason,
            token_state=token_state,
            log_line=log_line,
        )

    if etype == EventType.MESSAGE:
        msg = data.get("message", {})
        role = msg.get("role", "")
        texts: list[str] = []
        thinkings: list[str] = []
        tool_calls: list[str] = []
        tool_results: list[str] = []
        log_lines: list[str] = []

        for item in msg.get("content", []):
            item_type = item.get("type", "")
            
            # ── TEXT ──────────────────────────────────────────────────────────
            if item_type == "text":
                t = item.get("text", "")
                if t:
                    texts.append(t)
            
            # ── THINKING ──────────────────────────────────────────────────────
            elif item_type == "thinking":
                t = item.get("thinking", "")
                if t:
                    thinkings.append(t)
                    logger.info("[%s] 💭 %s", bot_name, t[:200])
            
            # ── TOOL CALL ────────────────────────────────────────────────────
            elif item_type in ("tool_use", "tool_request", "toolRequest"):
                tool_call = item.get("tool_call") or item.get("toolCall") or item
                if isinstance(tool_call, dict):
                    value = tool_call.get("value", {}) if isinstance(tool_call, dict) else {}
                    name = value.get("name") or tool_call.get("name") or ""
                    args = value.get("arguments") or tool_call.get("input", {})
                    if isinstance(args, str):
                        try: args = json.loads(args)
                        except Exception: args = {}
                    args_json = json.dumps(args)[:200] if args else "{}"
                    tool_calls.append(f"[tool: {name}({args_json})]")
                    log_lines.append(f"🛠️  {name}({args_json[:120]})")
                    call_id = item.get("id", "")
                    if call_id and name:
                        _tool_call_names[call_id] = name
            
            # ── TOOL RESULT ───────────────────────────────────────────────────
            elif item_type in ("tool_result", "tool_response", "toolResponse"):
                result = item.get("tool_result") or item.get("toolResult") or item
                value = result.get("value", {}) if isinstance(result, dict) else {}
                success = True
                result_text = ""
                if isinstance(result, dict):
                    content_items = value.get("content", []) or result.get("content", [])
                    ok = result.get("Ok")
                    if ok:
                        content_items = ok if isinstance(ok, list) else ok.get("content", []) if isinstance(ok, dict) else []
                    result_text = " ".join(
                        c.get("text", "")
                        for c in content_items
                        if isinstance(c, dict) and c.get("type") == "text"
                    )
                    success = not value.get("isError", False) and "Err" not in str(result)
                else:
                    result_text = str(result)
                
                tr_summary = result_text[:200] if result_text else ""
                tool_results.append(tr_summary)
                if success:
                    log_lines.append(f"  ✅ result: {tr_summary[:150]}")
                else:
                    log_lines.append(f"  ❌ FAILED: {tr_summary[:150]}")

        text_content = "\n".join(texts) if texts else ""
        thinking_all = "\n".join(thinkings) if thinkings else ""
        log_line = "\n".join(log_lines) if log_lines else ""
        
        if log_line:
            logger.info("[%s] %s", bot_name, log_line)

        return MessageEvent(
            event_type=etype,
            role=role,
            text_content=text_content,
            thinking=thinking_all,
            log_line=log_line,
        )

    return None


async def send_to_goosed(
    bot_name: str,
    goosed_url: str,
    goosed_secret: str,
    session_id: str,
    message: str,
    pool: asyncpg.Pool | None = None,    # for periodic progress updates
    request_msg_id: int | None = None,   # comm_messages id for progress updates
) -> dict:
    """Send message to goosed via /reply SSE and collect full response + detailed trace.
    
    If pool and request_msg_id are provided, writes periodic progress updates
    to comm_messages every PROGRESS_INTERVAL seconds so Goose can monitor activity.
    """
    headers = {"Content-Type": "application/json", "x-secret-key": goosed_secret} if goosed_secret else {"Content-Type": "application/json"}
    
    texts: list[str] = []
    trace_lines: list[str] = []  # detailed activity log
    thinking_accum: list[str] = []
    tool_count = 0
    timeout = httpx.Timeout(300.0)  # 5 min timeout for long tasks
    PROGRESS_INTERVAL = 10  # seconds between progress updates
    
    start_time = time.time()
    last_progress = start_time

    _call_depth[bot_name] = 0

    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
        # Ensure session exists
        r = await client.get(f"{goosed_url}/sessions/{session_id}", headers=headers)
        if r.status_code != 200:
            r2 = await client.post(f"{goosed_url}/agent/start", json={"working_dir": "/tmp"}, headers=headers)
            r2.raise_for_status()
            data = r2.json()
            session_id = data.get("id") or data.get("session_id") or session_id
            r3 = await client.post(f"{goosed_url}/agent/resume", json={"session_id": session_id, "load_model_and_extensions": True}, headers=headers)
            r3.raise_for_status()
            logger.info("Created new session %s for %s", session_id, bot_name)
        else:
            await client.post(f"{goosed_url}/agent/resume", json={"session_id": session_id, "load_model_and_extensions": True}, headers=headers)

        payload = {
            "session_id": session_id,
            "user_message": {
                "role": "user",
                "created": int(time.time()),
                "content": [{"type": "text", "text": message}],
                "metadata": {"userVisible": True, "agentVisible": True},
            },
        }

        async with client.stream("POST", f"{goosed_url}/reply", json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:]
                if not raw.strip():
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event = _parse_event(data, bot_name)
                if event is None:
                    continue
                if event.event_type == EventType.PING:
                    continue
                if event.event_type == EventType.ERROR:
                    texts.append(f"[ERROR: {event.error}]")
                    trace_lines.append(event.log_line)
                    break
                if event.event_type == EventType.FINISH:
                    if event.token_state:
                        logger.info(
                            "  %s tokens: %d in, %d out, $%.6f acc",
                            bot_name,
                            event.token_state.input_tokens,
                            event.token_state.output_tokens,
                            event.token_state.accumulated_cost,
                        )
                    if event.log_line:
                        trace_lines.append(event.log_line)
                    
                    # ── Executive summary at session end ───────────────────
                    total_elapsed = time.time() - start_time
                    all_thinking = " ".join(thinking_accum) if thinking_accum else ""
                    tool_list = [l for l in trace_lines if "🛠️" in l]
                    all_tools = "; ".join(t.replace("🛠️ ", "").strip() for t in tool_list[-20:])
                    all_text = " ".join(texts) if texts else ""
                    
                    exec_summary_activity = (
                        f"Duración: {total_elapsed:.0f}s\n"
                        f"Tools ejecutados: {tool_count}\n"
                        f"Pensamientos: {len(thinking_accum)}\n"
                        f"Herramientas: {all_tools[:800]}\n"
                        f"Thinking clave: {all_thinking[:500]}\n"
                        f"Respuesta final: {all_text[:500]}"
                    )
                    
                    exec_summary = f"[📋 {bot_name}] Finalizado en {total_elapsed:.0f}s | {tool_count} tools | {len(thinking_accum)} thoughts"
                    try:
                        ds_key = os.environ.get("DEEPSEEK_API_KEY", "")
                        if ds_key and exec_summary_activity.strip():
                            async with httpx.AsyncClient(timeout=15.0) as ds:
                                r = await ds.post(
                                    "https://api.deepseek.com/chat/completions",
                                    headers={"Authorization": f"Bearer {ds_key}", "Content-Type": "application/json"},
                                    json={
                                        "model": "deepseek-chat",
                                        "messages": [
                                            {"role": "system", "content": (
                                                "Sos un analista técnico. Generá un RESUMEN EJECUTIVO de 4-5 líneas "
                                                "de lo que hizo un bot durante una tarea. Incluí: "
                                                "• Qué hizo (archivos, comandos, resultados clave) "
                                                "• Cuánto duró y cuántas herramientas usó "
                                                "• El resultado o conclusión principal "
                                                "Usá emojis 📋🔧📄✅. Respondé en español."
                                            )},
                                            {"role": "user", "content": f"Generá el resumen ejecutivo de esta actividad:\n\n{exec_summary_activity[:1500]}"}
                                        ],
                                        "max_tokens": 300,
                                        "temperature": 0.4,
                                    },
                                )
                                d = r.json()
                                if "choices" in d and d["choices"]:
                                    exec_summary = d["choices"][0]["message"]["content"].strip()
                    except Exception as se:
                        logger.warning("[exec-summary] DeepSeek failed: %s", se)
                    
                    # ── Enviar resumen ejecutivo vía comm (NUNCA Telegram directo) ──
                    if pool:
                        try:
                            async with pool.acquire() as ec:
                                await ec.execute(
                                    """INSERT INTO comm_messages (sender, destination, message, status) 
                                       VALUES ($1, $2, $3, 'sent')""",
                                    "comm", "goose", exec_summary,
                                )
                            logger.info("[exec-summary] ✅ Summary for %s via comm: %s", bot_name, exec_summary[:150])
                        except Exception as pe:
                            logger.warning("[exec-summary] Failed to write: %s", pe)
                    
                    # ── Enviar respuesta completa vía comm (NUNCA Telegram directo) ──
                    if all_text and pool:
                        try:
                            async with pool.acquire() as ec:
                                await ec.execute(
                                    """INSERT INTO comm_messages (sender, destination, message, status) 
                                       VALUES ($1, $2, $3, 'sent')""",
                                    "comm", "goose",
                                    f"📬 {bot_name.upper()} completó la tarea\n"
                                    f"Tiempo: {total_elapsed:.0f}s | Tools: {tool_count}\n\n"
                                    f"{exec_summary[:400]}\n\n---\n{all_text[:2000]}",
                                )
                            logger.info("[orchestrator] ✅ %s result sent via comm (%d chars)", bot_name, len(all_text))
                        except Exception as pe:
                            logger.warning("[orchestrator] Failed to send result via comm: %s", pe)
                    
                    break
                # Collect thinking
                if event.thinking:
                    thinking_accum.append(event.thinking)
                # Collect trace
                if event.log_line:
                    trace_lines.append(event.log_line)
                    if "🛠️" in event.log_line:
                        tool_count += 1
                # Collect text
                if event.text_content:
                    texts.append(event.text_content)

                # ── (Progress updates removed — solo resumen final al terminar) ──

    # Build detailed trace
    detailed_trace = "\n".join(trace_lines) if trace_lines else ""
    
    logger.info(
        "[%s] Session done: %d tools, %d thinking chunks, %d chars output",
        bot_name, tool_count, len(thinking_accum), sum(len(t) for t in texts),
    )

    return {
        "text": "".join(texts),
        "trace": detailed_trace,
        "tool_count": tool_count,
        "thinking_count": len(thinking_accum),
    }


async def process_bot_messages(
    pool: asyncpg.Pool,
    bot_name: str,
    goosed_url: str,
    goosed_secret: str,
    session_id: str,
) -> None:
    """Poll and process pending messages for a single bot."""
    while True:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, sender, message, created_at 
                       FROM comm_messages 
                       WHERE destination = $1 AND status = 'sent' 
                       ORDER BY id ASC 
                       LIMIT 1""",
                    bot_name,
                )

                for row in rows:
                    msg_id = row["id"]
                    sender = row["sender"]
                    user_text = row["message"]
                    logger.info("→ %s: %s (msg #%s)", bot_name, user_text[:120], msg_id)

                    # Mark as delivered atomically (claim the message)
                    await conn.execute(
                        "UPDATE comm_messages SET status = 'delivered', delivered_at = NOW() WHERE id = $1",
                        msg_id,
                    )

                    # Send to goosed
                    try:
                        result = await send_to_goosed(
                            bot_name, goosed_url, goosed_secret, session_id, user_text,
                            pool=pool, request_msg_id=msg_id,
                        )
                        response = result["text"] if isinstance(result, dict) else result
                    except Exception as e:
                        logger.error("Failed to send to %s: %s", bot_name, e)
                        # Notify Goose of failure (message already marked delivered)
                        await conn.execute(
                            """INSERT INTO comm_messages 
                               (sender, destination, message, status) 
                               VALUES ($1, 'goose', $2, 'sent')""",
                            bot_name,
                            f"[❌ Error processing request #{msg_id} for {bot_name}]: {e}",
                        )
                        continue

                    # Mark original as delivered
                    await conn.execute(
                        "UPDATE comm_messages SET status = 'delivered' WHERE id = $1",
                        msg_id,
                    )

                    # Write response back to the original sender (not hardcoded to goose)
                    dest = sender if sender else "goose"
                    if response.strip():
                        await conn.execute(
                            """INSERT INTO comm_messages 
                               (sender, destination, message, status) 
                               VALUES ($1, $2, $3, 'sent')""",
                            bot_name,
                            dest,
                            response[:10000],  # cap at 10k chars
                        )
                        logger.info("← %s → %s: %s chars", bot_name, dest, len(response))
                    else:
                        logger.warning("Empty response from %s for msg #%s", bot_name, msg_id)
                        await conn.execute(
                            """INSERT INTO comm_messages 
                               (sender, destination, message, status) 
                               VALUES ($1, $2, $3, 'sent')""",
                            bot_name,
                            dest,
                            f"[{bot_name}: processed request #{msg_id} — no text response]",
                        )

        except Exception as e:
            logger.error("Error polling for %s: %s", bot_name, e)

        await asyncio.sleep(POLL_INTERVAL)


async def run_bot_health() -> dict | None:
    """Ejecuta bin/bot-health --json y devuelve el dict parseado, o None si falla."""
    script = os.path.join(os.path.dirname(__file__) or ".", "..", "bin", "bot_health.py")
    if not os.path.exists(script):
        script = os.path.join(os.path.dirname(__file__) or ".", "..", "bin", "bot-health")
    if not os.path.exists(script):
        logger.warning("bot-health script not found at %s", script)
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script, "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "LINA_DB_URL": DB_URL},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        if proc.returncode != 0:
            logger.warning("bot-health exited %d: %s", proc.returncode, stderr.decode()[:200])
            return None
        return json.loads(stdout.decode())
    except asyncio.TimeoutError:
        logger.warning("bot-health timed out")
        return None
    except Exception as e:
        logger.warning("bot-health failed: %s", e)
        return None


async def health_check_loop(pool: asyncpg.Pool) -> None:
    """Health check mejorado: usa bot-health script + bot_heartbeat table."""
    while True:
        try:
            # ── Pending messages count ───────────────────────────────────
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT count(*) as c FROM comm_messages WHERE status = 'sent'"
                )
                pending = row["c"] if row else 0

            # ── Run comprehensive health check via bot-health script ─────
            report = await run_bot_health()

            if report:
                overall = report.get("overall", "unknown")
                logger.info(
                    "HEALTH: overall=%s pending=%s bots=%s/%s db=%s bridge=%s",
                    overall, pending,
                    sum(1 for b in report.get("bots", {}).values() if b.get("online")),
                    len(report.get("bots", {})),
                    "✅" if report.get("database", {}).get("connected") else "❌",
                    "✅" if report.get("bridge", {}).get("active") else "❌",
                )

                # ── Write heartbeats to bot_heartbeat table ─────────────
                async with pool.acquire() as conn:
                    for bot_name, bot_data in report.get("bots", {}).items():
                        await conn.execute(
                            """INSERT INTO bot_heartbeat (bot_name, last_pulse, status, failures)
                               VALUES ($1, NOW(), $2, $3)
                               ON CONFLICT (bot_name) DO UPDATE SET
                                   last_pulse = NOW(),
                                   status = EXCLUDED.status,
                                   failures = EXCLUDED.failures,
                                   updated_at = NOW()""",
                            bot_name,
                            "alive" if bot_data.get("online") else "dead",
                            0 if bot_data.get("online") else 1,
                        )

                    # Write bridge status
                    bridge_status = "alive" if report.get("bridge", {}).get("active") else "dead"
                    await conn.execute(
                        """INSERT INTO bot_heartbeat (bot_name, last_pulse, status)
                           VALUES ($1, NOW(), $2)
                           ON CONFLICT (bot_name) DO UPDATE SET
                               last_pulse = NOW(),
                               status = EXCLUDED.status,
                               updated_at = NOW()""",
                        "comm-bridge", bridge_status,
                    )

                    # Write DB status
                    db_ok = report.get("database", {}).get("connected", False)
                    await conn.execute(
                        """INSERT INTO bot_heartbeat (bot_name, last_pulse, status)
                           VALUES ($1, NOW(), $2)
                           ON CONFLICT (bot_name) DO UPDATE SET
                               last_pulse = NOW(),
                               status = EXCLUDED.status,
                               updated_at = NOW()""",
                        "database",
                        "alive" if db_ok else "dead",
                    )

                    # ── Notify if overall status changed ────────────────
                    # Write a summary message to comm_messages for monitoring
                    if overall in ("critical", "degraded"):
                        bots_online = sum(
                            1 for b in report.get("bots", {}).values() if b.get("online")
                        )
                        total_bots = len(report.get("bots", {}))
                        summary = (
                            f"[⚠️ Health] Estado: {overall.upper()} | "
                            f"{bots_online}/{total_bots} bots online | "
                            f"DB={'ok' if db_ok else 'down'} | "
                            f"Bridge={'ok' if report.get('bridge', {}).get('active') else 'down'}"
                        )
                        await conn.execute(
                            """INSERT INTO comm_messages (sender, destination, message, status)
                               VALUES ($1, 'goose', $2, 'sent')""",
                            "comm", summary,
                        )
                        logger.warning("HEALTH ALERT: %s", summary)
            else:
                # Fallback: health check inline si bot-health no está disponible
                async with pool.acquire() as conn:
                    row = await conn.fetchrow("SELECT count(*) as c FROM comm_messages WHERE status = 'sent'")
                    pending = row["c"] if row else 0
                statuses = []
                for bot_name, cfg in BOTS.items():
                    try:
                        async with httpx.AsyncClient(timeout=5.0, verify=False) as c:
                            h = {"x-secret-key": cfg["secret"]} if cfg["secret"] else {}
                            r = await c.get(f"{cfg['url']}/status", headers=h)
                        statuses.append(f"{bot_name}={'✅' if r.is_success else '❌'}")
                    except Exception:
                        statuses.append(f"{bot_name}=❌")
                logger.info("HEALTH (fallback): pending=%s | %s", pending, " ".join(statuses))

        except Exception as e:
            logger.error("Health check failed: %s", e)

        await asyncio.sleep(30)  # Cada 30s para el heartbeat (TTL 60s)


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [comm-bridge] %(message)s",
        stream=sys.stderr,
    )

    logger.info("Comm Bridge starting...")
    logger.info("Bots: %s", ", ".join(BOTS.keys()))
    logger.info("DB: %s", DB_URL.replace(":lina_dev@", ":****@"))

    pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=5)

    # ── Graceful shutdown ────────────────────────────────────────────────
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, stop.set)
        loop.add_signal_handler(signal.SIGINT, stop.set)
    except (NotImplementedError, RuntimeError):
        logger.warning("Signal handlers not available")

    # ── Create per-bot polling tasks ─────────────────────────────────────
    tasks = []
    for bot_name, cfg in BOTS.items():
        session_id = f"comm-bridge-{bot_name}"
        tasks.append(asyncio.create_task(
            process_bot_messages(pool, bot_name, cfg["url"], cfg["secret"], session_id),
            name=f"poll-{bot_name}",
        ))

    # ── Health check ─────────────────────────────────────────────────────
    tasks.append(asyncio.create_task(health_check_loop(pool), name="health"))

    # ── Wait for stop signal ─────────────────────────────────────────────
    stop_task = asyncio.create_task(stop.wait(), name="stop")
    all_tasks = [*tasks, stop_task]

    done, pending = await asyncio.wait(all_tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in done:
        if t is stop_task:
            logger.info("Shutdown requested")
        else:
            name = t.get_name()
            exc = t.exception()
            logger.warning("Task '%s' exited: %s", name, exc)

    for t in pending:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    await pool.close()
    logger.info("Comm Bridge stopped")


if __name__ == "__main__":
    asyncio.run(main())
