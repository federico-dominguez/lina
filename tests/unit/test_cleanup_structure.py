"""Tests para verificar que la limpieza estructural del repo (#126) está correcta."""

import os

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "../.."))


def _path(*parts):
    return os.path.join(REPO_ROOT, *parts)


def test_moodle_files_in_docs():
    """m2-* files moved from Documents/study/ to docs/moodle/."""
    assert os.path.isfile(_path("docs/moodle/m2-final-preguntas.txt"))
    assert os.path.isfile(_path("docs/moodle/m2-r6-preguntas.txt"))
    assert os.path.isfile(_path("docs/moodle/m2-r7-preguntas.txt"))
    assert os.path.isfile(_path("docs/moodle/m2-r8-preguntas.txt"))
    assert os.path.isfile(_path("docs/moodle/m2-r9-preguntas.txt"))
    # Old location should not exist
    assert not os.path.exists(_path("Documents/study/moodle-m2"))


def test_agents_md_moved_to_prompts():
    """AGENTS.md moved from root to prompts/system/goose.md."""
    assert os.path.isfile(_path("prompts/system/goose.md"))
    assert not os.path.isfile(_path("AGENTS.md"))


def test_docs_0003_archived():
    """docs/0003-* moved to docs/archive/."""
    assert os.path.isfile(_path("docs/archive/0003-telegram-thinking-display.md"))
    assert not os.path.isfile(_path("docs/0003-telegram-thinking-display.md"))


def test_docs_analysis_archived():
    """docs/analysis/ archived under docs/archive/analysis/."""
    assert os.path.isfile(_path("docs/archive/analysis/2026-05-27-session-review.md"))
    assert not os.path.exists(_path("docs/analysis"))


def test_docs_reportes_archived():
    """docs/reportes/ archived under docs/archive/reportes/."""
    assert os.path.isfile(_path("docs/archive/reportes/estado-proyecto-2026-06-01.md"))
    assert not os.path.exists(_path("docs/reportes"))


def test_editorconfig_exists():
    """.editorconfig file created."""
    assert os.path.isfile(_path(".editorconfig"))
