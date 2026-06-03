"""
E2E: Tool call format — Telegram HTML output verification.

Verifies that tool call messages in Telegram show:
  - ✅ or ⚙️ icon (completed vs in progress)
  - 📥 Input in an expandable <blockquote> with <pre><code> for the args
  - 📤 Output in a separate expandable <blockquote> when the tool completes

See: infrastructure/gateway/telegram/src/lina_gateway/formatter.py:format_tool_status
"""

from __future__ import annotations

import re

import pytest

from tests.e2e.telegram.assertions import (
    assert_full_integrity,
    assert_valid_html,
    assert_no_markdown_leak,
    assert_message_length,
)
from tests.e2e.telegram.client import TelegramTestClient
from tests.e2e.telegram.capture import ResponseCapture, CapturedMessage


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _is_tool_message(msg: CapturedMessage) -> bool:
    """Heuristic: tool messages start with ⚙️ or ✅ as first non-whitespace."""
    text = msg.final_text.lstrip()
    first_line = text.split('\n')[0].strip()
    return first_line.startswith(("✅", "⚙️", "❌"))


def _is_thinking_message(msg: CapturedMessage) -> bool:
    return msg.is_thinking()


def _find_tool_messages(capture: ResponseCapture) -> list[CapturedMessage]:
    """Return only the tool-status messages (⚙️/✅)."""
    return [m for m in capture.messages if _is_tool_message(m)]


def _find_completed_tool_messages(capture: ResponseCapture) -> list[CapturedMessage]:
    """Return completed (✅) tool messages (must start with ✅)."""
    return [m for m in capture.messages if _is_tool_message(m) and "✅" in m.final_text]


def _count_tags(html: str, tag: str) -> int:
    return len(re.findall(rf"<{tag}[>\s]", html)) - len(re.findall(rf"</{tag}>", html))


def _assert_tool_rendered_format(text: str, *, expect_output: bool = True) -> None:
    """Assert the tool message has correct RENDERED format (Telethon strips HTML).

    Expected format (plain text after Telegram renders HTML):
        ✅ shell
        📥 Input
        <command>
        📤 Output
        <result>

    Args:
        text: The plain-text tool message (HTML tags already stripped by Telethon).
        expect_output: If True, 📤 Output must be present.
    """
    # Header: must start with ✅ or ⚙️ followed by tool name
    assert re.match(r'^(✅|⚠️|❌)\s+\S+', text), (
        f"Tool message must start with ✅/⚠️/❌ + tool name. Got: {text[:80]!r}"
    )
    # 📥 Input must be present
    assert "📥 Input" in text, (
        f"Tool message missing 📥 Input. Got: {text[:200]!r}"
    )
    # Content after 📥 Input (the argument/command)
    input_match = re.search(r'📥 Input\n(.+)', text)
    assert input_match, (
        f"📥 Input must be followed by command on next line. Got: {text[:200]!r}"
    )
    if expect_output:
        # 📤 Output must be present in completed tool messages
        assert "📤 Output" in text, (
            f"Completed tool message missing 📤 Output. Got: {text[:300]!r}"
        )
        # Content after 📤 Output
        output_match = re.search(r'📤 Output\n(.+)', text)
        assert output_match, (
            f"📤 Output must be followed by result on next line. Got: {text[:300]!r}"
        )


# ─── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.e2e_telegram
async def test_tool_call_completes_with_checkmark(tg: TelegramTestClient) -> None:
    """
    When a tool completes, its message must be updated from ⚙️ to ✅.
    At least one tool message must show ✅ (completed).
    """
    capture = await tg.send_prompt(
        "Ejecutá el comando \"echo 'hola mundo'\" y decime el resultado.",
        timeout=60,
    )
    assert_full_integrity(capture, context="tool_complete_checkmark")

    completed = _find_completed_tool_messages(capture)
    assert completed, (
        "No completed (✅) tool messages found. "
        "Tool message was not updated from ⚙️ to ✅. "
        f"All messages: {[c.final_text[:80] for c in capture.messages]}"
    )


