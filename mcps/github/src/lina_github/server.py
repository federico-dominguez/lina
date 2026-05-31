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


def _put(path: str, json: dict) -> Any:
    with _client() as c:
        r = c.put(path, json=json)
        r.raise_for_status()
        return r.json()


def _delete(path: str, json: dict | None = None) -> Any:
    with _client() as c:
        r = c.request("DELETE", path, json=json)
        r.raise_for_status()
        return r.json() if r.content else {}


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


# ── Write tools ───────────────────────────────────────────────────────────────


@mcp.tool()
def github_create_branch(owner: str, repo: str, branch: str, sha: str) -> dict:
    """Crea una branch a partir de un SHA (commit, tag, o HEAD de otra rama).

    Args:
        sha: SHA del commit de origen. Obtenerlo con github_list_branches.
    """
    return _post(
        f"/repos/{owner}/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha}
    )


@mcp.tool()
def github_delete_branch(owner: str, repo: str, branch: str) -> dict:
    """Elimina una branch del repositorio."""
    return _delete(f"/repos/{owner}/{repo}/git/refs/heads/{branch}")


@mcp.tool()
def github_create_or_update_file(
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str,
    sha: str = "",
) -> dict:
    """Crea o actualiza un archivo en el repositorio (commit directo vía API).

    Args:
        content: contenido en texto plano (se encode a base64 internamente).
        sha:     SHA del blob actual si el archivo ya existe (requerido para update).
                 Dejarlo vacío para crear archivos nuevos.
    """
    import base64

    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    return _put(f"/repos/{owner}/{repo}/contents/{path}", json=payload)


@mcp.tool()
def github_delete_file(
    owner: str,
    repo: str,
    path: str,
    message: str,
    sha: str,
    branch: str,
) -> dict:
    """Elimina un archivo del repositorio.

    Args:
        sha: SHA del blob del archivo (obtenido con github_get_file).
    """
    return _delete(
        f"/repos/{owner}/{repo}/contents/{path}",
        json={"message": message, "sha": sha, "branch": branch},
    )


@mcp.tool()
def github_add_comment(owner: str, repo: str, issue_number: int, body: str) -> dict:
    """Agrega un comentario a un issue o pull request."""
    return _post(f"/repos/{owner}/{repo}/issues/{issue_number}/comments", json={"body": body})


@mcp.tool()
def github_close_issue(owner: str, repo: str, issue_number: int, comment: str = "") -> dict:
    """Cierra un issue. Si se provee 'comment', lo agrega antes de cerrar.

    Returns:
        Dict con claves 'issue' (respuesta del PATCH) y 'comment' (respuesta
        del POST si se envio comentario, None en caso contrario).
    """
    comment_result = None
    if comment:
        comment_result = _post(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": comment},
        )
    issue_result = _patch(
        f"/repos/{owner}/{repo}/issues/{issue_number}",
        json={"state": "closed"},
    )
    return {"issue": issue_result, "comment": comment_result}


@mcp.tool()
def github_request_review(
    owner: str,
    repo: str,
    pull_number: int,
    reviewers: list[str] | None = None,
    team_reviewers: list[str] | None = None,
) -> dict:
    """Solicita review en un pull request.

    Args:
        reviewers:      lista de usuarios (ej: ['copilot-ai']).
        team_reviewers: lista de equipos (ej: ['my-org/backend-team']).
    """
    payload: dict[str, Any] = {}
    if reviewers:
        payload["reviewers"] = reviewers
    if team_reviewers:
        payload["team_reviewers"] = team_reviewers
    return _post(f"/repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers", json=payload)


@mcp.tool()
def github_merge_pr(
    owner: str,
    repo: str,
    pull_number: int,
    commit_title: str = "",
    merge_method: str = "squash",
) -> dict:
    """Mergea un pull request.

    Args:
        merge_method: merge | squash | rebase (default: squash).
    """
    payload: dict[str, Any] = {"merge_method": merge_method}
    if commit_title:
        payload["commit_title"] = commit_title
    return _put(f"/repos/{owner}/{repo}/pulls/{pull_number}/merge", json=payload)


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
