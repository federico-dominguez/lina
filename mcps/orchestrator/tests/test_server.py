"""Tests para lina_orchestrator.server (smoke + tool wrappers)."""

from __future__ import annotations

import importlib
import textwrap
from pathlib import Path

import pytest

FULL_YAML = textwrap.dedent("""\
    version: "1"
    roles:
      dev:
        description: Dev role
        allowed_mcps: [lina-fs-safe, lina-github]
        sudo_allowed: false
        max_runtime_minutes: 30
        max_tokens_per_run: 50000
        max_usd_per_day: 2.0
        needs_approval_for: [pr_merge]
""")


@pytest.fixture
def reloaded_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Carga el módulo server con LINA_POLICIES_FILE apuntando a un tmp."""
    policies = tmp_path / "policies.yaml"
    policies.write_text(FULL_YAML)
    monkeypatch.setenv("LINA_POLICIES_FILE", str(policies))
    # Re-import para que tome el env var.
    import lina_orchestrator.server as srv

    importlib.reload(srv)
    # Limpiar singleton.
    srv._service = None
    yield srv
    srv._service = None


def test_build_service_raises_when_policies_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "nope.yaml"
    monkeypatch.setenv("LINA_POLICIES_FILE", str(missing))
    import lina_orchestrator.server as srv

    importlib.reload(srv)
    srv._service = None
    with pytest.raises(FileNotFoundError):
        srv._build_service()


def test_list_roles_tool_returns_roles(reloaded_server) -> None:
    assert reloaded_server.list_roles() == ["dev"]


def test_get_role_tool_returns_dict(reloaded_server) -> None:
    out = reloaded_server.get_role("dev")
    assert out["role"] == "dev"
    assert out["allowed_mcps"] == ["lina-fs-safe", "lina-github"]


def test_get_role_tool_unknown_returns_error_dict(reloaded_server) -> None:
    out = reloaded_server.get_role("ghost")
    assert "error" in out
    assert out["known_roles"] == ["dev"]


def test_check_mcp_allowed_tool(reloaded_server) -> None:
    assert reloaded_server.check_mcp_allowed("dev", "lina-fs-safe") == {
        "role": "dev",
        "mcp": "lina-fs-safe",
        "allowed": True,
    }
    out = reloaded_server.check_mcp_allowed("dev", "lina-shell-policy")
    assert out["allowed"] is False


def test_check_requires_approval_tool(reloaded_server) -> None:
    out = reloaded_server.check_requires_approval("dev", "pr_merge")
    assert out["requires_approval"] is True
    out2 = reloaded_server.check_requires_approval("dev", "list_files")
    assert out2["requires_approval"] is False


def test_reload_policies_tool(reloaded_server) -> None:
    out = reloaded_server.reload_policies()
    assert out["success"] is True
    assert out["roles_count"] == 1
