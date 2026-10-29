"""Per-user Windows Open With / Default Apps registration for MDS documents."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

APP_REGISTRATION = r"Software\MaterialsDataStudio\Capabilities"
DOCUMENT_TYPES = {
    ".mdss": ("MaterialsDataStudio.Session", "Materials Data Studio Session", "mdss.ico"),
    ".mdsg": ("MaterialsDataStudio.Graph", "Materials Data Studio Graph", "mdsg.ico"),
}


def registration_entries() -> list[tuple[str, str, str]]:
    """Build auditable registry values without changing Windows preferences."""
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.is_file():
            command = [str(pythonw)]
        command += ["-m", "materials_data_studio"]
    # Force quotes around the placeholder even though list2cmdline would not:
    # Explorer replaces it with paths that may contain spaces/non-ASCII text.
    open_command = subprocess.list2cmdline(command) + ' "%1"'
    entries = [
        (APP_REGISTRATION, "ApplicationName", "Materials Data Studio"),
        (
            APP_REGISTRATION,
            "ApplicationDescription",
            "Plot and analyze materials measurement data and graph documents.",
        ),
        (r"Software\RegisteredApplications", "Materials Data Studio", APP_REGISTRATION),
    ]
    for extension, (progid, description, icon) in DOCUMENT_TYPES.items():
        classes = rf"Software\Classes\{progid}"
        entries += [
            (classes, "", description),
            (classes + r"\DefaultIcon", "", f'"{resource_root / "resources" / "icons" / icon}",0'),
            (classes + r"\shell\open\command", "", open_command),
            (rf"Software\Classes\{extension}\OpenWithProgids", progid, ""),
            (APP_REGISTRATION + r"\FileAssociations", extension, progid),
        ]
    return entries


def register_document_types() -> None:
    """Offer MDS in Windows settings, without replacing a UserChoice default."""
    if sys.platform != "win32":
        return
    import winreg

    # KEY_WRITE omits KEY_QUERY_VALUE: QueryValueEx on such a handle raises
    # WinError 5 even for a key owned by the current user. Request the two
    # specific rights used below, without broader registry/admin access.
    access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
    changed = False
    for path, name, value in registration_entries():
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, access) as key:
            try:
                existing, _kind = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                existing = None
            if existing != value:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
                changed = True
    for extension, (progid, _description, _icon) in DOCUMENT_TYPES.items():
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\{extension}",
            0,
            access,
        ) as key:
            try:
                existing, _kind = winreg.QueryValueEx(key, "")
            except FileNotFoundError:
                existing = ""
            if not existing:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, progid)
                changed = True
    # SHCNE_ASSOCCHANGED / SHCNF_IDLIST: invalidate Explorer's icon cache.
    if changed:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)


def open_default_apps_settings() -> None:
    if sys.platform == "win32":
        os.startfile("ms-settings:defaultapps")


def unregister_document_types() -> None:
    """Remove only registrations still owned by this executable on uninstall."""
    if sys.platform != "win32":
        return
    import winreg

    entries = registration_entries()
    expected_command = next(
        value for path, _name, value in entries if path.endswith(r"\shell\open\command")
    )
    access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
    owned: set[str] = set()
    for _extension, (progid, _description, _icon) in DOCUMENT_TYPES.items():
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                rf"Software\Classes\{progid}\shell\open\command",
                0,
                winreg.KEY_QUERY_VALUE,
            ) as key:
                command = winreg.QueryValueEx(key, "")[0]
            if command == expected_command:
                owned.add(progid)
        except FileNotFoundError:
            continue
    if not owned:
        return
    # Delete known values only when they still match. Never remove whole
    # registry trees or protected UserChoice keys, including another handler.
    removals = [
        (path, name, value)
        for path, name, value in entries
        if any(progid in path or progid in (value, name) for progid in owned)
    ]
    for extension, (progid, _description, _icon) in DOCUMENT_TYPES.items():
        if progid in owned:
            removals.append((rf"Software\Classes\{extension}", "", progid))
    # The shared application entry is ours only if both document handlers are.
    if len(owned) == len(DOCUMENT_TYPES):
        removals.extend(
            (path, name, value)
            for path, name, value in entries
            if path == APP_REGISTRATION or path == r"Software\RegisteredApplications"
        )
    for path, name, value in removals:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, access) as key:
                if winreg.QueryValueEx(key, name)[0] == value:
                    winreg.DeleteValue(key, name)
        except FileNotFoundError:
            continue
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
