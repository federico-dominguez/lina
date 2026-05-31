"""Unit tests for the Telegram HTML formatter."""

from lina_gateway.formatter import (
    _strip_inline_markdown,
    code_block,
    format_tool_status,
    format_with_thinking,
    looks_like_diff,
    markdown_to_telegram_html,
    split_message,
    strip_ansi,
    strip_html_tags,
    truncate_chars,
    wrap_long_pre_blocks,
)

# ─── split_message ───────────────────────────────────────────────────────────

class TestSplitMessage:
    def test_short_message_unchanged(self):
        assert split_message("hello world") == ["hello world"]

    def test_splits_at_newline(self):
        text = "a" * 4000 + "\n" + "b" * 200
        chunks = split_message(text, 4096)
        assert len(chunks) == 2
        assert chunks[0].endswith("\n")
        assert chunks[1] == "b" * 200

    def test_splits_at_space(self):
        text = "a" * 4000 + " " + "b" * 200
        chunks = split_message(text, 4096)
        assert len(chunks) == 2

    def test_no_boundary_hard_cut(self):
        text = "a" * 5000
        chunks = split_message(text, 4096)
        assert len(chunks) == 2

    def test_exact_limit(self):
        text = "x" * 4096
        assert split_message(text, 4096) == [text]


# ─── strip_ansi ──────────────────────────────────────────────────────────────

class TestStripAnsi:
    def test_removes_colors(self):
        assert strip_ansi("\x1b[32mgreen\x1b[0m") == "green"

    def test_no_ansi_unchanged(self):
        assert strip_ansi("hello world") == "hello world"


# ─── strip_html_tags ─────────────────────────────────────────────────────────

class TestStripHtmlTags:
    def test_removes_tags(self):
        assert strip_html_tags("<b>bold</b> text") == "bold text"

    def test_decodes_entities(self):
        assert strip_html_tags("a &amp; b &lt;c&gt;") == "a & b <c>"


# ─── truncate_chars ──────────────────────────────────────────────────────────

class TestTruncateChars:
    def test_short_unchanged(self):
        assert truncate_chars("hello", 10) == "hello"

    def test_truncates_with_ellipsis(self):
        result = truncate_chars("hello world", 5)
        assert result == "hello…"
        assert len(result) == 6  # 5 chars + ellipsis

    def test_multibyte_safe(self):
        text = "α" * 10
        result = truncate_chars(text, 5)
        assert result == "α" * 5 + "…"


# ─── looks_like_diff ─────────────────────────────────────────────────────────

class TestLooksLikeDiff:
    def test_git_diff(self):
        assert looks_like_diff("diff --git a/foo b/foo\n--- a/foo\n+++ b/foo")

    def test_unified_diff(self):
        assert looks_like_diff("--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,4 @@\n")

    def test_not_diff(self):
        assert not looks_like_diff("Just some regular text")


# ─── code_block ──────────────────────────────────────────────────────────────

class TestCodeBlock:
    def test_with_lang(self):
        result = code_block("python", "x = 1")
        assert 'class="language-python"' in result
        assert "x = 1" in result

    def test_without_lang(self):
        result = code_block(None, "x = 1")
        assert "<pre><code>" in result
        assert 'class=' not in result

    def test_escapes_body(self):
        result = code_block(None, "<script>")
        assert "&lt;script&gt;" in result


# ─── wrap_long_pre_blocks ────────────────────────────────────────────────────

class TestWrapLongPreBlocks:
    def test_short_block_not_wrapped(self):
        html = "<pre><code>x = 1</code></pre>"
        result = wrap_long_pre_blocks(html)
        assert "<blockquote" not in result

    def test_long_block_wrapped(self):
        long_code = "x = 1\n" * 20
        html = f"<pre><code>{long_code}</code></pre>"
        result = wrap_long_pre_blocks(html)
        assert "<blockquote expandable>" in result

    def test_already_in_blockquote_not_double_wrapped(self):
        html = "<blockquote expandable><pre><code>" + "x\n" * 20 + "</code></pre></blockquote>"
        result = wrap_long_pre_blocks(html)
        assert result.count("<blockquote") == 1


