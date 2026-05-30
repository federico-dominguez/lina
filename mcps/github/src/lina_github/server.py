"""LINA MCP — GitHub REST API v3

Variables de entorno:
    GITHUB_TOKEN      PAT clásico con permisos repo/workflow
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

log = logging.getLogger("lina-github")

# ── Config ────────────────────────────────────────────────────────────────────
_GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
_GITHUB_API = "https://api.github.com"
_MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
_MCP_HTTP_PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP("lina-github", host="0.0.0.0", port=_MCP_HTTP_PORT)


# ── HTTP helper ───────────────────────────────────────────────────────────────
def _client() -> httpx.Client:
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if _GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {_GITHUB_TOKEN}"
    return httpx.Client(base_url=_GITHUB_API, headers=headers, timeout=30)


def _get(path: str, params: dict | None = None) -> Any:
    with _client() as c:
        r = c.get(path, params=params)
        r.raise_for_status()
        return r.json()


def _post(path: str, json: dict) -> Any:
    with _client() as c:
        r = c.post(path, json=json)
        r.raise_for_status()
        return r.json()


def _patch(path: str, json: dict) -> Any:
    with _client() as c:
        r = c.patch(path, json=json)
        r.raise_for_status()
        return r.json()


# ── Tools ─────────────────────────────────────────────────────────────────────
@mcp.tool()
def github_list_repos(owner: str, page: int = 1, per_page: int = 30) -> list[dict]:
    """Lista repositorios de un usuario u organización (owner)."""
    return _get(
        f"/users/{owner}/repos", params={"page": page, "per_page": per_page, "sort": "updated"}
    )


@mcp.tool()
def github_get_repo(owner: str, repo: str) -> dict:
    """Retorna metadata de un repositorio."""
    return _get(f"/repos/{owner}/{repo}")


@mcp.tool()
def github_list_branches(owner: str, repo: str) -> list[dict]:
    """Lista branches de un repositorio."""
    return _get(f"/repos/{owner}/{repo}/branches", params={"per_page": 100})


@mcp.tool()
def github_get_file(owner: str, repo: str, path: str, ref: str = "HEAD") -> dict:
    """Obtiene el contenido de un archivo (base64 decoded).

    Returns dict with keys: name, path, content (texto plano), size, sha, html_url.
    """
    data = _get(f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref})
    if isinstance(data, list):
        return {
            "type": "directory",
            "entries": [{"name": e["name"], "type": e["type"]} for e in data],
        }
    import base64

    content_b64 = data.get("content", "").replace("\n", "")
    data["content"] = (
        base64.b64decode(content_b64).decode("utf-8", errors="replace") if content_b64 else ""
    )
    return data


@mcp.tool()
def github_list_issues(
    owner: str,
    repo: str,
    state: str = "open",
    page: int = 1,
    per_page: int = 20,
) -> list[dict]:
    """Lista issues de un repositorio. state: open | closed | all."""
    return _get(
        f"/repos/{owner}/{repo}/issues", params={"state": state, "page": page, "per_page": per_page}
    )


@mcp.tool()
def github_create_issue(
    owner: str, repo: str, title: str, body: str = "", labels: list[str] | None = None
) -> dict:
    """Crea un issue en un repositorio."""
    payload: dict[str, Any] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    return _post(f"/repos/{owner}/{repo}/issues", json=payload)


@mcp.tool()
def github_list_prs(
    owner: str,
    repo: str,
    state: str = "open",
    page: int = 1,
    per_page: int = 20,
) -> list[dict]:
    """Lista pull requests de un repositorio. state: open | closed | all."""
    return _get(
        f"/repos/{owner}/{repo}/pulls", params={"state": state, "page": page, "per_page": per_page}
    )


@mcp.tool()
def github_create_pr(
    owner: str,
    repo: str,
    title: str,
    head: str,
    base: str,
    body: str = "",
    draft: bool = False,
) -> dict:
    """Crea un pull request.

    Args:
        head: rama origen (ej: feat/my-feature)
        base: rama destino (ej: main)
    """
    return _post(
        f"/repos/{owner}/{repo}/pulls",
        json={"title": title, "head": head, "base": base, "body": body, "draft": draft},
    )


@mcp.tool()
def github_list_workflow_runs(
    owner: str,
    repo: str,
    workflow_id: str = "",
    status: str = "",
    per_page: int = 10,
) -> list[dict]:
    """Lista ejecuciones de GitHub Actions.

    Args:
        workflow_id: nombre del archivo .yml o ID numérico; vacío = todas
        status:      completed | in_progress | queued | waiting; vacío = todas
    """
    path = (
        f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
        if workflow_id
        else f"/repos/{owner}/{repo}/actions/runs"
    )
    params: dict[str, Any] = {"per_page": per_page}
    if status:
        params["status"] = status
    data = _get(path, params=params)
    return data.get("workflow_runs", data)


@mcp.tool()
def github_search_code(query: str, page: int = 1, per_page: int = 10) -> list[dict]:
    """Busca código en GitHub. Ej: query='org:myorg filename:Makefile'."""
    data = _get("/search/code", params={"q": query, "page": page, "per_page": per_page})
    return data.get("items", [])


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
        format="[lina-github] %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    if not _GITHUB_TOKEN:
        log.warning(
            "GITHUB_TOKEN no configurado — peticiones sin autenticar (rate-limit: 60 req/h)"
        )
    log.info("starting lina-github (transport=%s, port=%d)", _MCP_TRANSPORT, _MCP_HTTP_PORT)
    mcp.run(transport=_MCP_TRANSPORT)


if __name__ == "__main__":
    main()
