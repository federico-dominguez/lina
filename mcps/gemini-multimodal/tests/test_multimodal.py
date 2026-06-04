"""Tests para lina-gemini-multimodal MCP server.

Usa monkeypatch/unittest.mock para evitar llamadas reales a Gemini API.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─── Helpers para crear archivos temporales ──────────────────────────────────


@pytest.fixture
def audio_file():
    """Crea un archivo .mp3 dummy."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(b"\xff\xfb\x90\x00" * 100)  # MP3 frame header simulado
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def image_file():
    """Crea un archivo .png dummy (mínimo PNG válido)."""
    # PNG header + IHDR chunk mínimo
    png_data = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\x0dIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\x0f\x00\x00\x00\x00\xff\xff\x03\x00\x00\x00\x04"
        b"\x00\x01\x00\x00\x00\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png_data)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def doc_file():
    """Crea un archivo .txt dummy."""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
        f.write("Contenido de prueba para el documento.")
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def unsupported_file():
    """Crea un archivo con extensión no soportada."""
    with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
        f.write(b"data")
        path = f.name
    yield path
    os.unlink(path)


# ─── Mock Gemini Client ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def mock_gemini_client():
    """Parchea google.genai.Client y google.genai.types para evitar llamadas reales.

    Configura respuestas por defecto.
    """
    with patch("lina_gemini_multimodal.server._get_client") as mock_get_client, \
         patch("lina_gemini_multimodal.server.genai_types") as mock_types:

        # Mock del cliente
        mock_client = MagicMock()

        # --- Mock para files.upload ---
        mock_file = MagicMock()
        mock_file.name = "files/test-file"
        mock_file.uri = "https://generativelanguage.googleapis.com/v1beta/files/test-file"
        mock_file.mime_type = "audio/mp3"
        mock_client.files.upload.return_value = mock_file

        # --- Mock para files.get (estado ACTIVE) ---
        mock_file_state = MagicMock()
        mock_file_state.state.name = "ACTIVE"
        mock_client.files.get.return_value = mock_file_state

        # --- Mock para models.generate_content ---
        mock_response = MagicMock()
        mock_response.text = "Esta es una respuesta simulada de Gemini."
        mock_candidate = MagicMock()
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        # --- Mock para models.generate_images ---
        mock_img_response = MagicMock()
        mock_generated_image = MagicMock()
        mock_img_content = MagicMock()
        mock_img_content.image_bytes = b"\x89PNG\x0d\x0a\x1a\x0a"  # PNG header
        mock_generated_image.image = mock_img_content
        mock_img_response.generated_images = [mock_generated_image]
        mock_client.models.generate_images.return_value = mock_img_response

        mock_get_client.return_value = mock_client

        # Mock types
        mock_types.GenerateContentConfig = MagicMock
        mock_types.Content = MagicMock
        mock_types.Part.from_uri = MagicMock(return_value="mocked_part_uri")
        mock_types.Part.from_text = MagicMock(return_value="mocked_part_text")
        mock_types.Tool = MagicMock(return_value="mocked_tool")
        mock_types.GoogleSearch = MagicMock(return_value="mocked_google_search")
        mock_types.GenerateImagesConfig = MagicMock(return_value="mocked_img_config")

        yield {
            "client": mock_client,
            "response": mock_response,
            "img_response": mock_img_response,
        }


# ─── Tests de validación ──────────────────────────────────────────────────────


class TestValidation:
    """Tests de validación de archivos sin mock de Gemini."""

    def test_unsupported_extension(self, unsupported_file):
        from lina_gemini_multimodal.server import _validate_file, _SUPPORTED_AUDIO_EXTS

        with pytest.raises(ValueError, match="extensión no soportada"):
            _validate_file(unsupported_file, _SUPPORTED_AUDIO_EXTS)

    def test_nonexistent_file(self):
        from lina_gemini_multimodal.server import _validate_file, _SUPPORTED_IMAGE_EXTS

        with pytest.raises(FileNotFoundError):
            _validate_file("/no/existe/imagen.png", _SUPPORTED_IMAGE_EXTS)

    def test_supported_extensions(self, audio_file):
        from lina_gemini_multimodal.server import _validate_file, _SUPPORTED_AUDIO_EXTS

        # No debe lanzar excepción
        result = _validate_file(audio_file, _SUPPORTED_AUDIO_EXTS)
        assert result.exists()


