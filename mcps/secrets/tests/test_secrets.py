"""Unit tests — lina-secrets.

Testea la lógica de validación sin tocar el keyring real
(keyring está patcheado con un backend en memoria).
"""

from __future__ import annotations

import pytest
import keyring.backend
import keyring


# ─── in-memory keyring backend para tests ─────────────────────────────────────

class _MemoryKeyring(keyring.backend.KeyringBackend):
    """Backend de keyring en memoria, sin dependencia de libsecret/DBus."""

    priority = 100  # mayor que SecretService → se elige primero

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        import keyring.errors
        if (service, username) not in self._store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self._store[(service, username)]


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch):
    """Sustituye el backend de keyring por uno en memoria antes de cada test."""
    backend = _MemoryKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    yield backend


# ─── importar módulo bajo test ─────────────────────────────────────────────────
# Importamos después del fixture para que el monkeypatch esté activo
from lina_secrets.server import _svc, _index_get, _index_add, _index_remove  # noqa: E402


# ─── tests de helpers internos ────────────────────────────────────────────────

class TestSvc:
    def test_prefixes_with_namespace(self, monkeypatch):
        monkeypatch.setenv("LINA_KEYRING_SERVICE", "test-ns")
        # re-importar para que tome el nuevo env
        import importlib, lina_secrets.server as m
        importlib.reload(m)
        assert m._svc("myservice") == "test-ns:myservice"

    def test_rejects_empty_service(self):
        with pytest.raises(ValueError, match="inválido"):
            _svc("")

    def test_rejects_slash_in_service(self):
        with pytest.raises(ValueError, match="inválido"):
            _svc("a/b")

    def test_rejects_null_byte(self):
        with pytest.raises(ValueError, match="inválido"):
            _svc("a\x00b")

    def test_valid_service(self):
        result = _svc("moodle")
        assert result.endswith(":moodle")


class TestIndex:
    def test_empty_index_returns_empty_set(self):
        assert _index_get("nonexistent") == set()

    def test_add_and_get(self):
        _index_add("svc1", "key1")
        assert "key1" in _index_get("svc1")

    def test_add_multiple_keys(self):
        _index_add("svc2", "a")
        _index_add("svc2", "b")
        assert _index_get("svc2") == {"a", "b"}

    def test_remove_key(self):
        _index_add("svc3", "x")
        _index_remove("svc3", "x")
        assert "x" not in _index_get("svc3")

    def test_remove_last_key_clears_index(self):
        _index_add("svc4", "only")
        _index_remove("svc4", "only")
        assert _index_get("svc4") == set()

    def test_remove_nonexistent_key_is_safe(self):
        """No debe lanzar excepción al intentar eliminar una key que no existe en el índice."""
        _index_remove("svc5", "ghost")  # no debería lanzar
