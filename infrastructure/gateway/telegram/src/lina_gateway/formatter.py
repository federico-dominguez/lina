"""
Telegram HTML formatter.

Port of goose/src/gateway/telegram_format.rs:
  - markdown_to_telegram_html  — Markdown → Telegram HTML subset
  - format_with_thinking       — thinking block + body
  - format_tool_status         — tool-call status card
  - split_message              — split at ≤4096 chars
  - strip_html_tags            — HTML→plain fallback
  - strip_ansi                 — remove ANSI escape sequences
  - truncate_chars             — safe char-level truncation
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ─── Constants ───────────────────────────────────────────────────────────────

MAX_MESSAGE_LENGTH = 4096
MAX_THINKING_CHARS = 800
INLINE_TOOL_RESULT_MAX_CHARS = 1500
PRE_CHAR_THRESHOLD = 400
PRE_LINE_THRESHOLD = 10

# ─── ANSI stripping ──────────────────────────────────────────────────────────

_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"          # CSI sequence
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC sequence
    r"|\x1b[@-Z\\-_]"                   # 2-byte escape
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from terminal output."""
    return _ANSI_RE.sub("", text)


# ─── HTML escaping ───────────────────────────────────────────────────────────

_HTML_ESCAPE = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})


def escape_html(text: str) -> str:
    return text.translate(_HTML_ESCAPE)


# ─── HTML stripping (fallback to plain text) ─────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_ENTITIES = {
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&amp;": "&",
}


def strip_html_tags(html: str) -> str:
    plain = _TAG_RE.sub("", html)
    for entity, char in _ENTITIES.items():
        plain = plain.replace(entity, char)
    return plain


# ─── char-level truncation ───────────────────────────────────────────────────


def truncate_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


# ─── Message splitting ───────────────────────────────────────────────────────


def split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split *text* into chunks of at most *max_len* characters.

    Prefers splitting at newline, then space, then hard-cuts at max_len.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        slice_ = remaining[:max_len]
        split_at = slice_.rfind("\n")
        if split_at < 0:
            split_at = slice_.rfind(" ")
        if split_at < 0:
            split_at = max_len
        else:
            split_at += 1  # include the delimiter in the left chunk
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    return chunks


# ─── looks_like_diff ─────────────────────────────────────────────────────────


def looks_like_diff(text: str) -> bool:
    t = text.lstrip()
    if t.startswith("diff --git ") or t.startswith("--- a/") or t.startswith("--- /dev/null"):
        return True
    if t.startswith("@@ ") and " @@" in t:
        return True
    header_lines = [ln for ln in text.splitlines()[:4] if ln.startswith("+++ ") or ln.startswith("--- ")]
    return len(header_lines) >= 2


# ─── lang_for_tool ───────────────────────────────────────────────────────────

_LANG_MAP = {
    "shell": "bash",
    "bash": "bash",
    "__execute": "bash",
    "python": "python",
    "sql": "sql",
}


def lang_for_tool(tool_name: str) -> str | None:
    n = tool_name.lower()
    for key, lang in _LANG_MAP.items():
        if key in n:
            return lang
    return None


# ─── code_block ──────────────────────────────────────────────────────────────


def code_block(lang: str | None, body: str) -> str:
    body_esc = escape_html(body)
    if lang:
        return f'<pre><code class="language-{escape_html(lang)}">{body_esc}</code></pre>'
    return f"<pre><code>{body_esc}</code></pre>"


# ─── wrap_long_pre_blocks ────────────────────────────────────────────────────


def wrap_long_pre_blocks(html: str) -> str:
    """Wrap large <pre> blocks in <blockquote expandable> for Telegram."""
    out_parts: list[str] = []
    rest = html
    while True:
        start = rest.find("<pre>")
        if start < 0:
            out_parts.append(rest)
            break
        out_parts.append(rest[:start])
        after_start = rest[start:]
        end_rel = after_start.find("</pre>")
        if end_rel < 0:
            out_parts.append(after_start)
            break
        end = end_rel + len("</pre>")
        block = after_start[:end]
        inner_len = len(block)
        line_count = block.count("\n") + 1
        current = "".join(out_parts).rstrip()
        already_wrapped = current.endswith("<blockquote expandable>") or current.endswith("<blockquote>")
        if not already_wrapped and (inner_len > PRE_CHAR_THRESHOLD or line_count > PRE_LINE_THRESHOLD):
            out_parts.append(f"<blockquote expandable>{block}</blockquote>")
        else:
            out_parts.append(block)
        rest = after_start[end:]
    return "".join(out_parts)


# ─── collapse_newlines ───────────────────────────────────────────────────────

_TRIPLE_NL = re.compile(r"\n{3,}")


def collapse_newlines(text: str) -> str:
    return _TRIPLE_NL.sub("\n\n", text)


# ─── lang sanitization ───────────────────────────────────────────────────────

_LANG_ALIASES = {
    "sh": "bash",
    "shell": "bash",
    "zsh": "bash",
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "rs": "rust",
    "yml": "yaml",
    "dockerfile": "docker",
}

