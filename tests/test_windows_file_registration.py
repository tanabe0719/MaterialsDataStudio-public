"""Registry rights use mocks and disposable native HKCU trees, preserving user settings."""

from __future__ import annotations

import ctypes
import sys
import uuid
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import pytest

from materials_data_studio.integrations.windows_files import (
    DOCUMENT_TYPES,
    register_document_types,
    registration_entries,
    unregister_document_types,
)


def test_registration_handles_spaces_and_separate_document_icons(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Application Folder\MDS.exe")
    monkeypatch.setattr(sys, "_MEIPASS", r"C:\Application Folder\_internal", raising=False)
    entries = registration_entries()
    commands = [value for path, _name, value in entries if path.endswith(r"\shell\open\command")]
    assert commands == ['"C:\\Application Folder\\MDS.exe" "%1"'] * 2
    icons = [value for path, _name, value in entries if path.endswith(r"\DefaultIcon")]
    assert len(icons) == 2 and icons[0] != icons[1]
    assert not any("UserChoice" in path for path, _name, _value in entries)


def test_per_user_registration_is_idempotent_and_preserves_existing_defaults(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    values = {(r"Software\Classes\.mdss", ""): "Existing.Session.Handler"}
    writes, notifications = [], []

    class Key:
        def __init__(self, path, access):
            self.path, self.access = path, access

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    def query(key, name):
        if not key.access & 1:
            raise PermissionError("KEY_QUERY_VALUE is required")
        if (key.path, name) not in values:
            raise FileNotFoundError
        return values[key.path, name], 1

    def set_value(key, name, _reserved, _kind, value):
        if not key.access & 2:
            raise PermissionError("KEY_SET_VALUE is required")
        writes.append((key.path, name))
        values[key.path, name] = value

    fake = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        KEY_WRITE=2,
        KEY_READ=4,
        KEY_QUERY_VALUE=1,
        KEY_SET_VALUE=2,
        REG_SZ=1,
        CreateKeyEx=lambda _hive, path, _reserved, access: Key(path, access),
        QueryValueEx=query,
        SetValueEx=set_value,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(
            shell32=SimpleNamespace(SHChangeNotify=lambda *args: notifications.append(args))
        ),
        raising=False,
    )
    register_document_types()
    assert values[r"Software\Classes\.mdss", ""] == "Existing.Session.Handler"
    assert values[r"Software\Classes\.mdsg", ""] == DOCUMENT_TYPES[".mdsg"][0]
    assert notifications == [(0x08000000, 0, None, None)]
    write_count = len(writes)
    register_document_types()
    assert len(writes) == write_count and len(notifications) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows registry access masks")
def test_registration_repairs_partial_install_with_native_registry_handles(monkeypatch):
    """Redirect production registration into a disposable isolated HKCU tree."""
    import winreg

    prefix = rf"Software\MaterialsDataStudio\RegistrationTests\{uuid.uuid4().hex}"
    created: set[str] = {prefix}
    accesses: list[int] = []
    notifications = []
    access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE

    def create(hive, path, reserved, rights):
        assert hive == winreg.HKEY_CURRENT_USER
        relative = path.split("\\")
        for length in range(1, len(relative) + 1):
            created.add(prefix + "\\" + "\\".join(relative[:length]))
        accesses.append(rights)
        return winreg.CreateKeyEx(hive, prefix + "\\" + path, reserved, rights)

    def read(path, name):
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, prefix + "\\" + path, 0, winreg.KEY_QUERY_VALUE
        ) as key:
            return winreg.QueryValueEx(key, name)[0]

    try:
        # v0.14.4 can leave the first key created but with no values populated.
        with create(
            winreg.HKEY_CURRENT_USER,
            r"Software\MaterialsDataStudio\Capabilities",
            0,
            winreg.KEY_WRITE,
        ) as key:
            with pytest.raises(PermissionError) as error:
                winreg.QueryValueEx(key, "ApplicationName")
            assert error.value.winerror == 5
        with create(winreg.HKEY_CURRENT_USER, r"Software\Classes\.mdss", 0, access) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Existing.Session.Handler")
        accesses.clear()
        proxy = SimpleNamespace(
            HKEY_CURRENT_USER=winreg.HKEY_CURRENT_USER,
            KEY_QUERY_VALUE=winreg.KEY_QUERY_VALUE,
            KEY_SET_VALUE=winreg.KEY_SET_VALUE,
            REG_SZ=winreg.REG_SZ,
            CreateKeyEx=create,
            QueryValueEx=winreg.QueryValueEx,
            SetValueEx=winreg.SetValueEx,
            OpenKey=lambda hive, path, reserved, rights: winreg.OpenKey(
                hive, prefix + "\\" + path, reserved, rights
            ),
            DeleteValue=winreg.DeleteValue,
        )
        monkeypatch.setitem(sys.modules, "winreg", proxy)
        monkeypatch.setattr(
            ctypes,
            "windll",
            SimpleNamespace(
                shell32=SimpleNamespace(SHChangeNotify=lambda *args: notifications.append(args))
            ),
        )
        register_document_types()
        assert all(rights == access for rights in accesses)
        for path, name, value in registration_entries():
            assert read(path, name) == value
        assert read(r"Software\Classes\.mdss", "") == "Existing.Session.Handler"
        assert read(r"Software\Classes\.mdsg", "") == DOCUMENT_TYPES[".mdsg"][0]
        assert len(notifications) == 1
        register_document_types()
        assert len(notifications) == 1
        # An older installation must leave a newer installation's registration.
        original_executable = sys.executable
        monkeypatch.setattr(sys, "executable", str(Path(original_executable).parent / "other-install" / "MDS.exe"))
        unregister_document_types()
        monkeypatch.setattr(sys, "executable", original_executable)
        assert read(r"Software\Classes\.mdsg", "") == DOCUMENT_TYPES[".mdsg"][0]
        unregister_document_types()
        for path, name, _value in registration_entries():
            with pytest.raises(FileNotFoundError):
                read(path, name)
        assert read(r"Software\Classes\.mdss", "") == "Existing.Session.Handler"
        assert len(notifications) == 2
        unregister_document_types()
        assert len(notifications) == 2
    finally:
        # Only keys underneath this test's randomly generated prefix are removed.
        for path in sorted(created, key=lambda item: item.count("\\"), reverse=True):
            assert path == prefix or path.startswith(prefix + "\\")
            with suppress(FileNotFoundError):
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


def test_default_apps_settings_launch_is_independent(monkeypatch):
    from materials_data_studio.integrations import windows_files

    launched = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(windows_files.os, "startfile", launched.append, raising=False)
    windows_files.open_default_apps_settings()
    assert launched == ["ms-settings:defaultapps"]
