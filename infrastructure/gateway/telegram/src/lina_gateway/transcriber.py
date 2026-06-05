"""Transcripción de audio usando Gemini Flash-Lite directamente.

El gateway llama a la API de Gemini directamente (sin pasar por el MCP
lina-gemini-multimodal) para transcribir mensajes de voz de Telegram.

Esto es más robusto que delegar la transcripción al LLM porque:
1. No gasta tokens del modelo en entender cómo usar tools
2. No depende de que el modelo elija la herramienta correcta
3. No hay problemas de filesystem entre contenedores
4. El usuario recibe la respuesta más rápido

Uso:
    transcription = await transcribe_audio("/shared/voice/voice_xxx.ogg")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── Constantes ──────────────────────────────────────────────────────────────

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
_SUPPORTED_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}
_AUDIO_MIME_MAP: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
}


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _get_client() -> Any:
    """Retorna un cliente Gemini autenticado."""
    from google.genai import Client

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY no configurada")
    return Client(api_key=api_key)


def _validate_audio(path: str) -> Path:
    """Valida que el archivo exista, no sea muy grande y tenga extensión de audio."""
    p = Path(path).expanduser().resolve(strict=True)
    if not p.is_file():
        raise FileNotFoundError(f"no es un archivo: {p}")
    ext = p.suffix.lower()
    if ext not in _SUPPORTED_AUDIO_EXTS:
        raise ValueError(
            f"extensión no soportada '{ext}'. Soportadas: {', '.join(sorted(_SUPPORTED_AUDIO_EXTS))}"
        )
    size = p.stat().st_size
    if size > _MAX_UPLOAD_BYTES:
        raise ValueError(f"archivo demasiado grande: {size}B (máx {_MAX_UPLOAD_BYTES}B)")
    return p


def _upload_file(client: Any, path: str | Path) -> Any:
    """Sube un archivo de audio a Gemini y espera a que esté activo."""
    from google.genai import types as genai_types

    p = Path(str(path))
    mime_type = _AUDIO_MIME_MAP.get(p.suffix.lower(), "audio/ogg")
    config = genai_types.UploadFileConfig(mimeType=mime_type)

    f = client.files.upload(file=str(path), config=config)

    import time

    while True:
        meta = client.files.get(name=f.name)
        if meta.state.name == "ACTIVE":
            break
        if meta.state.name in ("FAILED",):
            raise RuntimeError(f"Gemini file upload failed: {meta.state.name}")
        time.sleep(1)
    return f


# ─── API Pública ─────────────────────────────────────────────────────────────


async def transcribe_audio(audio_path: str) -> str:
    """Transcribe un archivo de audio usando Gemini Flash-Lite.

    Args:
        audio_path: Ruta al archivo de audio en /shared/voice/

    Returns:
        Texto transcrito

    Raises:
        FileNotFoundError: si el archivo no existe
        ValueError: si la extensión no es soportada o el archivo es muy grande
        RuntimeError: si falla la transcripción
    """
    from google.genai import types as genai_types

    logger.info("transcribing: %s", audio_path)

    # Validar
    p = _validate_audio(audio_path)

    # Subir a Gemini
    client = _get_client()
    gf = _upload_file(client, p)

    # Transcribir
    contents = [
        genai_types.Content(
            role="user",
            parts=[
                genai_types.Part.from_uri(
                    file_uri=gf.uri,
                    mime_type=gf.mime_type or _AUDIO_MIME_MAP.get(p.suffix.lower(), "audio/ogg"),
                )
            ],
        ),
        genai_types.Content(
            role="user",
            parts=[
                genai_types.Part.from_text(
                    text="Transcribe el audio palabra por palabra en español rioplatense. "
                    "No agregues nada más que la transcripción."
                )
            ],
        ),
    ]

    model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
    try:
        response = client.models.generate_content(
            model=model, contents=contents, config=genai_types.GenerateContentConfig()
        )
    except Exception as e:
        logger.error("Gemini API error: %s", e)
        raise RuntimeError(f"Error en Gemini API: {e}") from e

    if not response.candidates:
        try:
            feedback = response.prompt_feedback
            raise RuntimeError(f"Gemini bloqueó la consulta: {feedback}")
        except AttributeError:
            raise RuntimeError("Gemini no devolvió candidates (consulta bloqueada)")

    text = response.text
    logger.info("transcription result (%d chars): %.80s...", len(text), text)
    return text
