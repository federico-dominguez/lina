"""LINA MCP — lina-gemini-multimodal.

Expone capacidades multimodales de Gemini Flash-Lite como herramientas MCP
consumibles por DeepSeek: audio, imagen, búsqueda web, documentos y generación
de imágenes.

Transporte:
    Por defecto stdio; si MCP_TRANSPORT=streamable-http escucha en 0.0.0.0:8000.

Autenticación:
    GEMINI_API_KEY — API key de Google AI Studio (obligatoria).
"""

from __future__ import annotations

import base64
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from google.genai import types as genai_types
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
    format="[lina-gemini-multimodal] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("lina-gemini-multimodal")

# ─── Config ───────────────────────────────────────────────────────────────────

_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", ""))
if not _GEMINI_API_KEY:
    log.error("GOOGLE_API_KEY|GEMINI_API_KEY no configurada — las tools fallarán con auth error")

# Gemini Flash-Lite es el más barato con multimodal completo.
# Ver issue #148 para justificación de costos.
_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
_IMAGE_GEN_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "imagen-3.0-generate-001")

_MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
_MCP_HTTP_PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP(
    "lina-gemini-multimodal",
    host="0.0.0.0",
    port=_MCP_HTTP_PORT,
)

_MAX_UPLOAD_BYTES = int(os.environ.get("LINA_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))  # 20 MiB
_SUPPORTED_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}
_AUDIO_MIME_MAP = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
}
_SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_SUPPORTED_DOC_EXTS = {".pdf", ".docx", ".pptx", ".txt", ".csv", ".html", ".md"}


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _get_client() -> Any:
    """Retorna un cliente Gemini autenticado."""
    from google.genai import Client

    return Client(api_key=_GEMINI_API_KEY)


def _validate_file(path: str, supported_exts: set[str]) -> Path:
    """Valida que el archivo exista, no sea muy grande y tenga extensión soportada."""
    p = Path(path).expanduser().resolve(strict=True)
    if not p.is_file():
        raise FileNotFoundError(f"no es un archivo: {p}")
    ext = p.suffix.lower()
    if ext not in supported_exts:
        raise ValueError(
            f"extensión no soportada '{ext}'. Soportadas: {', '.join(sorted(supported_exts))}"
        )
    size = p.stat().st_size
    if size > _MAX_UPLOAD_BYTES:
        raise ValueError(
            f"archivo demasiado grande: {size}B (máx {_MAX_UPLOAD_BYTES}B)"
        )
    return p


def _upload_file(client: Any, path: str | Path) -> Any:
    """Sube un archivo a Gemini y espera a que esté activo."""
    mime_type = _AUDIO_MIME_MAP.get(Path(str(path)).suffix.lower())
    config = genai_types.UploadFileConfig(mimeType=mime_type) if mime_type else None
    f = client.files.upload(file=str(path), config=config)
    # Esperar a que el archivo esté procesado
    import time

    while True:
        meta = client.files.get(name=f.name)
        if meta.state.name == "ACTIVE":
            break
        if meta.state.name in ("FAILED",):
            raise RuntimeError(f"Gemini file upload failed: {meta.state.name}")
        time.sleep(1)
    return f


def _gemini_generate(
    contents: list[Any],
    model: str | None = None,
    tools: list[Any] | None = None,
) -> str:
    """Llamada genérica a generate_content con manejo de errores."""
    client = _get_client()
    config = genai_types.GenerateContentConfig()
    if tools:
        config.tools = tools
    try:
        response = client.models.generate_content(
            model=model or _MODEL,
            contents=contents,
            config=config,
        )
    except Exception as e:
        log.error("Gemini API error: %s", e)
        raise RuntimeError(f"Error en Gemini API: {e}") from e
    if not response.candidates:
        # Intentar extraer feedback si no hay candidates
        try:
            feedback = response.prompt_feedback
            raise RuntimeError(f"Gemini bloqueó la consulta: {feedback}")
        except AttributeError:
            raise RuntimeError("Gemini no devolvió candidates (consulta bloqueada)")
    return response.text


# ─── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
def transcribe_audio(audio_path: str) -> str:
    """Transcribe un archivo de audio a texto usando Gemini Flash-Lite.

    Soportado: .mp3, .wav, .ogg, .m4a, .flac, .aac
    Costo típico: ~$0.0003 por 30s de audio

    Args:
        audio_path: Ruta al archivo de audio.

    Returns:
        Transcripción textual del audio.
    """
    log.info("transcribe_audio: %s", audio_path)
    p = _validate_file(audio_path, _SUPPORTED_AUDIO_EXTS)
    client = _get_client()
    gf = _upload_file(client, p)

    contents = [
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_uri(file_uri=gf.uri, mime_type=gf.mime_type or _AUDIO_MIME_MAP.get(p.suffix.lower(), "audio/ogg"))],
        ),
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text="Transcribe el audio palabra por palabra en español rioplatense. No agregues nada más que la transcripción.")],
        ),
    ]

    return _gemini_generate(contents)


