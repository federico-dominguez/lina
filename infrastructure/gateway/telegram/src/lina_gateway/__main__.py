"""Entry point: python -m lina_gateway."""

import asyncio
import logging
import signal
from contextlib import suppress

from .boot_hook import on_boot, on_shutdown, record_balance_snapshot
from .bot import Bot
from .config import Config


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

    bot_task = loop.create_task(bot.run())
    stop_task = loop.create_task(stop.wait())

    done, pending = await asyncio.wait({bot_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
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
