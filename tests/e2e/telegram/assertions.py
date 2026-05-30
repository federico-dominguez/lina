"""
Assertion helpers for Telegram E2E tests.

All functions raise AssertionError with a descriptive message on failure.
Designed to be used directly in pytest scenarios.
"""

from __future__ import annotations

import re

from .capture import ResponseCapture

# ── HTML integrity ────────────────────────────────────────────────────────────

# Tags supported by Telegram HTML mode
_TELEGRAM_ALLOWED_TAGS = frozenset(
    [
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "code",
        "pre",
        "a",
        "tg-spoiler",
        "blockquote",
    ]
)

_TAG_RE = re.compile(r"<(/?)(\w[\w-]*)(\s[^>]*)?>", re.IGNORECASE)
_OPEN_CLOSE_RE = re.compile(r"<(/?)(\w[\w-]*)[^>]*>", re.IGNORECASE)
# Unescaped bare & < > (not inside a tag, not already an entity)
_BARE_AMP_RE = re.compile(r"&(?!(?:#\d+|#x[\da-fA-F]+|[a-zA-Z]\w*);)")
_BARE_LT_RE = re.compile(r"<(?![/a-zA-Z!])")
_BARE_GT_RE = re.compile(r"(?<![=\w'\"})>])>")

# Markdown patterns that should NOT appear in Telegram HTML-mode output
_MARKDOWN_LEAKS = [
    (re.compile(r"\*\*[^*]+\*\*"), "markdown bold **...**"),
    (re.compile(r"(?<!\*)\*(?!\*)[^*\n]+(?<!\*)\*(?!\*)"), "markdown italic *...*"),
    (re.compile(r"^#{1,6}\s", re.MULTILINE), "markdown header #"),
    (re.compile(r"^---+$", re.MULTILINE), "markdown hr ---"),
    (re.compile(r"^\|.+\|.+\|", re.MULTILINE), "markdown table |...|"),
    (re.compile(r"^```", re.MULTILINE), "markdown fenced code ```"),
]


def assert_valid_html(text: str, context: str = "") -> None:
    """Assert the text contains only Telegram-supported HTML and no bare entities."""
    ctx = f" [{context}]" if context else ""

    # Check for unsupported tags
    for m in _TAG_RE.finditer(text):
        tag_name = m.group(2).lower()
        if tag_name not in _TELEGRAM_ALLOWED_TAGS:
            raise AssertionError(
                f"Unsupported Telegram HTML tag <{tag_name}>{ctx} in: {text[:200]!r}"
            )

    # Check for unclosed tags (simple stack-based)
    stack: list[str] = []
    void_tags = frozenset(["br", "hr", "img"])
    for m in _OPEN_CLOSE_RE.finditer(text):
        is_close = m.group(1) == "/"
        tag = m.group(2).lower()
        if tag in void_tags:
            continue
        if is_close:
            if stack and stack[-1] == tag:
                stack.pop()
            # Tolerate mismatched closes (Telegram is lenient)
        else:
            stack.append(tag)
    if stack:
        raise AssertionError(f"Unclosed HTML tags {stack}{ctx} in: {text[:300]!r}")

    # Check for unescaped & (not part of entity)
    bare_amps = _BARE_AMP_RE.findall(text)
    if bare_amps:
        raise AssertionError(
            f"Unescaped '&' found{ctx} — should be &amp;. Text: {text[:200]!r}"
        )


def assert_no_markdown_leak(text: str, context: str = "") -> None:
    """Assert the text contains no raw Markdown syntax (Telegram uses HTML mode)."""
    ctx = f" [{context}]" if context else ""
    for pattern, description in _MARKDOWN_LEAKS:
        if pattern.search(text):
            raise AssertionError(
                f"Markdown leak detected: {description}{ctx}. Text: {text[:200]!r}"
            )


def assert_no_open_tags_at_split(chunks: list[str]) -> None:
    """Assert that no message chunk ends with an unclosed HTML tag."""
    for i, chunk in enumerate(chunks):
        stack: list[str] = []
        for m in _OPEN_CLOSE_RE.finditer(chunk):
            is_close = m.group(1) == "/"
            tag = m.group(2).lower()
            if tag in frozenset(["br", "hr"]):
                continue
            if is_close:
                if stack and stack[-1] == tag:
                    stack.pop()
            else:
                stack.append(tag)
        if stack:
            raise AssertionError(
                f"Chunk {i} ends with unclosed tags {stack}. "
                f"Chunk tail: {chunk[-200:]!r}"
            )


