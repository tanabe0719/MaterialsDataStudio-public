"""Exercise real offline Setup upgrades in a disposable, uniquely named installation.

The actual MDS payload is used. Silent setup runs SDK hooks without starting
the GUI, so document registration (a normal GUI-startup action) is not invoked.
The uninstall hook must find no owned production handler; guards check that
all existing MDS registration values remain identical throughout the test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import load_session, save_session
from materials_data_studio.integrations.windows_files import DOCUMENT_TYPES, registration_entries
from materials_data_studio.version import __version__

ROOT = Path(__file__).resolve().parents[1]


def registry_snapshot():
    import winreg
    entries = registration_entries() + [(rf"Software\Classes\{extension}", "", "") for extension in DOCUMENT_TYPES]
    snapshot = {}
    for path, name, _value in entries:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_QUERY_VALUE) as key:
                snapshot[path, name] = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            snapshot[path, name] = None
    return snapshot


def metadata(path: Path) -> dict[str, str]:
    return {element.tag.split("}")[-1]: element.text or "" for element in ET.parse(path).iter()}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-setup", type=Path, help="Verify replacement of an older payload with the same public version")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise RuntimeError("Native Windows test required")
    config = json.loads((ROOT / "packaging/velopack.json").read_text(encoding="utf-8"))
    identifier = "MaterialsDataStudioValidation." + uuid.uuid4().hex
    title = "MDS Installer Validation " + identifier.split(".")[-1]
    work = (ROOT / ".test-tmp" / identifier).resolve()
    target = work / "installed"
    assert work.is_relative_to(ROOT / ".test-tmp") and not target.exists()
    work.mkdir(parents=True)
    source = ROOT / ("dist_v" + __version__.replace(".", "")) / "MaterialsDataStudio"
    payload = work / "payload"
    shutil.copytree(source, payload)
    dll = ROOT / f".tools/velopack-{config['toolVersion']}/tools/net8.0/any/vpk.dll"
    before = registry_snapshot()
    # Abort before any install if this freshly generated path somehow owns a
    # production handler. No installer run or registry restoration is attempted.
    assert not any(str(target).casefold() in str(value).casefold() for value in before.values())
    state_dir = work / "persistent-state"
    state_dir.mkdir()
    session = state_dir / "saved-work.mdss"
    save_session(ProjectState([Dataset("Retained", ModuleType.XRD, {"x": np.arange(3.), "intensity": np.ones(3)})]), session)
    database = state_dir / "reference.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sentinel(value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES('retained')")
    settings = state_dir / "preferences.ini"
    settings.write_text("[installer-validation]\nsentinel=retained\n", encoding="utf-8")
    protected = {path: sha(path) for path in (session, database, settings)}
    environment = {**os.environ, "MDS_SETTINGS_DIRECTORY": str(state_dir / "smoke-settings"),
                   "MDS_RECOVERY_DIRECTORY": str(state_dir / "smoke-recovery"),
                   "TEMP": str(work), "TMP": str(work), "QT_QPA_PLATFORM": "offscreen"}

    def unchanged() -> None:
        assert registry_snapshot() == before, "Existing MDS registrations changed"
        for path, digest in protected.items():
            assert sha(path) == digest, f"Persistent data changed: {path.name}"

    def run(command: list[str], label: str, timeout: int = 180) -> None:
        with (work / f"{label}.log").open("w", encoding="utf-8") as output:
            completed = subprocess.run(command, env=environment, stdout=output,
                                       stderr=subprocess.STDOUT, timeout=timeout)
        if completed.returncode:
            raise RuntimeError(f"{label}: exit {completed.returncode}; see {work}")

    def pack(version: str, label: str, pack_dir: Path = payload) -> Path:
        directory = work / label
        run(["dotnet", str(dll), "--skip-updates", "--yes", "pack", "--packId", identifier,
             "--packTitle", title, "--packVersion", version, "--packDir", str(pack_dir),
             "--mainExe", config["mainExe"], "--runtime", "win-x64", "--channel", "win",
             "--noPortable", "--delta", "None", "--shortcuts", "StartMenuRoot",
             "--outputDir", str(directory)], label)
        setups = list(directory.glob("*Setup.exe"))
        assert len(setups) == 1
        return setups[0]

    def install(setup: Path, label: str) -> None:
        assert target.resolve().is_relative_to(work)
        run([str(setup), "--silent", "--installto", str(target), "--log", str(work / f"{label}-native.log")], label)
        unchanged()

    def uninstall() -> None:
        updater = target / "Update.exe"
        if not updater.is_file():
            return
        assert target.resolve().is_relative_to(work)
        assert metadata(target / "current/sq.version")["id"] == identifier
        unchanged()  # guarantees the uninstall callback owns no real handler
        run([str(updater), "uninstall", "--silent", "--rootDir", str(target),
             "--log", str(work / "uninstall-native.log")], "uninstall")
        unchanged()

    baseline_version = __version__ if args.previous_setup else __version__ + "-rc.1"
    baseline_payload = payload
    if args.previous_setup:
        baseline_payload = work / "previous-payload"
        baseline_payload.mkdir()
        with zipfile.ZipFile(args.previous_setup) as archive:
            for name in archive.namelist():
                if not name.startswith("lib/app/") or name.endswith("/"):
                    continue
                relative = Path(name.removeprefix("lib/app/"))
                if relative.name in {"sq.version", "Squirrel.exe", "Materials Data Studio_ExecutionStub.exe"}:
                    continue
                destination = (baseline_payload / relative).resolve()
                assert destination.is_relative_to(baseline_payload)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(name))
        assert sha(baseline_payload / config["mainExe"]) != sha(payload / config["mainExe"]), "Previous/current payloads must differ"
    (baseline_payload / "installer-validation.txt").write_text("baseline", encoding="utf-8")
    (baseline_payload / "obsolete-validation.txt").write_text("remove on upgrade", encoding="utf-8")
    baseline_setup = pack(baseline_version, "baseline-package", baseline_payload)
    (payload / "installer-validation.txt").write_text("upgraded", encoding="utf-8")
    (payload / "obsolete-validation.txt").unlink(missing_ok=True)
    current_setup = pack(__version__, "current-package")
    shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs" / (title + ".lnk")
    report = {"version": __version__, "baseline_package_version": baseline_version,
              "test_uses_production_payload": True, "fixture_note": "Same frozen MDS payload in two package versions, plus changed/obsolete marker files",
              "install_scope": "unique test application id and workspace installation directory"}
    if args.previous_setup:
        report.update(previous_setup_sha256=sha(args.previous_setup), previous_executable_sha256=sha(baseline_payload / config["mainExe"]),
                      current_executable_sha256=sha(payload / config["mainExe"]),
                      fixture_note="Actual earlier published payload replaced by the new payload at the same version; isolated application id")
    try:
        install(baseline_setup, "first-install")
        assert metadata(target / "current/sq.version")["version"] == baseline_version
        assert (target / "current/installer-validation.txt").read_text() == "baseline"
        assert sha(target / "current" / config["mainExe"]) == sha(baseline_payload / config["mainExe"])
        assert shortcut.is_file()
        report["first_install"] = "passed"
        install(current_setup, "upgrade")
        assert metadata(target / "current/sq.version")["version"] == __version__
        assert (target / "current/installer-validation.txt").read_text() == "upgraded"
        assert sha(target / "current" / config["mainExe"]) == sha(payload / config["mainExe"])
        assert not (target / "current/obsolete-validation.txt").exists()
        assert shortcut.is_file()
        report["new_setup_upgrades_existing_install"] = "passed"
        install(current_setup, "repeat-install")
        assert metadata(target / "current/sq.version")["version"] == __version__
        report["same_version_reinstall"] = "passed"
        installed_exe = target / "current" / config["mainExe"]
        smoke = work / "installed-smoke.txt"
        run([str(installed_exe), "--packaging-smoke-test", str(smoke)], "installed-smoke", timeout=300)
        assert smoke.read_text(encoding="utf-8").startswith("OK\n")
        np.testing.assert_array_equal(load_session(session).datasets[0].raw_data["x"], np.arange(3.))
        unchanged()
        report["installed_application_smoke"] = "passed"
        report["sessions_preferences_external_database_retained"] = "passed"
    finally:
        uninstall()
    assert not (target / "current" / config["mainExe"]).exists()
    assert not shortcut.exists()
    report["uninstall"] = "passed"
    report["existing_mds_registration_values_unchanged"] = True
    suffix = "_same_version" if args.previous_setup else ""
    output = ROOT / f"docs/installer_validation_v{__version__.replace('.', '')}{suffix}.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
