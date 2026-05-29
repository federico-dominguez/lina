"""LINA MCP fs-safe — operaciones de filesystem con allowlist rígida.

Reglas:
    - LECTURA permitida en cualquier ruta legible por el usuario.
    - ESCRITURA / BORRADO permitidos SOLO si la ruta resuelta (sin symlink
      escape) está dentro de alguna raíz del allowlist.
    - Todas las operaciones de escritura emiten un audit log a stderr y a
      ~/lina/logs/fs-safe.audit.log (JSON line).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
    format="[lina-fs-safe] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("lina-fs-safe")

_DEFAULT_ALLOWLIST = f"{Path.home()}/lina:{Path.home()}/Documents:{Path.home()}/IdeaProjects"
ALLOWLIST: list[Path] = [
    Path(p).expanduser().resolve()
    for p in os.environ.get("LINA_FS_ALLOWLIST", _DEFAULT_ALLOWLIST).split(":")
    if p.strip()
]

MAX_READ_BYTES = int(os.environ.get("LINA_FS_MAX_READ_BYTES", str(2 * 1024 * 1024)))  # 2 MiB
MAX_WRITE_BYTES = int(os.environ.get("LINA_FS_MAX_WRITE_BYTES", str(8 * 1024 * 1024)))  # 8 MiB

_AUDIT_DIR = Path.home() / "lina" / "logs"
_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
_AUDIT_LOG = _AUDIT_DIR / "fs-safe.audit.log"

# Transport config — read early because FastMCP bakes host/port at construction.
# MCP_TRANSPORT=streamable-http  enables HTTP mode (for containerised Fase 2+).
# MCP_PORT overrides the listening port in HTTP mode (default 8000).
# Without MCP_TRANSPORT the server starts in stdio mode (current / Fase 0-1).
_MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
_MCP_HTTP_PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP(
    "lina-fs-safe",
    # host/port are only used when transport="streamable-http".
    host="0.0.0.0",
    port=_MCP_HTTP_PORT,
)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _resolve(p: str) -> Path:
    return Path(p).expanduser().resolve(strict=False)


def _is_within_allowlist(path: Path) -> bool:
    for root in ALLOWLIST:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _require_writable(path: Path) -> None:
    if not _is_within_allowlist(path):
        raise PermissionError(
            f"ruta fuera del allowlist para escritura: {path}\n"
            f"allowlist: {[str(p) for p in ALLOWLIST]}"
        )


def _audit(action: str, path: Path, **extra: object) -> None:
    record = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "action": action,
        "path": str(path),
        **extra,
    }
    line = json.dumps(record, ensure_ascii=False)
    log.info(line)
    try:
        with _AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as e:
        log.warning("no se pudo escribir audit log: %s", e)


# ─── tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
def fs_allowlist() -> list[str]:
    """Devuelve las rutas raíz donde la escritura está permitida."""
    return [str(p) for p in ALLOWLIST]


@mcp.tool()
def fs_read(path: str, max_bytes: int | None = None) -> str:
    """Lee un archivo de texto (UTF-8). Acotado por max_bytes (default 2 MiB)."""
    p = _resolve(path)
    if not p.is_file():
        raise FileNotFoundError(f"no es un archivo: {p}")
    limit = min(max_bytes or MAX_READ_BYTES, MAX_READ_BYTES)
    data = p.read_bytes()[:limit]
    return data.decode("utf-8", errors="replace")


@mcp.tool()
def fs_list(path: str) -> list[dict]:
    """Lista el contenido de un directorio (no recursivo)."""
    p = _resolve(path)
    if not p.is_dir():
        raise NotADirectoryError(f"no es un directorio: {p}")
    out = []
    for child in sorted(p.iterdir()):
        try:
            st = child.stat()
            out.append(
                {
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file" if child.is_file() else "other",
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                }
            )
        except OSError:
            continue
    return out


@mcp.tool()
def fs_stat(path: str) -> dict:
    """Metadata de un archivo o directorio."""
    p = _resolve(path)
    st = p.stat()
    return {
        "path": str(p),
        "type": "dir" if p.is_dir() else "file" if p.is_file() else "other",
        "size": st.st_size,
        "mtime": int(st.st_mtime),
        "mode": oct(st.st_mode),
        "writable_by_lina": _is_within_allowlist(p),
    }


@mcp.tool()
def fs_write(path: str, content: str, overwrite: bool = False, create_parents: bool = True) -> str:
    """Escribe un archivo de texto. Requiere `overwrite=True` si ya existe."""
    p = _resolve(path)
    _require_writable(p)
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise ValueError(f"contenido {len(data)}B excede LINA_FS_MAX_WRITE_BYTES={MAX_WRITE_BYTES}")
    if p.exists() and not overwrite:
        raise FileExistsError(f"{p} ya existe; usar overwrite=True")
    if create_parents:
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()[:12]
    _audit("write", p, bytes=len(data), sha256_12=digest, overwrite=overwrite)
    return f"ok: {p} ({len(data)}B sha256:{digest})"


@mcp.tool()
def fs_mkdir(path: str, parents: bool = True) -> str:
    """Crea un directorio dentro del allowlist."""
    p = _resolve(path)
    _require_writable(p)
    p.mkdir(parents=parents, exist_ok=True)
    _audit("mkdir", p)
    return f"ok: mkdir {p}"


@mcp.tool()
def fs_delete(path: str, recursive: bool = False) -> str:
    """Elimina un archivo o directorio dentro del allowlist. `recursive=True` para directorios."""
    p = _resolve(path)
    _require_writable(p)
    # Bloqueo extra: no permitir eliminar una raíz del allowlist directamente.
    if p in ALLOWLIST:
        raise PermissionError(f"no se puede eliminar una raíz del allowlist: {p}")
    if not p.exists():
        raise FileNotFoundError(f"no existe: {p}")
    if p.is_dir():
        if not recursive:
            raise IsADirectoryError(f"{p} es directorio; usar recursive=True")
        shutil.rmtree(p)
    else:
        p.unlink()
    _audit("delete", p, recursive=recursive)
    return f"ok: deleted {p}"


@mcp.tool()
def fs_move(src: str, dst: str, overwrite: bool = False) -> str:
    """Mueve/renombra. Tanto src como dst deben estar en el allowlist."""
    s, d = _resolve(src), _resolve(dst)
    _require_writable(s)
    _require_writable(d)
    if d.exists() and not overwrite:
        raise FileExistsError(f"destino existe: {d}")
    if d.exists():
        if d.is_dir():
            shutil.rmtree(d)
        else:
            d.unlink()
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    _audit("move", s, dst=str(d))
    return f"ok: {s} → {d}"


def main() -> None:
    log.info("starting allowlist=%s", [str(p) for p in ALLOWLIST])
    log.info("transport=%s port=%d", _MCP_TRANSPORT, _MCP_HTTP_PORT)
    mcp.run(transport=_MCP_TRANSPORT)


if __name__ == "__main__":
    main()