def assert_message_length(text: str, max_len: int = 4096, context: str = "") -> None:
    """Assert text does not exceed Telegram's max message length."""
    ctx = f" [{context}]" if context else ""
    if len(text) > max_len:
        raise AssertionError(f"Message too long: {len(text)} chars > {max_len}{ctx}")


# ── Latency ──────────────────────────────────────────────────────────────────


def assert_ttft_under(capture: ResponseCapture, seconds: float) -> None:
    """Assert TTFT (time to first token) is below *seconds*."""
    ttft = capture.ttft
    if ttft is None:
        raise AssertionError("No messages captured — cannot compute TTFT")
    if ttft > seconds:
        raise AssertionError(f"TTFT too slow: {ttft:.2f}s > {seconds}s threshold")


def assert_ttlt_under(capture: ResponseCapture, seconds: float) -> None:
    """Assert TTLT (time to last token) is below *seconds*."""
    ttlt = capture.ttlt
    if ttlt is None:
        raise AssertionError("Capture not sealed — cannot compute TTLT")
    if ttlt > seconds:
        raise AssertionError(f"TTLT too slow: {ttlt:.2f}s > {seconds}s threshold")


def assert_typewriter_cadence(
    capture: ResponseCapture,
    min_interval_ms: float = 100,
    max_interval_ms: float = 3000,
) -> None:
    """Assert edit intervals are within [min_interval_ms, max_interval_ms] ms."""
    intervals = capture.all_edit_intervals_ms
    if not intervals:
        return  # Single-edit or no streaming — nothing to check
    too_fast = [x for x in intervals if x < min_interval_ms]
    too_slow = [x for x in intervals if x > max_interval_ms]
    if too_fast:
        raise AssertionError(
            f"Typewriter cadence too fast: {len(too_fast)} intervals < {min_interval_ms}ms "
            f"(min={min(too_fast):.0f}ms)"
        )
    if too_slow:
        raise AssertionError(
            f"Typewriter cadence too slow: {len(too_slow)} intervals > {max_interval_ms}ms "
            f"(max={max(too_slow):.0f}ms)"
        )


# ── Thinking separation ───────────────────────────────────────────────────────


def assert_thinking_separated(capture: ResponseCapture) -> None:
    """Assert thinking block and final response are in separate messages."""
    thinking = capture.thinking_message
    if thinking is None:
        return  # No thinking block — nothing to check

    final_msgs = capture.final_messages
    if not final_msgs:
        raise AssertionError(
            "Bot sent a thinking message but no final response message was captured"
        )

    thinking_id = thinking.message_id
    for final_msg in final_msgs:
        if final_msg.message_id == thinking_id:
            raise AssertionError(
                f"Thinking block and final response share the same message_id={thinking_id}. "
                "They must be separate messages."
            )


def assert_thinking_not_in_final(capture: ResponseCapture) -> None:
    """Assert the final response doesn't contain chain-of-thought fragments."""
    final_text = capture.final_text
    # Look for thinking emoji in the final (non-thinking) messages
    if "💭" in final_text:
        raise AssertionError(
            f"Thinking emoji 💭 found in final response: {final_text[:200]!r}"
        )


def assert_thinking_max_length(capture: ResponseCapture, max_chars: int = 800) -> None:
    """Assert thinking block content is within the max_chars limit (AGENTS.md §3).

    Measures only the thinking *content*, excluding:
    - The header line ("💭 Razonando...\\n") that the formatter prepends.
    - The trailing "\\n…" truncation suffix added by the formatter.
    """
    thinking = capture.thinking_message
    if thinking is None:
        return
    text = thinking.final_text
    # Strip the header line ("💭 Razonando...\n") before measuring content length.
    content = text.split("\n", 1)[1] if "\n" in text else text
    # Strip the trailing truncation suffix "\n…" if present.
    if content.endswith("\n…"):
        content = content[:-2]
    # Telegram strips HTML tags and returns plain text; the plain text is
    # generally shorter than the raw markdown source.  We allow a small buffer
    # (+30 chars) for the handful of patterns (e.g. headings) that add newlines
    # after conversion: "# Title" (9 chars) → rendered "\nTitle\n\n" (11 chars).
    effective_limit = max_chars + 30
    if len(content) > effective_limit:
        raise AssertionError(
            f"Thinking block too long: {len(content)} chars > {effective_limit} "
            f"(base limit {max_chars} + 30 formatting overhead). "
            f"Content start: {content[:100]!r}"
        )


