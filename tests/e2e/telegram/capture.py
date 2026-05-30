"""
ResponseCapture — collects and indexes all messages + edits from the bot
during a single prompt/response cycle.

Usage::

    capture = ResponseCapture()
    capture.on_message(msg_id=42, text="💭 Razonando...", ts=time.monotonic())
    capture.on_edit(msg_id=42, text="💭 Razonando... más", ts=time.monotonic())
    capture.on_message(msg_id=43, text="Hola", ts=time.monotonic())
    capture.seal(ts=time.monotonic())

    capture.ttft            # seconds to first token
    capture.ttlt            # seconds to last token
    capture.thinking_message  # CapturedMessage with 💭
    capture.final_messages    # list of CapturedMessage (may be >1 if split)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CapturedMessage:
    message_id: int
    snapshots: list[tuple[float, str]] = field(default_factory=list)
    """List of (monotonic_timestamp, text) — one entry per send/edit."""

    @property
    def first_ts(self) -> float:
        return self.snapshots[0][0] if self.snapshots else 0.0

    @property
    def last_ts(self) -> float:
        return self.snapshots[-1][0] if self.snapshots else 0.0

    @property
    def final_text(self) -> str:
        return self.snapshots[-1][1] if self.snapshots else ""

    @property
    def edit_count(self) -> int:
        return max(0, len(self.snapshots) - 1)

    @property
    def edit_intervals_ms(self) -> list[float]:
        """Milliseconds between consecutive edits."""
        result = []
        for i in range(1, len(self.snapshots)):
            dt = (self.snapshots[i][0] - self.snapshots[i - 1][0]) * 1000
            result.append(dt)
        return result

    def is_thinking(self) -> bool:
        """Heuristic: thinking bubbles start with the thinking indicator."""
        text = self.final_text
        return text.startswith("💭") or text.startswith("⏳")


class ResponseCapture:
    """Accumulates bot messages and edits for a single prompt cycle."""

    def __init__(self, prompt_sent_at: Optional[float] = None) -> None:
        self._prompt_sent_at: float = prompt_sent_at or time.monotonic()
        self._messages: dict[int, CapturedMessage] = {}
        self._arrival_order: list[int] = []  # message_ids in arrival order
        self._sealed_at: Optional[float] = None

    # ── Ingestion ────────────────────────────────────────────────────────────

    def on_message(self, msg_id: int, text: str, ts: Optional[float] = None) -> None:
        """Record a new message from the bot."""
        t = ts or time.monotonic()
        if msg_id not in self._messages:
            self._messages[msg_id] = CapturedMessage(message_id=msg_id)
            self._arrival_order.append(msg_id)
        self._messages[msg_id].snapshots.append((t, text))

    def on_edit(self, msg_id: int, text: str, ts: Optional[float] = None) -> None:
        """Record an edit to an existing bot message."""
        t = ts or time.monotonic()
        if msg_id not in self._messages:
            # Edit arrived before the initial send (race); create it
            self._messages[msg_id] = CapturedMessage(message_id=msg_id)
            self._arrival_order.append(msg_id)
        self._messages[msg_id].snapshots.append((t, text))

    def seal(self, ts: Optional[float] = None) -> None:
        """Mark capture as complete (no more activity expected)."""
        self._sealed_at = ts or time.monotonic()

    # ── Derived metrics ───────────────────────────────────────────────────────

    @property
    def messages(self) -> list[CapturedMessage]:
        return [self._messages[mid] for mid in self._arrival_order]

    @property
    def thinking_message(self) -> Optional[CapturedMessage]:
        for mid in self._arrival_order:
            msg = self._messages[mid]
            if msg.is_thinking():
                return msg
        return None

    @property
    def final_messages(self) -> list[CapturedMessage]:
        """Non-thinking messages — may be multiple if bot split the reply."""
        return [self._messages[mid] for mid in self._arrival_order if not self._messages[mid].is_thinking()]

    @property
    def final_text(self) -> str:
        """Concatenation of all final message texts."""
        return "\n".join(m.final_text for m in self.final_messages)

    @property
    def ttft(self) -> Optional[float]:
        """Time to First Token: seconds from prompt_sent to first bot message."""
        if not self._messages:
            return None
        first_ts = min(m.first_ts for m in self._messages.values())
        return first_ts - self._prompt_sent_at

    @property
    def ttlt(self) -> Optional[float]:
        """Time to Last Token: seconds from prompt_sent to last bot activity."""
        if not self._sealed_at:
            return None
        return self._sealed_at - self._prompt_sent_at

    @property
    def all_edit_intervals_ms(self) -> list[float]:
        """All edit intervals across all messages, flat list."""
        result: list[float] = []
        for msg in self._messages.values():
            result.extend(msg.edit_intervals_ms)
        return result

    @property
    def total_edit_count(self) -> int:
        return sum(m.edit_count for m in self._messages.values())

    @property
    def total_chars(self) -> int:
        return sum(len(m.final_text) for m in self.final_messages)

    @property
    def approx_tokens_per_second(self) -> Optional[float]:
        """Very rough estimate: chars / 4 / ttlt."""
        ttlt = self.ttlt
        if not ttlt or ttlt <= 0:
            return None
        return (self.total_chars / 4) / ttlt

    def is_empty(self) -> bool:
        return len(self._messages) == 0

    def summary(self) -> str:
        """Human-readable one-liner for debugging."""
        ttft = f"{self.ttft:.2f}s" if self.ttft is not None else "N/A"
        ttlt = f"{self.ttlt:.2f}s" if self.ttlt is not None else "N/A"
        edits = self.total_edit_count
        msgs = len(self.final_messages)
        chars = self.total_chars
        thinking = "yes" if self.thinking_message else "no"
        return (
            f"ttft={ttft} ttlt={ttlt} edits={edits} "
            f"final_msgs={msgs} chars={chars} thinking={thinking}"
        )
