"""Unit tests — lina-moodle.

Testea los helpers internos de parsing/limpieza HTML sin hacer llamadas
a la API real de Moodle.
"""

from __future__ import annotations

from lina_moodle.server import _clean_html, _decode_html, _extract_question_text

# ─── tests de _decode_html ────────────────────────────────────────────────────


class TestDecodeHtml:
    def test_decodes_common_entities(self):
        assert _decode_html("caf&eacute;") == "café"
        assert _decode_html("&lt;tag&gt;") == "<tag>"
        assert _decode_html("a &amp; b") == "a & b"
        assert _decode_html("&quot;hello&quot;") == '"hello"'

    def test_decodes_spanish_accents(self):
        assert _decode_html("&aacute;&eacute;&iacute;&oacute;&uacute;") == "aeiou".translate(
            str.maketrans("aeiou", "áéíóú")
        )
        assert _decode_html("&ntilde;") == "ñ"

    def test_empty_string_returns_empty(self):
        assert _decode_html("") == ""

    def test_no_entities_unchanged(self):
        assert _decode_html("hello world") == "hello world"

    def test_numeric_entity(self):
        assert _decode_html("&#39;") == "'"


# ─── tests de _clean_html ─────────────────────────────────────────────────────


class TestCleanHtml:
    def test_strips_html_tags(self):
        result = _clean_html("<p>Hello <b>world</b></p>")
        assert "<" not in result
        assert "Hello" in result
        assert "world" in result

    def test_collapses_whitespace(self):
        result = _clean_html("<p>  foo   bar  </p>")
        assert "  " not in result

    def test_truncates_to_max_len(self):
        long_text = "a" * 10_000
        result = _clean_html(f"<p>{long_text}</p>", max_len=100)
        assert len(result) <= 100

    def test_empty_returns_empty(self):
        assert _clean_html("") == ""
        assert _clean_html(None) == ""  # type: ignore[arg-type]


# ─── tests de _extract_question_text ─────────────────────────────────────────


class TestExtractQuestionText:
    def test_extracts_qtext_div(self):
        html = """
        <div class="qtext">¿Cuánto es 2 + 2?</div>
        <div class="answer">4</div>
        """
        result = _extract_question_text(html)
        assert "2 + 2" in result
        assert "answer" not in result

    def test_falls_back_to_clean_html_if_no_qtext(self):
        html = "<p>Pregunta sin clase qtext</p>"
        result = _extract_question_text(html)
        assert "Pregunta" in result
        assert "<p>" not in result

    def test_decodes_entities_in_question(self):
        html = '<div class="qtext">&iquest;Cu&aacute;l es la respuesta?</div>'
        result = _extract_question_text(html)
        # &aacute; debe decodificarse; ¿ (iquest) puede no estar en la tabla
        assert "Cu\u00e1l" in result  # "Cuál" decodificado correctamente


# ─── tests de configuración de entorno ───────────────────────────────────────


class TestEnvConfig:
    def test_moodle_url_defaults(self, monkeypatch):
        monkeypatch.delenv("MOODLE_URL", raising=False)
        import importlib

        import lina_moodle.server as m

        importlib.reload(m)
        assert "utec.edu.uy" in m.MOODLE_URL

    def test_moodle_url_override(self, monkeypatch):
        monkeypatch.setenv("MOODLE_URL", "https://moodle.example.com")
        import importlib

        import lina_moodle.server as m

        importlib.reload(m)
        assert m.MOODLE_URL == "https://moodle.example.com"
