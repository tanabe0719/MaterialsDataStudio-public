"""Local startup milestones, without document names, paths or scientific data."""
from __future__ import annotations

import time

_origin = time.perf_counter()
_marks: dict[str, float] = {}


def process_memory() -> dict[str, int]:
    import sys
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes
    class Memory(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD), *[(name, ctypes.c_size_t) for name in
            ("peak_working_set", "working_set", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]]
    counters = Memory()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = wintypes.HANDLE
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    if not query(process(), ctypes.byref(counters), counters.cb):
        return {}
    return {"rss_bytes": counters.working_set, "peak_rss_bytes": counters.peak_working_set}


def mark(name: str) -> None:
    if name in _marks:
        return
    _marks[name] = time.perf_counter() - _origin
    import logging
    logging.getLogger(__name__).info("Startup phase=%s elapsed_seconds=%.6f memory=%s", name, _marks[name], process_memory())


def snapshot() -> dict:
    return {"marks_seconds": dict(_marks), **process_memory()}
