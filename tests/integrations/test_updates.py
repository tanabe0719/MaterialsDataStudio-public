"""Full-package identity, consent and failure boundaries for installed updates."""
from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from packaging.version import Version

from materials_data_studio.integrations import updates


@pytest.fixture
def plan():
    current = Version(updates.__version__)
    version = f"{current.major}.{current.minor + 1}.0"
    return {"version": version, "package_id": "MaterialsDataStudio", "type": "Full",
            "filename": f"MaterialsDataStudio-{version}-full.nupkg", "sha256": "a" * 64,
            "size": 1024, "notes": "## English\nChanges\n## 日本語\n変更"}


def info_for(plan):
    return SimpleNamespace(IsDowngrade=False, TargetFullRelease=SimpleNamespace(
        Version=plan["version"], PackageId=plan["package_id"], Type=plan["type"],
        FileName=plan["filename"], SHA256=plan["sha256"], Size=plan["size"], NotesMarkdown=plan["notes"]))


@pytest.mark.parametrize(("key", "value"), [
    ("version", "0.1.0"), ("version", "0.16.0-beta"), ("filename", "../another.nupkg"),
    ("package_id", "AnotherApp"), ("type", "Delta"), ("sha256", ""), ("size", -1),
    ("size", True), pytest.param("notes", "x" * 512001, id="oversized-notes"),
])
def test_unexpected_release_never_reaches_download(plan, key, value):
    plan[key] = value
    with pytest.raises(ValueError):
        updates.validate_plan(plan)


def test_source_worker_exits_without_qt_or_network():
    code = ("import sys; from materials_data_studio.bootstrap import main; "
            "sys.argv=['MDS','--mds-update-worker','check']; result=main(); "
            "assert 'PySide6.QtWidgets' not in sys.modules; raise SystemExit(result)")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["value"] == {"supported": False, "plan": None}


def test_installed_manager_uses_only_public_full_windows_feed(monkeypatch):
    calls = []
    manager = SimpleNamespace(get_is_portable=lambda: False, get_app_id=lambda: "MaterialsDataStudio",
                              get_current_version=lambda: updates.__version__)
    sdk = SimpleNamespace(
        HttpOptions=lambda *args: args, HttpSource=lambda *args: args,
        UpdateOptions=lambda *args: args,
        UpdateManager=lambda *args: calls.append(args) or manager,
    )
    monkeypatch.setitem(sys.modules, "velopack", sdk)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert updates.installed_manager() is manager
    assert calls == [((updates.FEED, ([], 30000)), (False, -1, "win"))]


def test_portable_installation_is_not_offered_in_app_updates(monkeypatch):
    manager = SimpleNamespace(get_is_portable=lambda: True)
    sdk = SimpleNamespace(HttpOptions=lambda *_a: None, HttpSource=lambda *_a: None,
                          UpdateOptions=lambda *_a: None, UpdateManager=lambda *_a: manager)
    monkeypatch.setitem(sys.modules, "velopack", sdk)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert updates.check() == {"supported": False, "plan": None}


def test_download_rechecks_pinned_version_and_surfaces_checksum_failure(plan, monkeypatch):
    calls = []
    def fail_download(_info, _callback):
        raise OSError("checksum mismatch")
    manager = SimpleNamespace(check_for_updates=lambda: info_for(plan), download_updates=fail_download)
    monkeypatch.setattr(updates, "installed_manager", lambda base, **options: calls.append((base, options)) or manager)
    with pytest.raises(OSError, match="checksum"):
        updates.download(plan, lambda _p: None)
    assert calls == [(updates.REPOSITORY + "/releases/download/v" + plan["version"], {"downloading": True})]


def test_changed_release_is_rejected_before_download(plan, monkeypatch):
    actual = dict(plan, sha256="b" * 64)
    manager = SimpleNamespace(check_for_updates=lambda: info_for(actual),
                              download_updates=lambda *_a: pytest.fail("Must not download"))
    monkeypatch.setattr(updates, "installed_manager", lambda *_a, **_k: manager)
    with pytest.raises(ValueError, match="changed"):
        updates.download(plan, lambda _p: None)


def test_restart_never_uses_a_different_pending_version(plan, monkeypatch):
    manager = SimpleNamespace(get_update_pending_restart=lambda: SimpleNamespace(Version="999.0.0", PackageId="MaterialsDataStudio"),
                              wait_exit_then_apply_updates=lambda *_a: pytest.fail("Must not apply"))
    monkeypatch.setattr(updates, "installed_manager", lambda *_a, **_k: manager)
    with pytest.raises(ValueError, match="not ready"):
        updates.arm_restart(plan, [])


def test_restart_arms_graceful_exit_not_immediate_termination(plan, monkeypatch):
    pending = SimpleNamespace(Version=plan["version"], PackageId=plan["package_id"])
    calls = []
    manager = SimpleNamespace(get_update_pending_restart=lambda: pending,
                              wait_exit_then_apply_updates=lambda *args: calls.append(args))
    monkeypatch.setattr(updates, "installed_manager", lambda *_a, **_k: manager)
    updates.arm_restart(plan, [])
    assert calls == [(pending, False, True, [])]