_SAFE_LANG_RE = re.compile(r"[^a-z0-9\-+]")


def sanitize_lang(raw: str) -> str | None:
    first = raw.split()[0] if raw.split() else ""
    cleaned = _SAFE_LANG_RE.sub("", first.lower())[:30]
    if not cleaned:
        return None
    return _LANG_ALIASES.get(cleaned, cleaned)


# ─── markdown_to_telegram_html ───────────────────────────────────────────────

# We implement a simple state-machine parser instead of pulling in pulldown-cmark.
# Supports: bold, italic, strikethrough, code (inline + block), links,
#           unordered/ordered lists, blockquotes, headings, horizontal rules,
#           and tables (rendered as <pre><code>).

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_ITALIC_RE = re.compile(r"\*(.+?)\*|_(.+?)_", re.DOTALL)
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
_LINK_RE = re.compile(r"\[(.+?)\]\((.+?)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_UL_RE = re.compile(r"^(\s*)[*\-+]\s+(.+)$")
_OL_RE = re.compile(r"^(\s*)\d+\.\s+(.+)$")
_BQ_RE = re.compile(r"^>\s*(.*)$")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_TABLE_SEP_RE = re.compile(r"^\|[-| :]+\|$")


@dataclass
class _MarkdownState:
    in_code_block: bool = False
    code_lang: str | None = None
    code_buf: list[str] = field(default_factory=list)
    in_table: bool = False
    table_rows: list[list[str]] = field(default_factory=list)
    header_row_count: int = 0
    output: list[str] = field(default_factory=list)


def _inline_format(text: str) -> str:
    """Apply inline formatting (bold, italic, code, links, strike)."""
    # Links first to avoid mangling URLs
    text = _LINK_RE.sub(lambda m: f'<a href="{escape_html(m.group(2))}">{escape_html(m.group(1))}</a>', text)
    text = _INLINE_CODE.sub(lambda m: f"<code>{escape_html(m.group(1))}</code>", text)
    text = _BOLD_RE.sub(lambda m: f"<b>{escape_html(m.group(1) or m.group(2))}</b>", text)
    text = _ITALIC_RE.sub(lambda m: f"<i>{escape_html(m.group(1) or m.group(2))}</i>", text)
    text = _STRIKE_RE.sub(lambda m: f"<s>{escape_html(m.group(1))}</s>", text)
    return text


def _cell_display_width(s: str) -> int:
    w = 0
    for ch in s:
        ew = unicodedata.east_asian_width(ch)
        w += 2 if ew in ("W", "F") else 1
    return w


def _render_table(rows: list[list[str]], header_rows: int) -> str:
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    if cols == 0:
        return ""
    col_widths = [1] * cols
    for row in rows:
        for i, cell in enumerate(row):
            if i < cols:
                col_widths[i] = max(col_widths[i], _cell_display_width(cell))

    def border(left: str, mid: str, right: str, fill: str = "─") -> str:
        parts = [fill * (w + 2) for w in col_widths]
        return left + mid.join(parts) + right

    top = border("┌", "┬", "┐")
    sep = border("├", "┼", "┤")
    bot = border("└", "┴", "┘")

    lines = [top]
    for row_idx, row in enumerate(rows):
        if row_idx > 0 and row_idx == header_rows:
            lines.append(sep)
        cells = []
        for col_idx, width in enumerate(col_widths):
            cell = row[col_idx] if col_idx < len(row) else ""
            pad = width - _cell_display_width(cell)
            cells.append(f" {escape_html(cell)}{' ' * (pad + 1)}")
        lines.append("│" + "│".join(cells) + "│")
    lines.append(bot)
    return "<pre><code>" + "\n".join(lines) + "\n</code></pre>"


def markdown_to_telegram_html(markdown: str) -> str:  # noqa: C901 (complex but mirrors the Rust)
    """Convert Markdown to Telegram-compatible HTML.

    Supports the subset that Telegram renders: bold, italic, strikethrough,
    inline code, code blocks (with optional language), links, headings,
    unordered/ordered lists, blockquotes, horizontal rules, and tables.
    """
    state = _MarkdownState()
    lines = markdown.splitlines()

    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.rstrip()

        # ── Code block ───────────────────────────────────────────────────
        if line.startswith("```"):
            if state.in_code_block:
                # End of code block
                body = "\n".join(state.code_buf)
                # Strip ANSI from shell-ish blocks
                if state.code_lang in ("bash", "shell", "sh", "console"):
                    body = strip_ansi(body)
                state.output.append(code_block(state.code_lang, body))
                state.output.append("\n\n")
                state.in_code_block = False
                state.code_buf = []
                state.code_lang = None
            else:
                # Flush pending table
                if state.in_table:
                    state.output.append(_render_table(state.table_rows, state.header_row_count))
                    state.output.append("\n")
                    state.in_table = False
                    state.table_rows = []
                    state.header_row_count = 0
                state.in_code_block = True
                lang_raw = line[3:].strip()
                state.code_lang = sanitize_lang(lang_raw) if lang_raw else None
            i += 1
            continue

        if state.in_code_block:
            state.code_buf.append(raw_line)
            i += 1
            continue

        # ── Table ────────────────────────────────────────────────────────
        if _TABLE_ROW_RE.match(line):
            if _TABLE_SEP_RE.match(line):
                # This is the separator row — mark header boundary
                state.header_row_count = len(state.table_rows)
                i += 1
                continue
            state.in_table = True
            cells = [c.strip() for c in line.strip("|").split("|")]
            state.table_rows.append(cells)
            i += 1
            continue
        elif state.in_table:
            state.output.append(_render_table(state.table_rows, state.header_row_count))
            state.output.append("\n")
            state.in_table = False
            state.table_rows = []
            state.header_row_count = 0

        # ── Horizontal rule ──────────────────────────────────────────────
        if _HR_RE.match(line):
            state.output.append("———\n")
            i += 1
            continue

        # ── Heading ──────────────────────────────────────────────────────
        m = _HEADING_RE.match(line)
        if m:
            text = _inline_format(escape_html(m.group(2)))
            state.output.append(f"\n<b>{text}</b>\n\n")
            i += 1
            continue

        # ── Blockquote ───────────────────────────────────────────────────
        m = _BQ_RE.match(line)
        if m:
            inner = _inline_format(escape_html(m.group(1)))
            state.output.append(f"<blockquote>{inner}</blockquote>\n\n")
            i += 1
            continue

        # ── Unordered list item ──────────────────────────────────────────
        m = _UL_RE.match(line)
        if m:
            inner = _inline_format(escape_html(m.group(2)))
            state.output.append(f"• {inner}\n")
            i += 1
            continue

        # ── Ordered list item ────────────────────────────────────────────
        m = _OL_RE.match(line)
        if m:
            # Find item number from original line
            num_end = line.index(".")
            num = line[:num_end].strip()
            inner = _inline_format(escape_html(m.group(2)))
            state.output.append(f"{num}. {inner}\n")
            i += 1
            continue

        # ── Empty line ───────────────────────────────────────────────────
        if not line.strip():
            state.output.append("\n")
            i += 1
            continue

        # ── Paragraph text ───────────────────────────────────────────────
        state.output.append(_inline_format(escape_html(line)) + "\n")
        i += 1

    # Flush pending code block (unterminated)
    if state.in_code_block and state.code_buf:
        body = "\n".join(state.code_buf)
        if state.code_lang in ("bash", "shell", "sh", "console"):
            body = strip_ansi(body)
        state.output.append(code_block(state.code_lang, body))
        state.output.append("\n\n")

    # Flush pending table
    if state.in_table and state.table_rows:
        state.output.append(_render_table(state.table_rows, state.header_row_count))
        state.output.append("\n")

    result = "".join(state.output)
    result = collapse_newlines(result)
    result = wrap_long_pre_blocks(result)
    return result.strip()


# ─── format_with_thinking ────────────────────────────────────────────────────


def format_with_thinking(thinking: str, body: str, sealed: bool) -> str:
    """Format a thinking block + body for Telegram.

    sealed=False → <blockquote> (visible during streaming)
    sealed=True  → <blockquote expandable> (collapsible after completion)
    """
    if not thinking.strip():
        return markdown_to_telegram_html(body)

    thinking_trimmed = thinking.strip()
    truncated = False
    if len(thinking_trimmed) > MAX_THINKING_CHARS:
        thinking_trimmed = thinking_trimmed[:MAX_THINKING_CHARS]
        truncated = True

    suffix = "\n…" if truncated else ""
    tag = "<blockquote expandable>" if sealed else "<blockquote>"
    thinking_html = (
        f"{tag}💭 <i>Razonando...</i>\n"
        f"{escape_html(thinking_trimmed)}{suffix}"
        "</blockquote>"
    )
    body_html = markdown_to_telegram_html(body)
    if not body_html:
        return thinking_html
    return f"{thinking_html}\n\n{body_html}"


# ─── format_tool_status ──────────────────────────────────────────────────────


def format_tool_status(
    tool_name: str,
    args_preview: str,
    done: bool,
    success: bool | None,
    result_preview: str,
) -> str:
    """Format a tool-call status card for Telegram.

    Header bubble always visible; output bubble collapsed (expandable).
    """
    if not done:
        icon = "⚙️"
    elif success is True or success is None:
        icon = "✅"
    else:
        icon = "❌"

    args_part = f" <code>{escape_html(args_preview)}</code>" if args_preview else ""
    header = f"<blockquote>{icon} <b>{escape_html(tool_name)}</b>{args_part}</blockquote>"

    if not done or not result_preview:
        return header

    cleaned = strip_ansi(result_preview).rstrip()
    if not cleaned:
        return header

    truncated = truncate_chars(cleaned, INLINE_TOOL_RESULT_MAX_CHARS)
    lang = "diff" if looks_like_diff(truncated) else lang_for_tool(tool_name)
    output = code_block(lang, truncated)
    return f"{header}\n<blockquote expandable>{output}</blockquote>"
