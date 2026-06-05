"""Multi-bot gateway entry point: python -m lina_gateway.

Runs N Bot instances in the same process (one per Telegram bot token).
Each bot connects to its own goosed endpoint and maintains independent state.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from .agent_notifier import AgentNotifier
from .boot_hook import on_boot, on_shutdown, record_balance_snapshot
from .bot import Bot
from .config import Config
from .observe import create_observer

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("Fatal error in main loop")
        raise


async def _run() -> None:
    cfg = Config()
    shared = cfg.shared
    bot_configs = cfg.bots

    logger.info("Multi-bot gateway starting — %d bot(s) configured", len(bot_configs))

    # ── Create Bot instances ─────────────────────────────────────────────────
    bots: list[Bot] = []
    bot_tasks: list[asyncio.Task] = []
    notifier_tasks: list[asyncio.Task] = []

    for bot_cfg in bot_configs:
        bot = Bot(bot_cfg, shared)
        bots.append(bot)

        # ── Observer per bot (each on a different WS port) ────────────────────
        agent_name = bot_cfg.name.lower()
        observer = create_observer(
            port=bot_cfg.observe_port,
            db_url=shared.lina_db_url,
            goosed_url=bot_cfg.goosed_url,
            agent=agent_name,
        )
        await observer.start()
        bot.set_observer(observer)
        observer.set_bot(bot)

        # ── Fire off the polling loop ────────────────────────────────────────
        bot_tasks.append(asyncio.create_task(bot.run(), name=f"bot-{agent_name}"))

        # ── Boot notification ────────────────────────────────────────────────
        await on_boot(bot.tg, shared.lina_db_url, bot_cfg.notify_chat_ids)

        # ── Balance snapshot on startup ──────────────────────────────────────
        if shared.lina_db_url and shared.deepseek_api_key:
            asyncio.create_task(
                record_balance_snapshot(
                    shared.lina_db_url, shared.deepseek_api_key, source=f"startup-{agent_name}"
                )
            )

        # ── Agent notifier (one per bot that has notify_chat_ids) ────────────
        if shared.lina_db_url and bot_cfg.notify_chat_ids and shared.agent_poll_interval > 0:
            notifier = AgentNotifier(
                db_url=shared.lina_db_url,
                tg=bot.tg,
                chat_ids=bot_cfg.notify_chat_ids,
                poll_interval=shared.agent_poll_interval,
            )
            notifier_tasks.append(
                asyncio.create_task(notifier.run(), name=f"notifier-{agent_name}")
            )

    # ── Graceful shutdown via SIGTERM / SIGINT ────────────────────────────────
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    try:
        loop.add_signal_handler(signal.SIGTERM, stop.set)
        loop.add_signal_handler(signal.SIGINT, stop.set)
    except (NotImplementedError, RuntimeError) as exc:
        logger.warning("Signal handlers not available (no TTY?): %s", exc)
    stop_task = asyncio.create_task(stop.wait(), name="stop")

    # ── Wait for any task to complete (or stop signal) ───────────────────────
    all_tasks = [*bot_tasks, stop_task, *notifier_tasks]
    done, pending = await asyncio.wait(all_tasks, return_when=asyncio.FIRST_COMPLETED)

    # ── Debug: log which task(s) completed ────────────────────────────────────
    for t in done:
        name = t.get_name() if hasattr(t, 'get_name') else str(t)
        cancelled = t.cancelled()
        exc = None
        if not cancelled:
            try:
                exc = t.exception()
            except (asyncio.InvalidStateError, Exception):
                pass
        logger.warning("Task '%s' completed: cancelled=%s exception=%s", name, cancelled, exc)

    # ── Cancel everything else ───────────────────────────────────────────────
    for t in pending:
        t.cancel()
        with suppress(asyncio.CancelledError):
            await t

    # ── Surface any bot errors ───────────────────────────────────────────────
    for bot_task in bot_tasks:
        if bot_task in done and not bot_task.cancelled():
            exc = bot_task.exception()
            if exc is not None:
                logger.error("Bot task exited with error: %s", exc)

    # ── Shutdown notifications ───────────────────────────────────────────────
    if stop_task in done:
        for bot in bots:
            await on_shutdown(bot.tg, shared.lina_db_url, [])
