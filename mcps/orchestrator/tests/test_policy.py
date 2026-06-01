"""Tests para lina_orchestrator.domain.policy."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from lina_orchestrator.domain.policy import PolicyStore, RolePolicy


# ─── helpers ─────────────────────────────────────────────────────────────────

MINIMAL_YAML = textwrap.dedent("""\
    version: "1"
    roles:
      dev:
        description: Dev role
        allowed_mcps:
          - lina-fs-safe
          - lina-github
        sudo_allowed: false
        max_runtime_minutes: 30
        max_tokens_per_run: 50000
        max_usd_per_day: 2.0
        needs_approval_for:
          - pr_merge
""")

FULL_YAML = textwrap.dedent("""\
    version: "1"
    roles:
      dev:
        allowed_mcps: [lina-fs-safe, lina-github]
        sudo_allowed: false
        max_runtime_minutes: 30
        max_tokens_per_run: 50000
        max_usd_per_day: 2.0
        needs_approval_for: [pr_merge]
      ops:
        allowed_mcps: [lina-shell-policy, lina-secrets]
        sudo_allowed: true
        sudo_allowlist: ["lina-deploy restart"]
        max_runtime_minutes: 15
        max_tokens_per_run: 20000
        max_usd_per_day: 1.0
        needs_approval_for: [deploy_production]
      study:
        allowed_mcps: [lina-moodle, lina-db]
        sudo_allowed: false
        max_runtime_minutes: 20
        max_tokens_per_run: 30000
        max_usd_per_day: 0.0
        needs_approval_for: [quiz_submit]
      research:
        allowed_mcps: [lina-db]
        sudo_allowed: false
        max_runtime_minutes: 15
        max_tokens_per_run: 40000
        max_usd_per_day: 0.0
        needs_approval_for: []
""")


@pytest.fixture
def yaml_file(tmp_path: Path) -> Path:
    f = tmp_path / "policies.yaml"
    f.write_text(FULL_YAML)
    return f


@pytest.fixture
def store(yaml_file: Path) -> PolicyStore:
    return PolicyStore.from_yaml(yaml_file)


# ─── PolicyStore.from_yaml ────────────────────────────────────────────────────


def test_loads_all_four_roles(store: PolicyStore) -> None:
    assert sorted(store.roles()) == ["dev", "ops", "research", "study"]


def test_get_returns_correct_policy(store: PolicyStore) -> None:
    dev = store.get("dev")
    assert isinstance(dev, RolePolicy)
    assert "lina-fs-safe" in dev.allowed_mcps
    assert dev.max_runtime_minutes == 30


def test_get_unknown_role_raises_key_error(store: PolicyStore) -> None:
    with pytest.raises(KeyError, match="'manager' no definido"):
        store.get("manager")


def test_fail_closed_unknown_role_message_lists_defined(store: PolicyStore) -> None:
    try:
        store.get("unknown_role")
    except KeyError as exc:
        msg = str(exc)
        assert "dev" in msg
        assert "ops" in msg


def test_file_not_found_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        PolicyStore.from_yaml(tmp_path / "does_not_exist.yaml")


# ─── RolePolicy validation ────────────────────────────────────────────────────


def test_sudo_allowed_without_allowlist_raises() -> None:
    with pytest.raises(ValidationError, match="sudo_allowlist"):
        RolePolicy(
            allowed_mcps=["lina-shell-policy"],
            sudo_allowed=True,
            sudo_allowlist=[],  # empty → must fail
        )


def test_sudo_allowed_with_allowlist_ok() -> None:
    policy = RolePolicy(
        allowed_mcps=["lina-shell-policy"],
        sudo_allowed=True,
        sudo_allowlist=["lina-deploy restart"],
    )
    assert policy.sudo_allowed is True


def test_max_runtime_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        RolePolicy(allowed_mcps=[], max_runtime_minutes=0)


def test_max_tokens_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        RolePolicy(allowed_mcps=[], max_tokens_per_run=0)


def test_max_usd_can_be_zero() -> None:
    policy = RolePolicy(allowed_mcps=[], max_usd_per_day=0.0)
    assert policy.max_usd_per_day == 0.0


# ─── RolePolicy helpers ───────────────────────────────────────────────────────


def test_allows_mcp_true(store: PolicyStore) -> None:
    dev = store.get("dev")
    assert dev.allows_mcp("lina-fs-safe") is True


def test_allows_mcp_false(store: PolicyStore) -> None:
    dev = store.get("dev")
    assert dev.allows_mcp("lina-moodle") is False


def test_requires_approval_true(store: PolicyStore) -> None:
    dev = store.get("dev")
    assert dev.requires_approval("pr_merge") is True


def test_requires_approval_false(store: PolicyStore) -> None:
    dev = store.get("dev")
    assert dev.requires_approval("read_file") is False


def test_research_needs_no_approval(store: PolicyStore) -> None:
    research = store.get("research")
    assert research.needs_approval_for == []


# ─── PolicyStore.reload ───────────────────────────────────────────────────────


def test_reload_picks_up_file_changes(tmp_path: Path) -> None:
    f = tmp_path / "policies.yaml"
    f.write_text(MINIMAL_YAML)
    store = PolicyStore.from_yaml(f)
    assert store.get("dev").max_runtime_minutes == 30

    updated = MINIMAL_YAML.replace("max_runtime_minutes: 30", "max_runtime_minutes: 60")
    f.write_text(updated)
    store.reload()

    assert store.get("dev").max_runtime_minutes == 60


def test_reload_invalid_yaml_raises_and_keeps_old_state(tmp_path: Path) -> None:
    f = tmp_path / "policies.yaml"
    f.write_text(MINIMAL_YAML)
    store = PolicyStore.from_yaml(f)

    f.write_text("this: is: not: valid: yaml: !!!")
    with pytest.raises(Exception):  # yaml or pydantic error
        store.reload()

    # Old state still accessible
    assert store.get("dev").max_runtime_minutes == 30


# ─── Production policies.yaml parses OK ──────────────────────────────────────


def test_real_policies_yaml_loads(tmp_path: Path) -> None:
    """Verifica que el config/policies.yaml del repo es válido."""
    repo_root = Path(__file__).parents[3]  # mcps/orchestrator/tests/ → repo root
    real_path = repo_root / "config" / "policies.yaml"
    if not real_path.exists():
        pytest.skip("config/policies.yaml no encontrado")
    store = PolicyStore.from_yaml(real_path)
    roles = store.roles()
    assert len(roles) >= 4, f"Se esperan al menos 4 roles, hay: {roles}"
    for role in ["dev", "ops", "study", "research"]:
        assert role in roles, f"Rol '{role}' ausente en policies.yaml"
