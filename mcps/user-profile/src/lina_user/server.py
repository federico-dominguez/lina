"""LINA User Profile MCP server — FastMCP with PostgreSQL store.

Tools:
    profile_set    — set a profile field
    profile_get    — get a specific field or all fields
    profile_delete — delete a field
    profile_list   — list all profile fields with values
"""

from __future__ import annotations

import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from . import store

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "lina-user-profile",
    host="0.0.0.0",
    port=8000,
)

# ── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
async def profile_set(key: str, value: str) -> dict:
    """Guardar un campo del perfil de Fede.

    Usa esto para registrar datos personales como nombre, edad, peso, altura,
    metas, valores, preferencias, triggers, historia, etc.

    Args:
        key: Nombre del campo (ej: "edad", "peso", "meta_libros_2026", "horario_sueno")
        value: Valor del campo (texto, número, o JSON)

    Returns:
        El campo guardado con key, value y updated_at.
    """
    import json as _json

    # Try to parse value as JSON, fall back to string
    try:
        parsed = _json.loads(value)
    except (_json.JSONDecodeError, TypeError):
        parsed = value

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, store.set_profile, key, parsed)


@mcp.tool()
async def profile_get(key: str = "") -> dict:
    """Obtener un campo del perfil o todos los campos.

    Args:
        key: Nombre del campo (opcional). Si se omite, devuelve todos.

    Returns:
        El campo solicitado o lista de todos los campos.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, store.get_profile, key if key else None)


@mcp.tool()
async def profile_list() -> list[dict]:
    """Listar todos los campos del perfil de Fede.

    Returns:
        Lista de todos los campos con key, value y updated_at.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, store.get_profile, None)


@mcp.tool()
async def profile_delete(key: str) -> dict:
    """Eliminar un campo del perfil.

    Args:
        key: Nombre del campo a eliminar.

    Returns:
        {key, deleted: true/false}
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, store.delete_profile, key)


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logger.info("LINA User Profile MCP starting on port 8000")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
