"""Read-only process identity queries; never send a signal on Windows."""

from __future__ import annotations

import os


def _windows_process(process_id: int) -> tuple[bool, float | None]:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, process_id)
    if not handle:
        # Access denied is not evidence of an abandoned workspace.
        return ctypes.get_last_error() == 5, None
    try:
        running = kernel.WaitForSingleObject(handle, 0) != 0
        times = [wintypes.FILETIME() for _ in range(4)]
        created = None
        if kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            created = ticks / 10_000_000 - 11_644_473_600
        return running, created
    finally:
        kernel.CloseHandle(handle)


def process_is_running(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if os.name == "nt":
        return _windows_process(process_id)[0]
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_started_at(process_id: int) -> float | None:
    return _windows_process(process_id)[1] if os.name == "nt" else None
