"""Installer hooks precede Qt startup; workers retain their isolated entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from materials_data_studio import bootstrap


def test_installed_startup_runs_sdk_before_application_without_update_fetch(monkeypatch):
    events = []
    callbacks = {}

    class App:
        def set_args(self, args):
            assert args == ["example.mdss"]
            return self

        def set_auto_apply_on_startup(self, enabled):
            assert enabled is False
            return self

        def on_before_uninstall_fast_callback(self, callback):
            callbacks["uninstall"] = callback
            return self

        def run(self):
            events.append("sdk")

    def forbidden_manager(*_args, **_kwargs):
        raise AssertionError("Online/in-app update code must not be used")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["MaterialsDataStudio.exe", "example.mdss"])
    monkeypatch.setitem(
        sys.modules, "velopack", SimpleNamespace(App=App, UpdateManager=forbidden_manager)
    )
    monkeypatch.setitem(
        sys.modules,
        "materials_data_studio.app",
        SimpleNamespace(main=lambda: events.append("qt") or 0),
    )
    from materials_data_studio.integrations import windows_files

    monkeypatch.setattr(windows_files, "register_document_types", lambda: events.append("register"))
    monkeypatch.setattr(
        windows_files, "unregister_document_types", lambda: events.append("unregister")
    )
    assert bootstrap.main() == 0
    assert events == ["sdk", "qt"]
    callbacks["uninstall"]("0.14.7")
    assert events == ["sdk", "qt", "unregister"]


def test_non_installed_source_startup_does_not_load_sdk(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "argv", ["materials-data-studio"])
    monkeypatch.setitem(sys.modules, "velopack", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "materials_data_studio.app", SimpleNamespace(main=lambda: 42))
    assert bootstrap.main() == 42


@pytest.mark.skipif(sys.platform != "win32", reason="Velopack SDK is Windows-only")
def test_no_callback_obsolete_hook_exits_before_qt():
    # Official obsolete = before-update; MDS registers no such callback.
    # The uninstall/association-removal hook is deliberately not executed here.
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from materials_data_studio.bootstrap import main; "
            "sys.argv=['MDS','--veloapp-obsolete','0.14.6']; main(); "
            "raise AssertionError('fast hook returned into Qt')",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