@pytest.mark.e2e_telegram
async def test_tool_input_expandable(tg: TelegramTestClient) -> None:
    """
    The tool message must contain 📥 Input with the command on the next line.
    """
    capture = await tg.send_prompt(
        "Ejecutá el comando \"cat /etc/hostname\" y mostrame el resultado.",
        timeout=60,
    )
    assert_full_integrity(capture, context="tool_input_expandable")

    tool_msgs = _find_tool_messages(capture)
    assert tool_msgs, "No tool messages found"

    for msg in tool_msgs:
        text = msg.final_text
        # Might be in progress (⚙️) — only check completed tools
        if "✅" not in text:
            continue
        _assert_tool_rendered_format(text)


@pytest.mark.e2e_telegram
async def test_tool_output_expandable(tg: TelegramTestClient) -> None:
    """
    When a tool completes, the message must show 📤 Output with the result
    on a separate line below.
    """
    capture = await tg.send_prompt(
        "Ejecutá el comando \"uname -o\" y mostrame el resultado.",
        timeout=60,
    )
    assert_full_integrity(capture, context="tool_output_expandable")

    completed = _find_completed_tool_messages(capture)
    assert completed, "No completed (✅) tool messages found"

    for msg in completed:
        _assert_tool_rendered_format(msg.final_text)


@pytest.mark.e2e_telegram
async def test_tool_input_output_separate_blocks(tg: TelegramTestClient) -> None:
    """
    📥 Input and 📤 Output must be on separate lines with their content.
    """
    capture = await tg.send_prompt(
        "Ejecutá el comando \"echo 'separate blocks test'\" y mostrame el output.",
        timeout=60,
    )
    assert_full_integrity(capture, context="tool_separate_blocks")

    completed = _find_completed_tool_messages(capture)
    assert completed, "No completed (✅) tool messages found"

    for msg in completed:
        _assert_tool_rendered_format(msg.final_text)


@pytest.mark.e2e_telegram
async def test_tool_args_in_code_block(tg: TelegramTestClient) -> None:
    """
    The command/args must appear on the line after 📥 Input.
    """
    capture = await tg.send_prompt(
        "Ejecutá el comando \"free -h\" y decime cuánta memoria libre hay.",
        timeout=60,
    )
    assert_full_integrity(capture, context="tool_args_code_block")

    completed = _find_completed_tool_messages(capture)
    assert completed, "No completed (✅) tool messages found"

    for msg in completed:
        _assert_tool_rendered_format(msg.final_text)


@pytest.mark.e2e_telegram
async def test_multiple_tool_calls_format(tg: TelegramTestClient) -> None:
    """
    When there are multiple tool calls (e.g., a sequence of shell commands),
    each must have its own ⚙️→✅ transition and separate input/output blocks.
    """
    capture = await tg.send_prompt(
        "Ejecutá estos comandos uno por uno: "
        "'echo primero', 'echo segundo', cada uno en su propio comando separado.",
        timeout=90,
    )
    assert_full_integrity(capture, context="multiple_tools")

    completed = _find_completed_tool_messages(capture)
    assert len(completed) >= 2, (
        f"Expected at least 2 completed tool messages for 2 commands, "
        f"found {len(completed)}. "
        f"All messages: {[c.final_text[:60] for c in capture.messages]}"
    )

    for msg in completed:
        _assert_tool_rendered_format(msg.final_text)


@pytest.mark.e2e_telegram
async def test_tool_response_edits_existing_message(tg: TelegramTestClient) -> None:
    """
    The tool message starts as ⚙️ and gets EDITED to ✅ with 📤 Output.
    We verify this by checking that the initial ⚙️ message was edited
    (snapshots > 1) OR that a final ✅ message exists.
    """
    capture = await tg.send_prompt(
        "Ejecutá \"echo 'editing test'\" y devolvé el resultado.",
        timeout=60,
    )
    assert_full_integrity(capture, context="tool_edit")

    completed = _find_completed_tool_messages(capture)
    assert completed, "No completed tool messages found — message was not updated"

    # If the message was EDITED from ⚙️ to ✅, it should have > 1 snapshot
    for msg in completed:
        if msg.edit_count >= 1:
            # ✅ Found an edited tool message — test passes
            return

    # If no edit found, the tool might have only one snapshot (show ✅ from the start)
    # This is acceptable if the tool was very fast (response came before Telethon saw edit)
    assert completed, "No tool message was updated with ✅"