# ─── format_with_thinking ────────────────────────────────────────────────────

class TestFormatWithThinking:
    def test_no_thinking_returns_body(self):
        result = format_with_thinking("", "hello", True)
        assert "<blockquote" not in result
        assert "hello" in result

    def test_sealed_uses_expandable(self):
        result = format_with_thinking("reason", "body", True)
        assert "<blockquote expandable>" in result
        assert "💭" in result
        assert "body" in result

    def test_unsealed_uses_plain_blockquote(self):
        result = format_with_thinking("reason", "body", False)
        assert "<blockquote>" in result
        assert "<blockquote expandable>" not in result

    def test_truncates_long_thinking(self):
        long_thinking = "x" * 1000
        result = format_with_thinking(long_thinking, "body", True)
        # Thinking should be truncated to MAX_THINKING_CHARS
        assert len(result) < len(long_thinking) + 500


# ─── format_tool_status ──────────────────────────────────────────────────────

class TestFormatToolStatus:
    def test_in_progress_shows_gear(self):
        result = format_tool_status("shell_exec", "ls -la", False, None, "")
        assert "⚙️" in result
        assert "shell_exec" in result
        assert "ls -la" in result

    def test_success_shows_check(self):
        result = format_tool_status("shell_exec", "ls", True, True, "file.txt")
        assert "✅" in result
        assert "file.txt" in result

    def test_failure_shows_cross(self):
        result = format_tool_status("shell_exec", "bad_cmd", True, False, "error")
        assert "❌" in result

    def test_diff_output_gets_diff_lang(self):
        diff = "--- a/foo\n+++ b/foo\n@@ -1,1 +1,2 @@\n-old\n+new"
        result = format_tool_status("shell_exec", "", True, True, diff)
        assert 'language-diff' in result

    def test_no_result_no_output_bubble(self):
        result = format_tool_status("shell_exec", "cmd", True, True, "")
        assert "expandable" not in result


# ─── markdown_to_telegram_html ───────────────────────────────────────────────