@mcp.tool()
def analyze_image(image_path: str, query: str = "") -> str:
    """Analiza una imagen y responde preguntas sobre ella usando Gemini Flash-Lite.

    Soportado: .png, .jpg, .jpeg, .webp, .gif, .bmp
    Costo típico: ~$0.0002 por imagen

    Args:
        image_path: Ruta al archivo de imagen.
        query:      Pregunta opcional sobre la imagen. Si está vacía,
                    describe la imagen en detalle.

    Returns:
        Análisis descriptivo o respuesta a la pregunta.
    """
    log.info("analyze_image: %s | query=%s", image_path, query[:80])
    p = _validate_file(image_path, _SUPPORTED_IMAGE_EXTS)
    client = _get_client()
    gf = _upload_file(client, p)

    prompt = query or "Describe esta imagen en detalle en español."
    contents = [
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_uri(file_uri=gf.uri, mime_type=gf.mime_type)],
        ),
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=prompt)],
        ),
    ]

    return _gemini_generate(contents)


@mcp.tool()
def search_web(query: str) -> str:
    """Busca información actualizada en la web usando Google Search Grounding
    integrado en Gemini (sin API de búsqueda externa).

    Costo típico: ~$0.014 por búsqueda (después del free tier)

    Args:
        query: Consulta de búsqueda.

    Returns:
        Resultados con fuentes y fragmentos relevantes.
    """
    log.info("search_web: %s", query[:120])
    client = _get_client()
    tools = [genai_types.Tool(google_search=genai_types.GoogleSearch())]
    contents = [
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(
                text=f"Buscá información actualizada sobre: {query}. "
                     "Incluí las fuentes con URLs al final de la respuesta."
            )],
        ),
    ]

    return _gemini_generate(contents, tools=tools)


@mcp.tool()
def process_document(file_path: str, query: str = "") -> str:
    """Procesa un documento (PDF, DOCX, PPTX, TXT, CSV, HTML, MD) y extrae
    su contenido o responde preguntas sobre él usando Gemini.

    Costo típico: ~$0.001 por 10 páginas

    Args:
        file_path: Ruta al archivo del documento.
        query:     Pregunta opcional sobre el documento. Si está vacía,
                   extrae y resume el contenido.

    Returns:
        Contenido extraído / respuesta.
    """
    log.info("process_document: %s | query=%s", file_path, query[:80])
    p = _validate_file(file_path, _SUPPORTED_DOC_EXTS)
    client = _get_client()
    gf = _upload_file(client, p)

    prompt = query or "Extraé y resumí el contenido de este documento en español. Incluí los puntos principales."
    contents = [
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_uri(file_uri=gf.uri, mime_type=gf.mime_type)],
        ),
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=prompt)],
        ),
    ]

    return _gemini_generate(contents)


@mcp.tool()
def generate_image(
    prompt: str,
    aspect_ratio: str = "1:1",
    number_of_images: int = 1,
) -> str:
    """Genera una imagen a partir de una descripción textual usando Imagen
    de Gemini (sin Stable Diffusion ni herramientas externas).

    Costo típico: ~$0.02 por imagen

    Args:
        prompt:         Descripción textual de la imagen a generar.
        aspect_ratio:   Relación de aspecto: "1:1", "3:4", "4:3", "9:16", "16:9".
                        (default: "1:1")
        number_of_images: Cantidad de imágenes a generar (default: 1, máx: 4).

    Returns:
        La(s) imagen(es) generada(s) en base64 con metadatos.
    """
    log.info("generate_image: %s | aspect_ratio=%s", prompt[:80], aspect_ratio)
    if number_of_images < 1 or number_of_images > 4:
        raise ValueError("number_of_images debe estar entre 1 y 4")

    valid_ratios = {"1:1", "3:4", "4:3", "9:16", "16:9"}
    if aspect_ratio not in valid_ratios:
        raise ValueError(
            f"aspect_ratio '{aspect_ratio}' no válido. "
            f"Válidos: {', '.join(sorted(valid_ratios))}"
        )

    client = _get_client()
    config = genai_types.GenerateImagesConfig(
        aspect_ratio=aspect_ratio,
        number_of_images=number_of_images,
    )

    try:
        response = client.models.generate_images(
            model=_IMAGE_GEN_MODEL,
            prompt=prompt,
            config=config,
        )
    except Exception as e:
        log.error("Gemini image generation error: %s", e)
        raise RuntimeError(f"Error generando imagen: {e}") from e

    if not response.generated_images:
        raise RuntimeError("Gemini no generó imágenes (consulta bloqueada)")

    results = []
    for i, img in enumerate(response.generated_images):
        b64 = base64.b64encode(img.image.image_bytes).decode("utf-8")
        # Guardar en temp dir por si el cliente quiere el path
        tmp = Path(tempfile.gettempdir()) / f"lina_generated_{i}.png"
        tmp.write_bytes(img.image.image_bytes)
        results.append(
            f"Imagen {i+1}/{len(response.generated_images)}:\n"
            f"  Base64: {b64[:80]}... ({len(img.image.image_bytes)} bytes)\n"
            f"  Path: {tmp}\n"
        )

    return "\n".join(results)


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    log.info("starting model=%s image_model=%s transport=%s", _MODEL, _IMAGE_GEN_MODEL, _MCP_TRANSPORT)
    mcp.run(transport=_MCP_TRANSPORT)


if __name__ == "__main__":
    main()