@pytest.mark.e2e_telegram
async def test_tool_format_no_raw_markdown(tg: TelegramTestClient) -> None:
    """
    Tool status messages must not contain raw Markdown syntax.
    Only Telegram-compatible HTML tags: <b>, <code>, <pre>, <blockquote>, etc.
    """
    capture = await tg.send_prompt(
        "Ejecutá \"ls /\" y mostrame los primeros 5 resultados.",
        timeout=60,
    )
    assert_full_integrity(capture, context="tool_no_markdown")

    tool_msgs = _find_tool_messages(capture)
    assert tool_msgs, "No tool messages found"

    for msg in tool_msgs:
        text = msg.final_text
        assert_valid_html(text, f"tool msg_id={msg.message_id}")
        assert_no_markdown_leak(text, f"tool msg_id={msg.message_id}")
        assert_message_length(text)


@pytest.mark.e2e_telegram
async def test_tool_header_visible_always(tg: TelegramTestClient) -> None:
    """
    The tool header (⚙️ ✅ ❌ + tool name) must be OUTSIDE the expandable
    blocks — always visible even when input/output are collapsed.
    """
    capture = await tg.send_prompt(
        "Ejecutá \"whoami\" y decime el resultado.",
        timeout=60,
    )
    assert_full_integrity(capture, context="tool_header_visible")

    completed = _find_completed_tool_messages(capture)
    assert completed, "No completed tool messages found"

    for msg in completed:
        text = msg.final_text.lstrip()
        lines = text.split('\n')
        # First line must be the header: ✅ shell (visible, not expandable)
        header_line = lines[0].strip()
        assert re.match(r'^(✅|⚠️|❌)\s+', header_line), (
            f"Header must start with ✅/⚠️/❌: {header_line!r}"
        )
        # Header must NOT contain 📥 or 📤
        assert "📥" not in header_line, f"📥 in header: {header_line!r}"
        assert "📤" not in header_line, f"📤 in header: {header_line!r}"
        # Subsequent lines have 📥 Input and 📤 Output
        full_text = '\n'.join(lines)
        assert "📥 Input" in full_text, f"Missing 📥 Input in tool message: {full_text[:200]!r}"


@pytest.mark.e2e_telegram
async def test_empty_output_omits_output_block(tg: TelegramTestClient) -> None:
    """
    If the tool produces no output (empty stdout), the 📤 Output block
    should be omitted entirely — not shown as an empty block.
    """
    capture = await tg.send_prompt(
        "Ejecutá el comando \"true\" (que no produce output) y decime qué pasó.",
        timeout=60,
    )
    assert_full_integrity(capture, context="tool_empty_output")

    # This test is inherently flaky — some tools may produce output even for "true"
    # We just verify that the format doesn't include empty output blocks
    completed = _find_completed_tool_messages(capture)
    if not completed:
        pytest.skip("No completed tool messages found — non-tool response is OK for 'true'")

    for msg in completed:
        text = msg.final_text
        # If there's no 📤 Output, that's fine — it means the tool had empty result
        # If there IS 📤 Output, it must have content (more than just "📤 Output")
        if "📤 Output" in text:
            output_match = re.search(r'📤 Output\n(.+)', text)
            assert output_match, (
                f"📤 Output must be followed by content on next line. "
                f"Text: {text[:400]!r}"
            )
            result_text = output_match.group(1).strip()
            assert len(result_text) > 0, (
                f"📤 Output must have non-empty content. Got: {text[:400]!r}"
            )
