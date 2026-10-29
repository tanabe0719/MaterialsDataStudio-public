"""Consent-driven, installed-Windows updates from the public full-package feed."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from packaging.version import Version

from materials_data_studio.version import __version__

REPOSITORY = "https://github.com/tanabe0719/MaterialsDataStudio-public"
RELEASES = REPOSITORY + "/releases/latest"
FEED = RELEASES + "/download"
STARTUP_SETTING = "updates/check_at_startup"
MAX_MESSAGE = 2 * 1024 * 1024


def other_instances_running() -> bool:
    """Velopack may terminate other instances; do not arm it while they exist."""
    import ctypes
    import os
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for function in (kernel.Process32FirstW, kernel.Process32NextW):
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while found:
            if entry.pid != os.getpid() and entry.name.casefold() == Path(sys.executable).name.casefold():
                return True
            found = kernel.Process32NextW(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:
            raise ctypes.WinError(ctypes.get_last_error())
        return False
    finally:
        kernel.CloseHandle(handle)


class InstallationRequired(RuntimeError):
    """Source and portable copies must first be installed with Setup."""


def installed_manager(base: str = FEED, *, downloading: bool = False) -> Any:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise InstallationRequired
    import velopack

    try:
        manager = velopack.UpdateManager(
            velopack.HttpSource(base, velopack.HttpOptions([], 1_800_000 if downloading else 30_000)),
            velopack.UpdateOptions(False, -1, "win"),
        )
    except Exception as exc:
        raise InstallationRequired from exc
    if manager.get_is_portable() or manager.get_app_id() != "MaterialsDataStudio":
        raise InstallationRequired
    if Version(manager.get_current_version()) != Version(__version__):
        raise InstallationRequired
    return manager


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    version = plan.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid stable version")
    if Version(version) <= Version(__version__):
        raise ValueError("Update must be newer than the running application")
    if (plan.get("package_id") != "MaterialsDataStudio" or plan.get("type") != "Full"
            or plan.get("filename") != f"MaterialsDataStudio-{version}-full.nupkg"):
        raise ValueError("Unexpected update identity")
    if not isinstance(plan.get("sha256"), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", plan["sha256"]):
        raise ValueError("Missing SHA-256")
    if type(plan.get("size")) is not int or not 0 < plan["size"] < 2 * 1024**3:
        raise ValueError("Invalid package size")
    if not isinstance(plan.get("notes", ""), str) or len(plan.get("notes", "")) > 512_000:
        raise ValueError("Release notes too large")
    return plan


def plan_for(info: Any) -> dict[str, Any]:
    if info.IsDowngrade:
        raise ValueError("Downgrades are not allowed")
    asset = info.TargetFullRelease
    return validate_plan({"version": asset.Version, "package_id": asset.PackageId,
                          "type": asset.Type, "filename": asset.FileName,
                          "sha256": asset.SHA256, "size": asset.Size,
                          "notes": asset.NotesMarkdown or ""})


def check() -> dict[str, Any]:
    try:
        manager = installed_manager()
    except InstallationRequired:
        return {"supported": False, "plan": None}
    info = manager.check_for_updates()
    return {"supported": True, "plan": plan_for(info) if info is not None else None}


def download(plan: dict[str, Any], progress: Any) -> dict[str, Any]:
    validate_plan(plan)
    # Pin downloads to the exact immutable version, even if 'latest' changes.
    manager = installed_manager(REPOSITORY + "/releases/download/v" + plan["version"], downloading=True)
    info = manager.check_for_updates()
    if info is None:
        raise ValueError("Release no longer available")
    actual = plan_for(info)
    for key in ("version", "package_id", "type", "filename", "sha256", "size"):
        if actual[key] != plan[key]:
            raise ValueError("Release changed; check again")
    # Velopack validates the full-package checksum before making it pending.
    manager.download_updates(info, progress)
    return {"ready": True}


def arm_restart(plan: dict[str, Any], restart_args: list[str]) -> None:
    """Only call in the GUI process, after every unsaved-work confirmation."""
    validate_plan(plan)
    manager = installed_manager()
    pending = manager.get_update_pending_restart()
    if pending is None or pending.Version != plan["version"] or pending.PackageId != plan["package_id"]:
        raise ValueError("Downloaded update is not ready")
    # No immediate process exit: Qt can complete its normal clean shutdown first.
    manager.wait_exit_then_apply_updates(pending, False, True, restart_args)


def worker_main(arguments: list[str]) -> int:
    """Bounded JSON protocol; no URLs, paths, credentials or code from the caller."""
    def emit(event: str, value: Any) -> None:
        print(json.dumps({"event": event, "value": value}, ensure_ascii=True), flush=True)
    try:
        if arguments == ["check"]:
            result = check()
        elif arguments == ["download"]:
            raw = sys.stdin.buffer.read(MAX_MESSAGE + 1)
            if len(raw) > MAX_MESSAGE:
                raise ValueError("Request too large")
            plan = json.loads(raw)
            if not isinstance(plan, dict):
                raise ValueError("Invalid request")
            result = download(plan, lambda value: emit("progress", max(0, min(100, int(value)))))
        else:
            raise ValueError("Unknown operation")
        emit("result", result)
        return 0
    except Exception:
        # Avoid exposing signed redirect URLs or local installation paths.
        emit("error", "Update operation failed. Please check your connection and try again.")
        return 1
