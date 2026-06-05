"""Bot profiles — Personalidad, tono y estilo por bot (Fase 4).

Loads bot personality profiles from config/bot_profiles.yaml and
formats them as system prompt extensions.

Uso:
    profile = BotProfileLoader.load("lina")
    prompt_ext = BotProfileLoader.format_system_prompt(profile)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)

_PROFILES_PATH = os.environ.get(
    "BOT_PROFILES_PATH",
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..", "..", "config", "bot_profiles.yaml"
    ),
)


@dataclass
class BotProfile:
    """Perfil de personalidad de un bot."""

    name: str
    personality: str = "neutral"
    tone: str = "neutral"
    style: str = "neutral"
    description: str = ""
    constraints: list[str] = field(default_factory=list)
    system_prompt_extension: str = ""


class BotProfileLoader:
    """Carga perfiles de bots desde YAML."""

    _cache: dict[str, BotProfile] = {}
    _path: str = _PROFILES_PATH

    @classmethod
    def load(cls, bot_name: str, reload: bool = False) -> BotProfile:
        """Carga el perfil de un bot por nombre.

        Args:
            bot_name: Nombre del bot (lina, cline, goose, gemma).
            reload: Si True, recarga desde disco ignorando cache.

        Returns:
            BotProfile con la configuración del bot.
            Si no se encuentra el perfil, devuelve un perfil por defecto.
        """
        name = bot_name.lower()
        if not reload and name in cls._cache:
            return cls._cache[name]

        try:
            profiles = cls._load_all()
            if name in profiles:
                profile = profiles[name]
                profile.name = name
                cls._cache[name] = profile
                return profile
        except Exception as exc:
            logger.warning("Failed to load profile for %s: %s", bot_name, exc)

        # Default profile
        default = BotProfile(
            name=name,
            personality="neutral",
            tone="neutral",
            style="neutral",
            description=f"Bot {bot_name}",
            constraints=[],
            system_prompt_extension="",
        )
        cls._cache[name] = default
        return default

    @classmethod
    def _load_all(cls) -> dict[str, BotProfile]:
        """Carga todos los perfiles desde el archivo YAML."""
        path = cls._path
        if not os.path.exists(path):
            # Try alternate path relative to project root
            alt_path = os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "..",
                "..",
                "..",
                "config",
                "bot_profiles.yaml",
            )
            if os.path.exists(alt_path):
                path = alt_path
            else:
                logger.warning("Bot profiles not found at %s or %s", cls._path, alt_path)
                return {}

        with open(path) as f:
            raw = yaml.safe_load(f)

        profiles: dict[str, BotProfile] = {}
        for name, data in raw.items():
            if not isinstance(data, dict):
                continue
            profiles[name] = BotProfile(
                name=name,
                personality=data.get("personality", "neutral"),
                tone=data.get("tone", "neutral"),
                style=data.get("style", "neutral"),
                description=data.get("description", ""),
                constraints=data.get("constraints", []),
                system_prompt_extension=data.get("system_prompt_extension", ""),
            )

        logger.info("Loaded %d bot profiles", len(profiles))
        return profiles

    @classmethod
    def format_system_prompt(cls, profile: BotProfile) -> str:
        """Formatea el perfil como extensión del system prompt.

        Returns:
            String listo para agregar al system prompt del bot.
        """
        if profile.system_prompt_extension:
            return profile.system_prompt_extension.strip()

        parts = [f"Perfil: {profile.name}"]
        parts.append(f"Personalidad: {profile.personality}")
        parts.append(f"Tono: {profile.tone}")
        parts.append(f"Estilo: {profile.style}")
        if profile.constraints:
            parts.append("Reglas:")
            parts.extend(f"  - {c}" for c in profile.constraints)
        return "\n".join(parts)

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()
