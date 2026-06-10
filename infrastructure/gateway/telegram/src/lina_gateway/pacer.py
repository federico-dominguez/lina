"""
Streaming bubble pacer.

Port of the `StreamingBubble` / `run_pacer` logic in handler.rs.

A Pacer drives `editMessageText` calls at a steady rate (1.5 s by default),
decoupling the bursty arrival of LLM chunks from the steady display cadence.
The sliding-window trim keeps long work-log bubbles readable by showing only
the last N lines.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

WORK_LOG_WINDOW_LINES = 50


def trim_work_log(text: str) -> str:
    """Keep only the last WORK_LOG_WINDOW_LINES lines, prepend '[…]' when trimmed."""
    lines = text.splitlines()
    if len(lines) > WORK_LOG_WINDOW_LINES:
        return "[…]\n" + "\n".join(lines[-WORK_LOG_WINDOW_LINES:])
    return text


EditFn = Callable[[str, str, bool], Awaitable[None]]
"""Type alias: async fn(body: str, thinking: str, sealed: bool) → None"""


class StreamingBubble:
    """A Telegram message that is live-edited during LLM streaming.

    Usage::

        bubble = StreamingBubble(tick=1.5, edit_fn=my_edit_fn)
        bubble.start()
        bubble.update(thinking="…", body="")
        bubble.update(thinking="…", body="partial")
        await bubble.seal()  # waits for final edit with sealed=True
    """

    def __init__(self, tick: float, edit_fn: EditFn) -> None:
        self._tick = tick
        self._edit_fn = edit_fn
        self._thinking = ""
        self._body = ""
        self._sealed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def update(self, thinking: str, body: str) -> None:
        self._thinking = thinking
        self._body = body

    async def seal(self) -> None:
        """Signal that the LLM turn is complete and wait for the final edit."""
        self._sealed.set()
        if self._task:
            await self._task

    async def _run(self) -> None:
        last_hash: int = -1

        while not self._sealed.is_set():
            try:
                await asyncio.wait_for(asyncio.shield(self._sealed.wait()), timeout=self._tick)
            except TimeoutError:
                pass

            h = hash((self._thinking, self._body))
            if h != last_hash:
                windowed_thinking = trim_work_log(self._thinking)
                await self._edit_fn(self._body, windowed_thinking, False)
                last_hash = h

        # Final flush with sealed=True (switches blockquote to expandable)
        await self._edit_fn(self._body, self._thinking, True)