# ─── Tests de tools ───────────────────────────────────────────────────────────


class TestTools:
    """Tests para cada tool MCP usando mocks."""

    def test_transcribe_audio(self, audio_file, mock_gemini_client):
        from lina_gemini_multimodal.server import transcribe_audio

        result = transcribe_audio(audio_file)
        assert "respuesta simulada" in result
        mock_gemini_client["client"].files.upload.assert_called_once()
        mock_gemini_client["client"].models.generate_content.assert_called_once()

    def test_transcribe_audio_invalid_extension(self, image_file):
        from lina_gemini_multimodal.server import transcribe_audio

        with pytest.raises(ValueError, match="extensión no soportada"):
            transcribe_audio(image_file)

    def test_analyze_image(self, image_file, mock_gemini_client):
        from lina_gemini_multimodal.server import analyze_image

        result = analyze_image(image_file, query="¿Qué hay en esta imagen?")
        assert "respuesta simulada" in result
        mock_gemini_client["client"].files.upload.assert_called_once()

    def test_analyze_image_no_query(self, image_file, mock_gemini_client):
        from lina_gemini_multimodal.server import analyze_image

        result = analyze_image(image_file)
        assert "respuesta simulada" in result

    def test_search_web(self, mock_gemini_client):
        from lina_gemini_multimodal.server import search_web

        result = search_web(query="últimas noticias tecnología 2026")
        assert "respuesta simulada" in result
        # Verificar que se usó GoogleSearch tool
        mock_gemini_client["client"].models.generate_content.assert_called_once()

    def test_process_document(self, doc_file, mock_gemini_client):
        from lina_gemini_multimodal.server import process_document

        result = process_document(doc_file, query="Resumí el contenido")
        assert "respuesta simulada" in result
        mock_gemini_client["client"].files.upload.assert_called_once()

    def test_process_document_no_query(self, doc_file, mock_gemini_client):
        from lina_gemini_multimodal.server import process_document

        result = process_document(doc_file)
        assert "respuesta simulada" in result

    def test_generate_image(self, mock_gemini_client):
        from lina_gemini_multimodal.server import generate_image

        result = generate_image(
            prompt="Un gato naranja volando sobre una ciudad cyberpunk",
            aspect_ratio="16:9",
            number_of_images=1,
        )
        assert "Imagen 1/" in result
        mock_gemini_client["client"].models.generate_images.assert_called_once()

    def test_generate_image_invalid_aspect_ratio(self):
        from lina_gemini_multimodal.server import generate_image

        with pytest.raises(ValueError, match="aspect_ratio"):
            generate_image("test", aspect_ratio="2:1")

    def test_generate_image_invalid_count(self):
        from lina_gemini_multimodal.server import generate_image

        with pytest.raises(ValueError, match="number_of_images"):
            generate_image("test", number_of_images=0)

    def test_generate_image_too_many(self):
        from lina_gemini_multimodal.server import generate_image

        with pytest.raises(ValueError, match="number_of_images"):
            generate_image("test", number_of_images=5)


# ─── Tests de integración del servidor ────────────────────────────────────────


class TestServer:
    """Tests de arranque y tools list del MCP."""

    def test_mcp_initialization(self):
        """Verifica que el servidor FastMCP se pueda crear."""
        from lina_gemini_multimodal.server import mcp

        assert mcp.name == "lina-gemini-multimodal"

    def test_tools_registered(self):
        """Verifica que las 5 tools estén registradas."""
        from lina_gemini_multimodal.server import mcp

        # FastMCP almacena tools en _tool_manager
        tool_names = {tool.name for tool in mcp._tool_manager._tools.values()}
        expected = {
            "transcribe_audio",
            "analyze_image",
            "search_web",
            "process_document",
            "generate_image",
        }
        assert expected.issubset(tool_names), f"Faltan tools: {expected - tool_names}"
