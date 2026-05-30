"""LINA MCP — GNS3 REST API v2

Variables de entorno:
    GNS3_HOST         hostname o IP del servidor GNS3 (default: localhost)
    GNS3_PORT         puerto del servidor GNS3 (default: 3080)
    GNS3_USER         usuario para Basic Auth (opcional)
    GNS3_PASSWORD     contraseña para Basic Auth (opcional)
    MCP_TRANSPORT     stdio (default) | streamable-http
    MCP_PORT          puerto HTTP (default 8000)
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

log = logging.getLogger("lina-gns3")

# ── Config ────────────────────────────────────────────────────────────────────
_GNS3_HOST  = os.environ.get("GNS3_HOST", "localhost")
_GNS3_PORT  = int(os.environ.get("GNS3_PORT", "3080"))
_GNS3_USER  = os.environ.get("GNS3_USER", "")
_GNS3_PASS  = os.environ.get("GNS3_PASSWORD", "")
_GNS3_BASE  = f"http://{_GNS3_HOST}:{_GNS3_PORT}/v2"
_MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
_MCP_HTTP_PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP("lina-gns3", host="0.0.0.0", port=_MCP_HTTP_PORT)


# ── HTTP helper ───────────────────────────────────────────────────────────────
def _client() -> httpx.Client:
    auth = (_GNS3_USER, _GNS3_PASS) if _GNS3_USER else None
    return httpx.Client(base_url=_GNS3_BASE, auth=auth, timeout=30)


def _get(path: str) -> Any:
    with _client() as c:
        r = c.get(path)
        r.raise_for_status()
        return r.json()


def _post(path: str, json: dict | None = None) -> Any:
    with _client() as c:
        r = c.post(path, json=json or {})
        r.raise_for_status()
        return r.json()


def _put(path: str, json: dict) -> Any:
    with _client() as c:
        r = c.put(path, json=json)
        r.raise_for_status()
        return r.json()


def _delete(path: str) -> int:
    with _client() as c:
        r = c.delete(path)
        r.raise_for_status()
        return r.status_code


# ── Tools ─────────────────────────────────────────────────────────────────────
@mcp.tool()
def gns3_list_projects() -> list[dict]:
    """Lista todos los proyectos GNS3 con su estado (opened/closed)."""
    return _get("/projects")


@mcp.tool()
def gns3_get_project(project_id: str) -> dict:
    """Retorna el detalle de un proyecto GNS3."""
    return _get(f"/projects/{project_id}")


@mcp.tool()
def gns3_open_project(project_id: str) -> dict:
    """Abre (activa) un proyecto GNS3."""
    return _post(f"/projects/{project_id}/open")


@mcp.tool()
def gns3_close_project(project_id: str) -> dict:
    """Cierra un proyecto GNS3 (conserva estado en disco)."""
    return _post(f"/projects/{project_id}/close")


@mcp.tool()
def gns3_list_nodes(project_id: str) -> list[dict]:
    """Lista todos los nodos (routers, switches, etc.) de un proyecto."""
    return _get(f"/projects/{project_id}/nodes")


@mcp.tool()
def gns3_get_node(project_id: str, node_id: str) -> dict:
    """Retorna el detalle de un nodo específico."""
    return _get(f"/projects/{project_id}/nodes/{node_id}")


@mcp.tool()
def gns3_start_node(project_id: str, node_id: str) -> dict:
    """Enciende (start) un nodo."""
    return _post(f"/projects/{project_id}/nodes/{node_id}/start")


@mcp.tool()
def gns3_stop_node(project_id: str, node_id: str) -> dict:
    """Apaga (stop) un nodo."""
    return _post(f"/projects/{project_id}/nodes/{node_id}/stop")


@mcp.tool()
def gns3_start_all_nodes(project_id: str) -> dict:
    """Enciende todos los nodos de un proyecto."""
    return _post(f"/projects/{project_id}/nodes/start")


@mcp.tool()
def gns3_stop_all_nodes(project_id: str) -> dict:
    """Apaga todos los nodos de un proyecto."""
    return _post(f"/projects/{project_id}/nodes/stop")


@mcp.tool()
def gns3_list_links(project_id: str) -> list[dict]:
    """Lista todos los links (conexiones) de un proyecto."""
    return _get(f"/projects/{project_id}/links")


@mcp.tool()
def gns3_get_node_console(project_id: str, node_id: str) -> dict:
    """Retorna la URL de consola (telnet/VNC) de un nodo.

    Returns dict with 'console', 'console_type', 'name', 'node_id'.
    """
    node = _get(f"/projects/{project_id}/nodes/{node_id}")
    return {
        "node_id": node.get("node_id"),
        "name": node.get("name"),
        "console": node.get("console"),
        "console_type": node.get("console_type"),
        "console_host": node.get("console_host", _GNS3_HOST),
    }


@mcp.tool()
def gns3_version() -> dict:
    """Retorna la versión del servidor GNS3."""
    return _get("/version")


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
        format="[lina-gns3] %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    log.info(
        "starting lina-gns3 (gns3=%s:%d, transport=%s)",
        _GNS3_HOST, _GNS3_PORT, _MCP_TRANSPORT,
    )
    mcp.run(transport=_MCP_TRANSPORT)


if __name__ == "__main__":
    main()
