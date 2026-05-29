"""Unit tests — lina-systemd-user.

Testea la lógica de validación de nombres de unidad y la comprobación de
permisos de escritura. Mockea completamente subprocess para no tocar systemd.
"""

from __future__ import annotations

import pytest


from lina_systemd_user.server import _validate_unit, _require_writable


# ─── tests de validación de nombre de unidad ──────────────────────────────────

class TestValidateUnit:
    @pytest.mark.parametrize("unit,expected", [
        ("lina-goosed", "lina-goosed.service"),
        ("lina-goosed.service", "lina-goosed.service"),
        ("lina-mcp-secrets.service", "lina-mcp-secrets.service"),
        ("some-timer.timer", "some-timer.timer"),
        ("my-unit.socket", "my-unit.socket"),
    ])
    def test_valid_units(self, unit, expected):
        assert _validate_unit(unit) == expected

    @pytest.mark.parametrize("bad_unit", [
        "",
        "unit with spaces",
        "unit/slash",
        "unit\x00null",
        "../../etc/passwd",
    ])
    def test_invalid_unit_names_raise(self, bad_unit):
        with pytest.raises(ValueError, match="inválido"):
            _validate_unit(bad_unit)

    def test_adds_service_suffix_when_missing(self):
        result = _validate_unit("lina-goosed")
        assert result.endswith(".service")

    def test_does_not_double_add_suffix(self):
        result = _validate_unit("lina-goosed.service")
        assert result.count(".service") == 1


# ─── tests de restricción de escritura ───────────────────────────────────────

class TestRequireWritable:
    def test_lina_unit_is_writable(self):
        _require_writable("lina-goosed.service")  # no debe lanzar

    def test_lina_prefix_variants_are_writable(self):
        _require_writable("lina-mcp-secrets.service")
        _require_writable("lina-custom.timer")

    def test_foreign_unit_raises(self):
        with pytest.raises(PermissionError, match="namespace"):
            _require_writable("nginx.service")

    def test_almost_lina_but_not_raises(self):
        """'my-lina-service' no tiene el prefijo 'lina-' al inicio → debe rechazarse."""
        with pytest.raises(PermissionError):
            _require_writable("my-lina-service.service")

    def test_custom_regex_via_env(self, monkeypatch):
        monkeypatch.setenv("LINA_SYSTEMD_UNIT_REGEX", r"^test-")
        import importlib, lina_systemd_user.server as m
        importlib.reload(m)
        m._require_writable("test-unit.service")  # OK
        with pytest.raises(PermissionError):
            m._require_writable("lina-goosed.service")  # no matchea el nuevo regex


# ─── tests de svc_status mockeando subprocess ────────────────────────────────

class TestSvcStatusMocked:
    def test_svc_status_calls_systemctl(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, stdout="active\n", stderr="")

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_run)

        from lina_systemd_user.server import svc_status
        result = svc_status("lina-goosed")
        assert result["exit_code"] == 0
        assert any("status" in c for c in calls[0])

    def test_svc_status_rejects_foreign_unit(self, monkeypatch):
        """svc_status sobre unidad no-lina no debe lanzar (es read-only)."""
        def fake_run(cmd, **kwargs):
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_run)

        from lina_systemd_user.server import svc_status
        # read-only no requiere namespace check → debe funcionar
        result = svc_status("nginx.service")
        assert "exit_code" in result
