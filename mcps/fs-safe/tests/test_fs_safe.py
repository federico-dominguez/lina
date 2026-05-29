"""Unit tests — lina-fs-safe.

Testea la lógica de allowlist, resolución de rutas y helpers internos.
No toca el filesystem real fuera del directorio tmp de pytest.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# ─── tests de allowlist y resolución de rutas ─────────────────────────────────

class TestAllowlist:
    def test_path_within_allowlist_is_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINA_FS_ALLOWLIST", str(tmp_path))
        import importlib, lina_fs_safe.server as m
        importlib.reload(m)
        assert m._is_within_allowlist(tmp_path / "subdir" / "file.txt")

    def test_path_outside_allowlist_is_rejected(self, tmp_path, monkeypatch):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setenv("LINA_FS_ALLOWLIST", str(allowed))
        import importlib, lina_fs_safe.server as m
        importlib.reload(m)
        outside = tmp_path / "other" / "file.txt"
        assert not m._is_within_allowlist(outside)

    def test_require_writable_raises_outside_allowlist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINA_FS_ALLOWLIST", str(tmp_path / "safe"))
        import importlib, lina_fs_safe.server as m
        importlib.reload(m)
        with pytest.raises(PermissionError, match="allowlist"):
            m._require_writable(tmp_path / "unsafe" / "file.txt")

    def test_require_writable_passes_inside_allowlist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINA_FS_ALLOWLIST", str(tmp_path))
        import importlib, lina_fs_safe.server as m
        importlib.reload(m)
        m._require_writable(tmp_path / "file.txt")  # no debe lanzar


class TestResolve:
    def test_resolve_expands_home(self):
        from lina_fs_safe.server import _resolve
        result = _resolve("~/lina")
        assert not str(result).startswith("~")
        assert Path.home() in result.parents or result == Path.home() / "lina"

    def test_resolve_absolute_path(self):
        from lina_fs_safe.server import _resolve
        assert _resolve("/tmp/foo") == Path("/tmp/foo")


# ─── tests de tools (sobre filesystem temporal) ───────────────────────────────

class TestFsTools:
    @pytest.fixture(autouse=True)
    def setup_allowlist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINA_FS_ALLOWLIST", str(tmp_path))
        import importlib, lina_fs_safe.server as m
        importlib.reload(m)
        self.m = m
        self.root = tmp_path

    def test_fs_write_and_read(self):
        path = str(self.root / "hello.txt")
        result = self.m.fs_write(path, "hola mundo")
        assert "ok" in result
        content = self.m.fs_read(path)
        assert content == "hola mundo"

    def test_fs_write_outside_allowlist_raises(self):
        with pytest.raises(PermissionError):
            self.m.fs_write("/etc/evil.txt", "pwned")

    def test_fs_list_returns_entries(self):
        (self.root / "a.txt").write_text("a")
        (self.root / "b.txt").write_text("b")
        result = self.m.fs_list(str(self.root))
        names = [e["name"] for e in result]
        assert "a.txt" in names
        assert "b.txt" in names

    def test_fs_mkdir_creates_directory(self):
        new_dir = str(self.root / "subdir" / "nested")
        result = self.m.fs_mkdir(new_dir)
        assert "ok" in result
        assert Path(new_dir).is_dir()

    def test_fs_delete_removes_file(self):
        f = self.root / "del.txt"
        f.write_text("bye")
        result = self.m.fs_delete(str(f))
        assert "ok" in result
        assert not f.exists()

    def test_fs_stat_returns_metadata(self):
        f = self.root / "stat.txt"
        f.write_text("data")
        result = self.m.fs_stat(str(f))
        assert result["type"] == "file"
        assert result["size"] == 4
