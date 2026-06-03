"""Entry point: python -m lina_gateway."""

import asyncio
import logging
import os
import signal
from contextlib import suppress

from .agent_notifier import AgentNotifier
from .boot_hook import on_boot, on_shutdown, record_balance_snapshot
from .bot import Bot
from .config import Config
from .observe import create_observer


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_run())


async def _run() -> None:
    cfg = Config()
    bot = Bot(cfg)

    # ── Boot notification (best-effort) ───────────────────────────────────────
    await on_boot(bot.tg, cfg.lina_db_url, cfg.notify_chat_ids)

    # ── Balance snapshot on startup (best-effort) ─────────────────────────────
    if cfg.lina_db_url and cfg.deepseek_api_key:
        asyncio.create_task(
            record_balance_snapshot(cfg.lina_db_url, cfg.deepseek_api_key, source="startup")
        )

    # ── Graceful shutdown via SIGTERM / SIGINT ────────────────────────────────
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    loop.add_signal_handler(signal.SIGINT, stop.set)

    observe_port = int(os.environ.get("OBSERVE_PORT", "9090"))
    observer = create_observer(
        port=observe_port,
        db_url=cfg.lina_db_url,
        goosed_url=cfg.goosed_url,
    )
    await observer.start()
    bot.set_observer(observer)
    observer.set_bot(bot)
    bot_task = loop.create_task(bot.run(), name="bot")
    stop_task = loop.create_task(stop.wait(), name="stop")

    # ── Agent notifier — proactive push of sub-agent status to Telegram ───────
    notifier_task: asyncio.Task | None = None
    if cfg.lina_db_url and cfg.notify_chat_ids and cfg.agent_poll_interval > 0:
        notifier = AgentNotifier(
            db_url=cfg.lina_db_url,
            tg=bot.tg,
            chat_ids=cfg.notify_chat_ids,
            poll_interval=cfg.agent_poll_interval,
        )
        notifier_task = loop.create_task(notifier.run(), name="agent-notifier")

    tasks_to_watch: set[asyncio.Task] = {bot_task, stop_task}
    if notifier_task is not None:
        tasks_to_watch.add(notifier_task)

    done, pending = await asyncio.wait(tasks_to_watch, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
        with suppress(asyncio.CancelledError):
            await t

    # Consume bot_task result to surface any unhandled exception in logs.
    if bot_task in done and not bot_task.cancelled():
        exc = bot_task.exception()
        if exc is not None:
            logging.getLogger(__name__).error("Bot task exited with error: %s", exc)

    # ── Shutdown notification (best-effort) ───────────────────────────────────
    # Only send "Reiniciándome" when we received an explicit stop signal.
    # If bot_task ended on its own (unexpected), skip to avoid noise.
    if stop_task in done:
        await on_shutdown(bot.tg, cfg.lina_db_url, cfg.notify_chat_ids)


if __name__ == "__main__":
    main()
