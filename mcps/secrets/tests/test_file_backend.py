"""Unit tests for lina-secrets file backend.

Tests cover:
- _file_set / _file_get / _file_delete / _file_list round-trip
- rstrip('\\n') behavior (Docker secrets have trailing newline)
- path traversal protection (_validate_name + _file_path)
- atomic write + 0600 permissions
- LINA_SECRETS_BACKEND validation (fail-fast on typo)
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

# ─── helpers to bootstrap secrets module with temp FILE_DIR ──────────────────


def _load_secrets_module(tmp_path: Path, backend: str = "file"):
    """Import lina_secrets.server with env overrides pointing to tmp_path."""

    # Remove cached module so re-import picks up new env
    for key in list(sys.modules):
        if "lina_secrets" in key:
            del sys.modules[key]

    os.environ["LINA_SECRETS_BACKEND"] = backend
    os.environ["LINA_SECRETS_FILE_DIR"] = str(tmp_path)
    os.environ["MCP_TRANSPORT"] = "stdio"

    import lina_secrets.server as srv  # noqa: PLC0415

    return srv


def _cleanup_env():
    for k in ("LINA_SECRETS_BACKEND", "LINA_SECRETS_FILE_DIR", "MCP_TRANSPORT"):
        os.environ.pop(k, None)
    for key in list(sys.modules):
        if "lina_secrets" in key:
            del sys.modules[key]


# ─── basic round-trip ────────────────────────────────────────────────────────


def test_file_set_get_round_trip(tmp_path):
    srv = _load_secrets_module(tmp_path)
    try:
        srv._file_set("myservice", "apikey", "supersecret")
        assert srv._file_get("myservice", "apikey") == "supersecret"
    finally:
        _cleanup_env()


def test_file_delete(tmp_path):
    srv = _load_secrets_module(tmp_path)
    try:
        srv._file_set("svc", "k", "v")
        srv._file_delete("svc", "k")
        assert srv._file_get("svc", "k") is None
    finally:
        _cleanup_env()


def test_file_list(tmp_path):
    srv = _load_secrets_module(tmp_path)
    try:
        srv._file_set("svc", "k1", "v1")
        srv._file_set("svc", "k2", "v2")
        srv._file_set("other", "k3", "v3")
        keys = srv._file_list("svc")
        assert sorted(keys) == ["k1", "k2"]
        # other service not included
        assert "k3" not in keys
    finally:
        _cleanup_env()


# ─── Docker secrets trailing newline ─────────────────────────────────────────


def test_file_get_strips_only_trailing_newline(tmp_path):
    """Docker secrets typically have a trailing \\n; rstrip('\\n') should remove
    only that, not leading spaces or internal newlines."""
    srv = _load_secrets_module(tmp_path)
    try:
        # Write raw file to simulate Docker secret file
        (tmp_path / "svc__tok").write_text("  value  \n", encoding="utf-8")
        result = srv._file_get("svc", "tok")
        assert result == "  value  "  # trailing \n stripped, leading spaces kept
    finally:
        _cleanup_env()


def test_file_get_preserves_leading_space(tmp_path):
    srv = _load_secrets_module(tmp_path)
    try:
        srv._file_set("svc", "k", "  spaced")
        assert srv._file_get("svc", "k") == "  spaced"
    finally:
        _cleanup_env()


# ─── permissions ─────────────────────────────────────────────────────────────


def test_file_set_permissions_0600(tmp_path):
    srv = _load_secrets_module(tmp_path)
    try:
        srv._file_set("svc", "k", "secret")
        p = tmp_path / "svc__k"
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode == 0o600, f"expected 0600 got {oct(mode)}"
    finally:
        _cleanup_env()


# ─── path traversal protection ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "../etc/passwd",
        "../../root",
        "/etc/shadow",
        "a/b",
        "a\x00b",
        "a" * 65,  # too long
        "",
    ],
)
def test_validate_name_rejects_traversal(tmp_path, name):
    srv = _load_secrets_module(tmp_path)
    try:
        with pytest.raises(ValueError):
            srv._validate_name(name, "service")
    finally:
        _cleanup_env()


def test_file_path_traversal_via_resolve(tmp_path, monkeypatch):
    """Even if _validate_name somehow passed, _file_path resolve() guard catches it."""
    srv = _load_secrets_module(tmp_path)
    try:
        # Bypass _validate_name by patching it
        monkeypatch.setattr(srv, "_validate_name", lambda *a: None)
        with pytest.raises(ValueError, match="ruta fuera de FILE_DIR"):
            srv._file_path("../etc", "passwd")
    finally:
        _cleanup_env()


# ─── backend validation ──────────────────────────────────────────────────────


def test_invalid_backend_raises_on_import(tmp_path):
    for key in list(sys.modules):
        if "lina_secrets" in key:
            del sys.modules[key]
    os.environ["LINA_SECRETS_BACKEND"] = "invalid_backend"
    os.environ["LINA_SECRETS_FILE_DIR"] = str(tmp_path)
    try:
        with pytest.raises(ValueError, match="LINA_SECRETS_BACKEND"):
            import lina_secrets.server  # noqa: F401, PLC0415
    finally:
        _cleanup_env()