class TestMarkdownToTelegramHtml:
    def test_bold(self):
        assert "<b>bold</b>" in markdown_to_telegram_html("**bold**")

    def test_italic(self):
        assert "<i>italic</i>" in markdown_to_telegram_html("*italic*")

    def test_inline_code(self):
        result = markdown_to_telegram_html("`code`")
        assert "<code>code</code>" in result

    def test_code_block(self):
        result = markdown_to_telegram_html("```python\nx = 1\n```")
        assert 'language-python' in result
        assert "x = 1" in result

    def test_heading(self):
        result = markdown_to_telegram_html("# Title")
        assert "<b>" in result
        assert "Title" in result

    def test_unordered_list(self):
        result = markdown_to_telegram_html("- item1\n- item2")
        assert "• item1" in result
        assert "• item2" in result

    def test_ordered_list(self):
        result = markdown_to_telegram_html("1. first\n2. second")
        assert "1. first" in result
        assert "2. second" in result

    def test_link(self):
        result = markdown_to_telegram_html("[text](https://example.com)")
        assert '<a href="https://example.com">text</a>' in result

    def test_escapes_html_in_text(self):
        result = markdown_to_telegram_html("a < b & c > d")
        assert "&lt;" in result
        assert "&amp;" in result

    def test_horizontal_rule(self):
        result = markdown_to_telegram_html("---")
        assert "———" in result

    def test_empty_input(self):
        assert markdown_to_telegram_html("") == ""

    def test_table_renders_pre(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = markdown_to_telegram_html(md)
        assert "<pre><code>" in result
        assert "A" in result

    def test_strikethrough(self):
        result = markdown_to_telegram_html("~~strike~~")
        assert "<s>strike</s>" in result

    def test_bold_not_double_escaped(self):
        """Regression: **text with &** must HTML-escape exactly once."""
        result = markdown_to_telegram_html("**a & b**")
        # & in the markdown source should become &amp; inside the <b> tag — not
        # double-escaped to &amp;amp; (the old bug when escape_html was called
        # again inside the _inline_format lambda).
        assert "<b>a &amp; b</b>" in result
        assert "&amp;amp;" not in result

    def test_italic_underscore_not_matching_url(self):
        """Regression: _italic_ must not corrupt href attributes with underscores."""
        result = markdown_to_telegram_html("[link](https://foo.com/a_b_c)")
        assert "a_b_c" in result
        # href should be intact — not replaced with <i>b</i> fragments
        assert "<i>" not in result

    def test_table_strips_bold_from_cells(self):
        """Regression: **bold** in table cells must not appear in <pre><code> output."""
        md = "| Operation | Complexity |\n|---|---|\n| **Insert** | O(log n) |"
        result = markdown_to_telegram_html(md)
        # Inside <pre><code>, markdown markers must be gone
        assert "**" not in result
        # The cell content (without stars) should be present
        assert "Insert" in result

    def test_table_strips_italic_from_cells(self):
        """Table cells with *italic* text must not leak asterisks."""
        md = "| A | B |\n|---|---|\n| *italic* | value |"
        result = markdown_to_telegram_html(md)
        assert "**" not in result
        assert "*italic*" not in result
        assert "italic" in result


# ─── _strip_inline_markdown ──────────────────────────────────────────────────

class TestStripInlineMarkdown:
    def test_strips_bold(self):
        assert _strip_inline_markdown("**bold**") == "bold"

    def test_strips_bold_underscore(self):
        assert _strip_inline_markdown("__bold__") == "bold"

    def test_strips_italic(self):
        assert _strip_inline_markdown("*italic*") == "italic"

    def test_strips_code(self):
        assert _strip_inline_markdown("`code`") == "code"

    def test_strips_strike(self):
        assert _strip_inline_markdown("~~strike~~") == "strike"

    def test_strips_link(self):
        assert _strip_inline_markdown("[text](https://example.com)") == "text"

    def test_plain_text_unchanged(self):
        assert _strip_inline_markdown("plain text") == "plain text"

    def test_mixed(self):
        result = _strip_inline_markdown("**bold** and *italic* and `code`")
        assert result == "bold and italic and code"

    def test_underscores_in_identifiers_preserved(self):
        """Regression (Copilot review): a_b_c must NOT be stripped to abc."""
        assert _strip_inline_markdown("O(log_n)") == "O(log_n)"
        assert _strip_inline_markdown("foo_bar_baz") == "foo_bar_baz"
        assert _strip_inline_markdown("some_method_name(x)") == "some_method_name(x)"

    def test_italic_underscore_still_works(self):
        """Boundary-aware italic: _word_ surrounded by spaces is stripped."""
        result = _strip_inline_markdown("use _this_ option")
        assert result == "use this option"


# ─── format_with_thinking (markdown in thinking) ─────────────────────────────

class TestFormatWithThinkingMarkdown:
    def test_bold_in_thinking_converted(self):
        """Regression: **bold** in thinking content must become <b>bold</b>."""
        result = format_with_thinking("**bold reasoning**", "body", True)
        assert "**" not in result
        assert "<b>bold reasoning</b>" in result

    def test_italic_in_thinking_converted(self):
        result = format_with_thinking("*italic thought*", "body", True)
        assert "*italic thought*" not in result
        assert "<i>italic thought</i>" in result

    def test_raw_thinking_no_markdown_leak(self):
        thinking = "consider **step 1** and __step 2__ then `code()`"
        result = format_with_thinking(thinking, "some body", True)
        # No raw markdown markers should appear in the output
        assert "**" not in result
        assert "__step 2__" not in result
