"""Tests para lina_orchestrator.application.policy_service."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lina_orchestrator.application.policy_service import PolicyService
from lina_orchestrator.domain.policy import PolicyStore

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
      ops:
        description: Ops role
        allowed_mcps: [lina-shell-policy, lina-secrets]
        sudo_allowed: true
        sudo_allowlist: ["lina-deploy restart"]
        max_runtime_minutes: 15
        max_tokens_per_run: 20000
        max_usd_per_day: 1.0
        needs_approval_for: [deploy_production]
""")

REDUCED_YAML = textwrap.dedent("""\
    version: "1"
    roles:
      dev:
        allowed_mcps: [lina-fs-safe]
        sudo_allowed: false
        max_runtime_minutes: 30
        max_tokens_per_run: 50000
        max_usd_per_day: 2.0
""")


@pytest.fixture
def yaml_file(tmp_path: Path) -> Path:
    f = tmp_path / "policies.yaml"
    f.write_text(FULL_YAML)
    return f


@pytest.fixture
def svc(yaml_file: Path) -> PolicyService:
    return PolicyService(PolicyStore.from_yaml(yaml_file))


# ─── list_roles ──────────────────────────────────────────────────────────────


def test_list_roles_returns_sorted(svc: PolicyService) -> None:
    assert svc.list_roles() == ["dev", "ops"]


# ─── get_role ────────────────────────────────────────────────────────────────


def test_get_role_returns_summary_dict(svc: PolicyService) -> None:
    summary = svc.get_role("dev")
    assert summary["role"] == "dev"
    assert summary["description"] == "Dev role"
    assert summary["allowed_mcps"] == ["lina-fs-safe", "lina-github"]
    assert summary["sudo_allowed"] is False
    assert summary["sudo_allowlist"] == []
    assert summary["max_runtime_minutes"] == 30
    assert summary["max_tokens_per_run"] == 50000
    assert summary["max_usd_per_day"] == 2.0
    assert summary["needs_approval_for"] == ["pr_merge"]


def test_get_role_ops_includes_sudo_allowlist(svc: PolicyService) -> None:
    summary = svc.get_role("ops")
    assert summary["sudo_allowed"] is True
    assert summary["sudo_allowlist"] == ["lina-deploy restart"]


def test_get_role_unknown_raises(svc: PolicyService) -> None:
    with pytest.raises(KeyError):
        svc.get_role("hacker")


# ─── check_mcp_allowed ───────────────────────────────────────────────────────


def test_check_mcp_allowed_positive(svc: PolicyService) -> None:
    assert svc.check_mcp_allowed("dev", "lina-fs-safe") is True
    assert svc.check_mcp_allowed("ops", "lina-shell-policy") is True


def test_check_mcp_allowed_negative(svc: PolicyService) -> None:
    assert svc.check_mcp_allowed("dev", "lina-shell-policy") is False
    assert svc.check_mcp_allowed("ops", "lina-github") is False


def test_check_mcp_allowed_unknown_role_fails_closed(svc: PolicyService) -> None:
    assert svc.check_mcp_allowed("hacker", "lina-fs-safe") is False


# ─── check_requires_approval ─────────────────────────────────────────────────


def test_check_requires_approval_positive(svc: PolicyService) -> None:
    assert svc.check_requires_approval("dev", "pr_merge") is True
    assert svc.check_requires_approval("ops", "deploy_production") is True


def test_check_requires_approval_negative(svc: PolicyService) -> None:
    assert svc.check_requires_approval("dev", "list_files") is False


def test_check_requires_approval_unknown_role_fails_closed_to_true(svc: PolicyService) -> None:
    """Rol desconocido → pedir aprobación (lado seguro)."""
    assert svc.check_requires_approval("hacker", "anything") is True


# ─── reload ──────────────────────────────────────────────────────────────────


def test_reload_picks_up_yaml_changes(svc: PolicyService, yaml_file: Path) -> None:
    assert svc.list_roles() == ["dev", "ops"]
    yaml_file.write_text(REDUCED_YAML)
    result = svc.reload()
    assert result == {"success": True, "roles_count": 1, "roles": ["dev"]}
    assert svc.list_roles() == ["dev"]


def test_reload_with_broken_yaml_returns_error(svc: PolicyService, yaml_file: Path) -> None:
    yaml_file.write_text("this: is: not: valid: yaml: structure: for: roles")
    result = svc.reload()
    assert result["success"] is False
    assert "error" in result
    # El store sigue con las políticas anteriores (atomic reload).
    assert svc.list_roles() == ["dev", "ops"]


def test_reload_with_missing_file_returns_error(svc: PolicyService, yaml_file: Path) -> None:
    yaml_file.unlink()
    result = svc.reload()
    assert result["success"] is False
    assert "FileNotFoundError" in result["error"] or "No such file" in result["error"]
