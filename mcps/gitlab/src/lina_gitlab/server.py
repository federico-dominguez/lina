"""LINA MCP lina-gitlab — acceso a GitLab UTEC via REST API.

Tools expuestos:
    gitlab_list_projects        → proyectos del usuario (id, name, url, namespace)
    gitlab_get_project          → detalle de un proyecto (branches, descripción)
    gitlab_list_branches        → branches de un proyecto
    gitlab_get_file             → contenido raw de un archivo en un repo
    gitlab_list_issues          → issues de un proyecto (filtros: state, labels)
    gitlab_create_issue         → crea un issue
    gitlab_list_mrs             → merge requests de un proyecto
    gitlab_create_mr            → crea un merge request
    gitlab_get_pipeline         → estado del último pipeline de una branch

Configuración (variables de entorno):
    GITLAB_URL              Base URL de la instancia (default: https://git.utec.edu.uy)
    GITLAB_TOKEN            PAT directamente (prioridad sobre keyring)
    GITLAB_TOKEN_KEYRING    "service:key" apuntando al PAT en el keyring
                            (ej: "lina:gitlab:GITLAB_TOKEN")
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import httpx
import keyring
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
    format="[lina-gitlab] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("lina-gitlab")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GITLAB_URL = os.environ.get("GITLAB_URL", "https://git.utec.edu.uy").rstrip("/")


def _load_token() -> str:
    """Carga el PAT desde la variable de entorno o el keyring."""
    # 1. Directo en variable de entorno
    token = os.environ.get("GITLAB_TOKEN", "").strip()
    if token:
        return token

    # 2. Via keyring (formato "service:key")
    keyring_ref = os.environ.get("GITLAB_TOKEN_KEYRING", "").strip()
    if keyring_ref:
        parts = keyring_ref.rsplit(":", 1)
        if len(parts) == 2:
            service, key = parts
            value = keyring.get_password(service, key)
            if value:
                return value.strip()
        log.warning("GITLAB_TOKEN_KEYRING=%r — no encontré el token en el keyring", keyring_ref)

    raise RuntimeError(
        "No hay token de GitLab configurado. "
        "Guardá el PAT con: secret_set('lina:gitlab', 'GITLAB_TOKEN', '<token>') "
        "y setea GITLAB_TOKEN_KEYRING=lina:gitlab:GITLAB_TOKEN"
    )


def _client() -> httpx.Client:
    token = _load_token()
    return httpx.Client(
        base_url=f"{GITLAB_URL}/api/v4",
        headers={"PRIVATE-TOKEN": token},
        timeout=30,
    )


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    with _client() as c:
        r = c.get(path, params=params or {})
        r.raise_for_status()
        return r.json()


def _post(path: str, json: dict[str, Any]) -> Any:
    with _client() as c:
        r = c.post(path, json=json)
        r.raise_for_status()
        return r.json()


def _encode_id(project_id: str | int) -> str:
    """URL-encode un project_id si contiene '/' (namespace/path)."""
    s = str(project_id)
    if "/" in s:
        return s.replace("/", "%2F")
    return s


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

_MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
_MCP_HTTP_PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP("lina-gitlab", host="0.0.0.0", port=_MCP_HTTP_PORT)


@mcp.tool()
def gitlab_list_projects(
    search: str = "",
    owned: bool = True,
    per_page: int = 20,
) -> list[dict]:
    """Lista proyectos de GitLab accesibles por el usuario.

    Args:
        search:   Filtro por nombre (substring).
        owned:    True = sólo proyectos propios; False = todos los visibles.
        per_page: Máximo de resultados (1-100).
    """
    params: dict[str, Any] = {"per_page": min(per_page, 100), "order_by": "last_activity_at"}
    if search:
        params["search"] = search
    if owned:
        params["owned"] = "true"

    data = _get("/projects", params)
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "path_with_namespace": p["path_with_namespace"],
            "default_branch": p.get("default_branch"),
            "description": p.get("description") or "",
            "web_url": p["web_url"],
            "last_activity_at": p.get("last_activity_at"),
            "visibility": p.get("visibility"),
        }
        for p in data
    ]


@mcp.tool()
def gitlab_get_project(project_id: str) -> dict:
    """Devuelve detalles de un proyecto.

    Args:
        project_id: ID numérico o 'namespace/path' del proyecto.
    """
    data = _get(f"/projects/{_encode_id(project_id)}")
    return {
        "id": data["id"],
        "name": data["name"],
        "path_with_namespace": data["path_with_namespace"],
        "description": data.get("description") or "",
        "default_branch": data.get("default_branch"),
        "web_url": data["web_url"],
        "visibility": data.get("visibility"),
        "open_issues_count": data.get("open_issues_count"),
        "star_count": data.get("star_count"),
        "forks_count": data.get("forks_count"),
        "last_activity_at": data.get("last_activity_at"),
    }


@mcp.tool()
def gitlab_list_branches(project_id: str, search: str = "") -> list[dict]:
    """Lista branches de un proyecto.

    Args:
        project_id: ID numérico o 'namespace/path'.
        search:     Filtro por nombre.
    """
    params: dict[str, Any] = {"per_page": 100}
    if search:
        params["search"] = search
    data = _get(f"/projects/{_encode_id(project_id)}/repository/branches", params)
    return [
        {
            "name": b["name"],
            "default": b.get("default", False),
            "protected": b.get("protected", False),
            "merged": b.get("merged", False),
            "commit_sha": b["commit"]["id"][:12],
            "commit_message": b["commit"]["message"].split("\n")[0],
            "commit_date": b["commit"]["committed_date"],
        }
        for b in data
    ]


@mcp.tool()
def gitlab_get_file(
    project_id: str,
    file_path: str,
    ref: str = "HEAD",
) -> str:
    """Lee el contenido raw de un archivo en un repositorio.

    Args:
        project_id: ID numérico o 'namespace/path'.
        file_path:  Ruta relativa al archivo (ej: 'src/Main.java').
        ref:        Branch, tag o commit SHA (default: HEAD).
    """
    encoded_path = file_path.replace("/", "%2F")
    data = _get(
        f"/projects/{_encode_id(project_id)}/repository/files/{encoded_path}",
        {"ref": ref},
    )
    import base64
    content_b64 = data.get("content", "")
    try:
        return base64.b64decode(content_b64).decode("utf-8", errors="replace")
    except Exception:
        return content_b64


@mcp.tool()
def gitlab_list_issues(
    project_id: str,
    state: str = "opened",
    labels: str = "",
    per_page: int = 20,
) -> list[dict]:
    """Lista issues de un proyecto.

    Args:
        project_id: ID numérico o 'namespace/path'.
        state:      'opened', 'closed' o 'all'.
        labels:     Etiquetas separadas por comas (opcional).
        per_page:   Máximo de resultados (1-100).
    """
    params: dict[str, Any] = {"state": state, "per_page": min(per_page, 100)}
    if labels:
        params["labels"] = labels
    data = _get(f"/projects/{_encode_id(project_id)}/issues", params)
    return [
        {
            "iid": i["iid"],
            "title": i["title"],
            "state": i["state"],
            "labels": i.get("labels", []),
            "assignee": (i.get("assignee") or {}).get("username"),
            "created_at": i.get("created_at"),
            "web_url": i["web_url"],
        }
        for i in data
    ]


@mcp.tool()
def gitlab_create_issue(
    project_id: str,
    title: str,
    description: str = "",
    labels: str = "",
    assignee_id: int | None = None,
) -> dict:
    """Crea un issue en un proyecto.

    Args:
        project_id:  ID numérico o 'namespace/path'.
        title:       Título del issue.
        description: Descripción en Markdown (opcional).
        labels:      Etiquetas separadas por comas (opcional).
        assignee_id: ID numérico del usuario asignado (opcional).
    """
    body: dict[str, Any] = {"title": title}
    if description:
        body["description"] = description
    if labels:
        body["labels"] = labels
    if assignee_id is not None:
        body["assignee_ids"] = [assignee_id]
    data = _post(f"/projects/{_encode_id(project_id)}/issues", body)
    return {
        "iid": data["iid"],
        "title": data["title"],
        "state": data["state"],
        "web_url": data["web_url"],
    }


@mcp.tool()
def gitlab_list_mrs(
    project_id: str,
    state: str = "opened",
    per_page: int = 20,
) -> list[dict]:
    """Lista merge requests de un proyecto.

    Args:
        project_id: ID numérico o 'namespace/path'.
        state:      'opened', 'closed', 'merged' o 'all'.
        per_page:   Máximo de resultados (1-100).
    """
    params: dict[str, Any] = {"state": state, "per_page": min(per_page, 100)}
    data = _get(f"/projects/{_encode_id(project_id)}/merge_requests", params)
    return [
        {
            "iid": mr["iid"],
            "title": mr["title"],
            "state": mr["state"],
            "source_branch": mr["source_branch"],
            "target_branch": mr["target_branch"],
            "author": mr["author"]["username"],
            "created_at": mr.get("created_at"),
            "web_url": mr["web_url"],
        }
        for mr in data
    ]


@mcp.tool()
def gitlab_create_mr(
    project_id: str,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str = "",
    remove_source_branch: bool = False,
) -> dict:
    """Crea un merge request.

    Args:
        project_id:           ID numérico o 'namespace/path'.
        source_branch:        Branch origen.
        target_branch:        Branch destino.
        title:                Título del MR.
        description:          Descripción en Markdown (opcional).
        remove_source_branch: Si borrar la branch al hacer merge (default: False).
    """
    body: dict[str, Any] = {
        "source_branch": source_branch,
        "target_branch": target_branch,
        "title": title,
        "remove_source_branch": remove_source_branch,
    }
    if description:
        body["description"] = description
    data = _post(f"/projects/{_encode_id(project_id)}/merge_requests", body)
    return {
        "iid": data["iid"],
        "title": data["title"],
        "state": data["state"],
        "web_url": data["web_url"],
    }


@mcp.tool()
def gitlab_get_pipeline(
    project_id: str,
    ref: str = "HEAD",
) -> dict | None:
    """Devuelve el estado del último pipeline de una branch o commit.

    Args:
        project_id: ID numérico o 'namespace/path'.
        ref:        Branch, tag o commit SHA (default: HEAD branch del proyecto).
    """
    params: dict[str, Any] = {"per_page": 1, "order_by": "id", "sort": "desc"}
    if ref and ref != "HEAD":
        params["ref"] = ref
    data = _get(f"/projects/{_encode_id(project_id)}/pipelines", params)
    if not data:
        return None
    p = data[0]
    return {
        "id": p["id"],
        "status": p["status"],
        "ref": p["ref"],
        "sha": p["sha"][:12],
        "created_at": p.get("created_at"),
        "updated_at": p.get("updated_at"),
        "web_url": p["web_url"],
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    import logging
    logging.basicConfig(
        level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
        format="[lina-gitlab] %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    log.info(
        "starting lina-gitlab (url=%s, transport=%s)",
        GITLAB_URL, _MCP_TRANSPORT,
    )
    mcp.run(transport=_MCP_TRANSPORT)


if __name__ == "__main__":
    main()