# ── Content checks ────────────────────────────────────────────────────────────


def assert_contains(
    capture: ResponseCapture, substring: str, context: str = ""
) -> None:
    """Assert the final response contains *substring*."""
    ctx = f" [{context}]" if context else ""
    if substring not in capture.final_text:
        raise AssertionError(
            f"Expected {substring!r} not found in response{ctx}. "
            f"Got: {capture.final_text[:300]!r}"
        )


def assert_contains_tag(capture: ResponseCapture, tag: str, context: str = "") -> None:
    """Assert the final response contains the given HTML tag."""
    ctx = f" [{context}]" if context else ""
    pattern = f"<{tag}"
    if pattern not in capture.final_text:
        raise AssertionError(
            f"Expected HTML tag <{tag}> not found in response{ctx}. "
            f"Got: {capture.final_text[:300]!r}"
        )


def assert_not_empty(capture: ResponseCapture) -> None:
    """Assert the bot returned at least one message."""
    if capture.is_empty():
        raise AssertionError("No messages captured from bot — response is empty")
    if not capture.final_text.strip():
        raise AssertionError("Bot replied but final text is blank")


# ── Composite: full message integrity check ───────────────────────────────────


def assert_full_integrity(capture: ResponseCapture, context: str = "") -> None:
    """Run all integrity checks on a capture: HTML validity, markdown leaks,
    message lengths, no open tags at split boundaries."""
    assert_not_empty(capture)

    all_messages = capture.messages
    chunks = [m.final_text for m in all_messages]

    for msg in all_messages:
        ctx = f"{context} msg_id={msg.message_id}"
        assert_valid_html(msg.final_text, ctx)
        assert_no_markdown_leak(msg.final_text, ctx)
        assert_message_length(msg.final_text, context=ctx)

    if len(chunks) > 1:
        assert_no_open_tags_at_split(chunks)


# ── Thinking bubble lifecycle ─────────────────────────────────────────────────


def assert_thinking_preceded_body(capture: ResponseCapture, context: str = "") -> None:
    """Thinking message must have arrived (first_ts) BEFORE the body message."""
    ctx = f" [{context}]" if context else ""
    thinking = capture.thinking_message
    finals = capture.final_messages
    if thinking is None or not finals:
        return  # Not enough data — caller should also run assert_thinking_separated
    body = finals[0]
    assert thinking.first_ts < body.first_ts, (
        f"Thinking arrived at t={thinking.first_ts:.3f}s but body arrived at "
        f"t={body.first_ts:.3f}s — thinking should precede body{ctx}"
    )


def assert_thinking_message_id_before_body(
    capture: ResponseCapture, context: str = ""
) -> None:
    """Thinking message_id < body message_id (thinking was sent first in the chat timeline)."""
    ctx = f" [{context}]" if context else ""
    thinking = capture.thinking_message
    finals = capture.final_messages
    if thinking is None or not finals:
        return
    assert thinking.message_id < finals[0].message_id, (
        f"Thinking msg_id={thinking.message_id} should be < "
        f"body msg_id={finals[0].message_id}{ctx}"
    )


def assert_thinking_realtime_updated(
    capture: ResponseCapture,
    min_edits: int = 1,
    context: str = "",
) -> None:
    """Thinking bubble must have been edited at least *min_edits* times (live growth)."""
    ctx = f" [{context}]" if context else ""
    thinking = capture.thinking_message
    if thinking is None:
        return  # No thinking — skip
    assert thinking.edit_count >= min_edits, (
        f"Thinking bubble has {thinking.edit_count} edit(s), expected ≥ {min_edits}{ctx}. "
        "The thinking block may not be growing in real time."
    )


def assert_thinking_sealed_before_body(
    capture: ResponseCapture,
    tolerance_s: float = 1.0,
    context: str = "",
) -> None:
    """Thinking bubble's last edit must happen no later than body's first appearance.

    A tolerance of *tolerance_s* accounts for network/async jitter.
    """
    ctx = f" [{context}]" if context else ""
    thinking = capture.thinking_message
    finals = capture.final_messages
    if thinking is None or not finals:
        return
    body_first_ts = finals[0].first_ts
    thinking_last_ts = thinking.last_ts
    assert thinking_last_ts <= body_first_ts + tolerance_s, (
        f"Thinking was still being updated ({thinking_last_ts:.3f}s) "
        f"after body arrived ({body_first_ts:.3f}s){ctx}. "
        "The thinking bubble may not have been sealed before the body message."
    )
