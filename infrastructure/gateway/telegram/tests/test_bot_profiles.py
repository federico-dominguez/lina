"""Tests para BotProfileLoader (Fase 4 — Perfiles de bots)."""

from __future__ import annotations

import os
import tempfile

import pytest
import yaml

from lina_gateway.profiles import BotProfile, BotProfileLoader


class TestBotProfile:
    """Tests del dataclass BotProfile."""

    def test_default_values(self):
        """BotProfile con valores por defecto."""
        p = BotProfile(name="test")
        assert p.name == "test"
        assert p.personality == "neutral"
        assert p.constraints == []

    def test_all_fields(self):
        """BotProfile con todos los campos."""
        p = BotProfile(
            name="lina",
            personality="formal",
            tone="entusiasta",
            style="detallado",
            constraints=["usar vos"],
            system_prompt_extension="Sos LINA",
        )
        assert p.name == "lina"
        assert p.system_prompt_extension == "Sos LINA"


class TestBotProfileLoader:
    """Tests del BotProfileLoader."""

    def setup_method(self):
        BotProfileLoader.clear_cache()

    def test_load_nonexistent_returns_default(self):
        """Cargar perfil inexistente devuelve default."""
        profile = BotProfileLoader.load("nonexistent_bot_xyz")
        assert profile.name == "nonexistent_bot_xyz"
        assert profile.personality == "neutral"

    def test_load_lina(self):
        """Cargar perfil de LINA."""
        profile = BotProfileLoader.load("lina")
        assert profile.name == "lina"
        assert profile.personality == "formal"
        assert profile.tone == "entusiasta"
        assert len(profile.constraints) > 0

    def test_load_cline(self):
        """Cargar perfil de Cline."""
        profile = BotProfileLoader.load("cline")
        assert profile.name == "cline"
        assert profile.personality == "technical"
        assert profile.style == "conciso"

    def test_load_goose(self):
        """Cargar perfil de Goose."""
        profile = BotProfileLoader.load("goose")
        assert profile.name == "goose"
        assert profile.personality == "practical"

    def test_load_case_insensitive(self):
        """El nombre del bot es case-insensitive."""
        profile1 = BotProfileLoader.load("LINA")
        profile2 = BotProfileLoader.load("lina")
        assert profile1.name == profile2.name

    def test_cache_works(self):
        """La caché evita recargar del disco."""
        profile1 = BotProfileLoader.load("lina")
        profile2 = BotProfileLoader.load("lina")
        assert profile1 is profile2  # misma referencia (caché)

    def test_format_system_prompt(self):
        """format_system_prompt devuelve el texto de extensión."""
        profile = BotProfileLoader.load("lina")
        formatted = BotProfileLoader.format_system_prompt(profile)
        assert len(formatted) > 0
        assert isinstance(formatted, str)

    def test_format_system_prompt_default(self):
        """Perfil sin extension usa formato por defecto."""
        profile = BotProfile(name="test", personality="formal")
        formatted = BotProfileLoader.format_system_prompt(profile)
        assert "Perfil: test" in formatted
        assert "Personalidad: formal" in formatted
