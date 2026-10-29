"""Windows AppContainer broker: no network capabilities, private runtime, bounded job.

Only this trusted broker has host privileges. User Python runs in a different
OS security token. Never fall back to an ordinary process if isolation fails.
Microsoft: learn.microsoft.com/windows/win32/secauthz/implementing-an-appcontainer
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from ctypes import wintypes as w
from pathlib import Path
from typing import Any

from materials_data_studio.version import __version__


def output_bytes(directory: Path) -> int:
    """Count extant regular files without racing short-lived font/cache locks.

    Ignore only deletion between enumeration and stat; permission/I/O errors
    still fail closed. The broker's existing total-byte limit is unchanged.
    """
    total = 0
    for path in directory.rglob("*"):
        try:
            info = path.stat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total


class SandboxUnavailable(RuntimeError):
    pass


def canonical_sandbox_root(root: Path) -> Path:
    """Expand ordinary Windows short names without accepting redirected roots."""
    absolute = root.absolute()
    # Check before resolving: genuine junction/symlink components must still
    # fail closed. Path.resolve also expands harmless 8.3 names (RUNNER~1),
    # which must not be mistaken for an output-directory redirection later.
    if any(path.is_symlink() or path.is_junction() for path in (absolute, *absolute.parents)):
        raise SandboxUnavailable("Sandbox root must not use a link/junction")
    return absolute.resolve(strict=True)


def api(dll: Any, name: str, result: Any, *args: Any) -> Any:
    function = getattr(dll, name)
    function.restype, function.argtypes = result, list(args)
    return function


def token_policy() -> dict[str, int]:
    if sys.platform != "win32":
        raise SandboxUnavailable("Python procedures require Windows AppContainer isolation; execution is disabled on this platform.")
    kernel, security = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("advapi32", use_last_error=True)
    process = api(kernel, "GetCurrentProcess", w.HANDLE)()
    token = w.HANDLE()
    if not api(security, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE))(process, 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        policy = {}
        for key, information in (("appcontainer", 29), ("capabilities", 30), ("lpac", 46)):
            data, size = ctypes.create_string_buffer(4096), w.DWORD()
            if not api(security, "GetTokenInformation", w.BOOL, w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD))(token, information, data, len(data) if information == 30 else 4, ctypes.byref(size)):
                if information == 46 and ctypes.get_last_error() == 87:
                    policy[key] = -1  # optional diagnostic, not an isolation prerequisite
                    continue
                raise ctypes.WinError(ctypes.get_last_error())
            policy[key] = ctypes.cast(data, ctypes.POINTER(w.DWORD)).contents.value
        return policy
    finally:
        api(kernel, "CloseHandle", w.BOOL, w.HANDLE)(token)


def require_isolation() -> None:
    policy = token_policy()
    if policy["appcontainer"] != 1 or policy["capabilities"] != 0:
        raise SandboxUnavailable("Refusing Python execution: a no-capability Windows AppContainer token is required.")


def verify_denials(source_path: Path) -> None:
    """Native preflight, before any user code: do not rely on Python guards."""
    settings = json.loads((source_path.parent / "constraints.json").read_text(encoding="utf-8"))
    try:
        with open(settings["sentinel"], "rb"):
            pass
    except PermissionError:
        pass
    else:
        raise SandboxUnavailable("Host-file isolation preflight failed; procedure not executed.")
    with socket.socket() as channel:
        channel.settimeout(0.2)
        if channel.connect_ex(("127.0.0.1", settings["port"])) == 0:
            raise SandboxUnavailable("Network isolation preflight failed; procedure not executed.")


def runtime_identity(origin: Path, source_hash: str) -> str:
    """Immutable snapshots must not mix Python ABIs, environments or source revisions."""
    identity = [str(origin), __version__, source_hash, sys.version, str(Path(sys.executable).resolve()),
                str(Path(sys.prefix).resolve()), str(Path(sys.base_prefix).resolve()), "lpac-runtime-5"]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:16]


def runtime_directory() -> tuple[Path, Path]:
    """Prepare a private copy; never grant a sandbox access to the user's repo."""
    frozen = bool(getattr(sys, "frozen", False))
    origin = Path(sys.executable).parent if frozen else Path(__file__).parents[2]
    fingerprint = hashlib.sha256()
    if frozen:
        fingerprint.update(Path(sys.executable).read_bytes())
    else:
        for path in sorted(Path(__file__).parents[1].rglob("*.py")):
            fingerprint.update(path.read_bytes())
    identity = runtime_identity(origin, fingerprint.hexdigest())
    root = Path(tempfile.gettempdir()) / ("mds-isolated-runtime-" + identity)
    executable = root / ("MaterialsDataStudio.exe" if frozen else "python.exe")
    if (root / "ready.json").exists() and executable.exists():
        return root, executable
    staging = Path(tempfile.mkdtemp(prefix="mds-runtime-build-"))
    try:
        if frozen:
            shutil.copy2(sys.executable, staging / executable.name)
            shutil.copytree(Path(sys._MEIPASS), staging / "_internal", ignore=shutil.ignore_patterns("__pycache__"))  # type: ignore[attr-defined]
        else:
            base = Path(sys.base_prefix)
            for file in base.glob("*.dll"):
                shutil.copy2(file, staging / file.name)
            shutil.copy2(base / "python.exe", staging / "python.exe")
            shutil.copytree(base / "DLLs", staging / "DLLs")
            shutil.copytree(base / "Lib", staging / "Lib", ignore=shutil.ignore_patterns("site-packages", "test", "tests", "__pycache__", "ensurepip", "idlelib", "tkinter", "turtledemo"))
            packages = Path(sys.prefix) / "Lib/site-packages"
            target = staging / "Lib/site-packages"
            target.mkdir(exist_ok=True)
            # Bootstrap dependency closure, not the user's import allowlist.
            names = ("numpy", "numpy.libs", "scipy", "scipy.libs", "PySide6", "shiboken6", "matplotlib", "matplotlib.libs", "mpl_toolkits", "fontTools", "PIL", "pillow.libs", "kiwisolver", "contourpy", "cycler", "dateutil", "pyparsing", "packaging", "six.py")
            for name in names:
                source = packages / name
                if source.is_dir():
                    shutil.copytree(source, target / name, ignore=shutil.ignore_patterns("__pycache__", "tests", "test"))
                elif source.is_file():
                    shutil.copy2(source, target / name)
            shutil.copytree(Path(__file__).parents[1], staging / "src/materials_data_studio", ignore=shutil.ignore_patterns("__pycache__"))
            (staging / "bootstrap.py").write_text(bootstrap_source(), encoding="utf-8")
        (staging / "ready.json").write_text(json.dumps({"version": __version__, "source": fingerprint.hexdigest()}), encoding="utf-8")
        # Windows cloud-sync/AV can briefly retain a freshly copied directory.
        # Retry this exact owned rename only; never weaken isolation or replace
        # an existing cache. Concurrent preparation may win the same rename.
        for attempt in range(40):
            try:
                staging.rename(root)
                break
            except FileExistsError:
                if not (root / "ready.json").exists():
                    raise SandboxUnavailable("An incomplete isolated runtime exists; restart after removing only that runtime cache.") from None
                break
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.25)
        return root, executable
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def permission(path: Path, sid: str, right: str) -> None:
    result = subprocess.run(["icacls.exe", str(path), "/grant", f"*{sid}:(OI)(CI){right}", "/Q"], capture_output=True, check=False, creationflags=0x08000000)
    if result.returncode:
        raise SandboxUnavailable(f"Could not set the sandbox's private directory permissions (code {result.returncode}).")


def bootstrap_source() -> str:
    return '''import sys
from pathlib import Path
output = Path(sys.argv[-1])
try:
    (output.parent / "bootstrap-started").write_text("started")
    sys.path.insert(0,str(Path(__file__).parent/"src"))
    from materials_data_studio.tools.procedure_worker import main
    result = main()
except BaseException:
    import traceback
    output.with_suffix(".bootstrap.txt").write_text(traceback.format_exc(), encoding="utf-8")
    result = 1
raise SystemExit(result)
'''


def cleanup_profile(root: Path) -> None:
    """Parent also calls this after killing the broker; paths come from its record."""
    record = root / "sandbox.json"
    if not record.exists() or sys.platform != "win32":
        return
    details = json.loads(record.read_text(encoding="utf-8"))
    name, sid = details["profile"], details["sid"]
    runtime = Path(details["runtime"]) if details.get("runtime") else None
    if not re.fullmatch(r"MDS\.Procedure\.[0-9a-f]{32}", name) or not re.fullmatch(r"S-1-15-2-(?:\d+-){6}\d+", sid) or (runtime is not None and (runtime.parent != Path(tempfile.gettempdir()) or not runtime.name.startswith("mds-isolated-runtime-"))):
        raise SandboxUnavailable("Invalid sandbox cleanup record")
    if runtime is not None:
        subprocess.run(["icacls.exe", str(runtime), "/remove:g", "*" + sid, "/Q"], capture_output=True, check=False, creationflags=0x08000000)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    result = api(userenv, "DeleteAppContainerProfile", ctypes.c_long, w.LPCWSTR)(name)
    if result not in (0, -2147024894):
        raise SandboxUnavailable(f"Could not clean the sandbox profile ({result:#x})")
    record.unlink(missing_ok=True)


class StartupInfo(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR), *[(n, w.DWORD) for n in ("x", "y", "xsize", "ysize", "xchars", "ychars", "fill", "flags")], ("show", w.WORD), ("reserved2size", w.WORD), ("reserved2", w.LPVOID), ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]


class StartupEx(ctypes.Structure):
    _fields_ = [("info", StartupInfo), ("attributes", w.LPVOID)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


class Capabilities(ctypes.Structure):
    _fields_ = [("sid", w.LPVOID), ("capabilities", w.LPVOID), ("count", w.DWORD), ("reserved", w.DWORD)]


class BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong), ("flags", w.DWORD), ("min_working_set", ctypes.c_size_t), ("max_working_set", ctypes.c_size_t), ("process_count", w.DWORD), ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


def launch(root: Path, *, probe: bool = False) -> int:
    if sys.platform != "win32":
        raise SandboxUnavailable("Windows AppContainer is required; no unrestricted fallback is provided.")
    root = canonical_sandbox_root(root)
    from materials_data_studio.tools.procedure_interaction import (
        BrokerInteractionClock,
        prepare_interaction,
    )
    prepare_interaction(root)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    name = "MDS.Procedure." + uuid.uuid4().hex
    sid = w.LPVOID()
    result = api(userenv, "CreateAppContainerProfile", ctypes.c_long, w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, w.LPVOID, w.DWORD, ctypes.POINTER(w.LPVOID))(name, "MDS isolated Python", "No network capabilities", None, 0, ctypes.byref(sid))
    if result != 0:
        raise SandboxUnavailable(f"Windows refused to create an isolated profile ({result:#x}); Python was not executed.")
    attributes = None
    process, job, thread = None, None, None
    interaction_clock = None
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    (root / "private-sentinel").write_text("MDS synthetic isolation sentinel", encoding="utf-8")
    (root / "input/constraints.json").write_text(json.dumps({"sentinel": str(root / "private-sentinel"), "port": listener.getsockname()[1]}), encoding="utf-8")
    try:
        text_sid = w.LPWSTR()
        if not api(security, "ConvertSidToStringSidW", w.BOOL, w.LPVOID, ctypes.POINTER(w.LPWSTR))(sid, ctypes.byref(text_sid)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid_string = text_sid.value
        api(kernel, "LocalFree", w.LPVOID, w.LPVOID)(text_sid)
        details = {"profile": name, "sid": sid_string, "runtime": None}
        (root / "sandbox.json").write_text(json.dumps(details), encoding="utf-8")
        print("Preparing isolated scientific runtime (first run may take longer)...", flush=True)
        runtime, executable = runtime_directory()
        details["runtime"] = str(runtime)
        (root / "sandbox.json").write_text(json.dumps(details), encoding="utf-8")
        assert sid_string is not None
        permission(runtime, sid_string, "RX")
        permission(root / "input", sid_string, "RX")
        permission(root / "output", sid_string, "M")
        label = subprocess.run(["icacls.exe", str(root / "output"), "/setintegritylevel", "(OI)(CI)L", "/Q"], capture_output=True, check=False, creationflags=0x08000000)
        if label.returncode:
            raise SandboxUnavailable("Could not label sandbox output as low integrity")
        size = ctypes.c_size_t()
        initialize = api(kernel, "InitializeProcThreadAttributeList", w.BOOL, w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t))
        initialize(None, 2, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        if not initialize(attributes, 2, 0, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        update = api(kernel, "UpdateProcThreadAttribute", w.BOOL, w.LPVOID, w.DWORD, ctypes.c_size_t, w.LPVOID, ctypes.c_size_t, w.LPVOID, w.LPVOID)
        capabilities, lpac = Capabilities(sid, None, 0, 0), w.DWORD(0)
        for key, value in ((0x20009, capabilities), (0x2000F, lpac)):
            if not update(attributes, 0, key, ctypes.byref(value), ctypes.sizeof(value), None, None):
                raise ctypes.WinError(ctypes.get_last_error())
        startup, information = StartupEx(), ProcessInfo()
        startup.info.cb, startup.attributes = ctypes.sizeof(StartupEx), ctypes.cast(attributes, w.LPVOID)
        # Do not mutate a shared window-station/desktop ACL. The trusted
        # bootstrap initializes DLLs; all UI restrictions precede user code.
        args = [str(executable)]
        if not getattr(sys, "frozen", False):
            args += ["-I", "-B", str(runtime / "bootstrap.py")]
        args += ["--sandbox-probe" if probe else "--procedure-worker", str(root / "input/input.mdsg"), str(root / "input/procedure.py"), str(root / "output/output.mdsg")]
        # No credentials, proxies, PYTHONPATH or host shell environment inheritance.
        environment = {"SystemRoot": os.environ.get("SYSTEMROOT", r"C:\Windows"), "TEMP": str(root / "output"), "TMP": str(root / "output"), "PATH": str(runtime), "MPLCONFIGDIR": str(root / "output/mpl"), "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
        environment.update({"USERPROFILE": str(root / "output"), "LOCALAPPDATA": str(root / "output"), "APPDATA": str(root / "output"), "SystemDrive": Path(environment["SystemRoot"]).drive, "windir": environment["SystemRoot"], "ComSpec": str(Path(environment["SystemRoot"]) / "System32/cmd.exe")})
        for variable in ("USERPROFILE", "LOCALAPPDATA", "APPDATA"):
            if variable in os.environ:
                environment[variable] = os.environ[variable]
        env = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(environment.items())) + "\0\0")
        job = api(kernel, "CreateJobObjectW", w.HANDLE, w.LPVOID, w.LPCWSTR)(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags, limits.basic.process_count = 0x2000 | 0x100 | 0x8 | 0x2, 1
        limits.basic.process_time, limits.process_memory = 120 * 10_000_000, 1024**3
        set_job = api(kernel, "SetInformationJobObject", w.BOOL, w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD)
        # Establish the complete UI restrictions after trusted DLL bootstrap,
        # before allowing user Python to run. No shared desktop ACL is modified.
        ui = w.DWORD(0)
        if not set_job(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not set_job(job, 4, ctypes.byref(ui), ctypes.sizeof(ui)):
            raise ctypes.WinError(ctypes.get_last_error())
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
        if not api(kernel, "CreateProcessW", w.BOOL, w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD, w.LPVOID, w.LPCWSTR, ctypes.POINTER(StartupEx), ctypes.POINTER(ProcessInfo))(str(executable), command, None, None, False, 0x80000 | 0x400 | 0x4 | 0x08000000, env, str(root / "output"), ctypes.byref(startup), ctypes.byref(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        process, thread = information.process, information.thread
        if not api(kernel, "AssignProcessToJobObject", w.BOOL, w.HANDLE, w.HANDLE)(job, process):
            api(kernel, "TerminateProcess", w.BOOL, w.HANDLE, w.UINT)(process, 1)
            raise ctypes.WinError(ctypes.get_last_error())
        if api(kernel, "ResumeThread", w.DWORD, w.HANDLE)(thread) == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        print("Windows AppContainer active: network disabled; child processes disabled; 1 GiB / 180 s computation limit (interactive dialogs: at most 5 minutes each, 1 hour overall).", flush=True)
        wait = api(kernel, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
        started = time.monotonic()
        interaction_clock = BrokerInteractionClock(root)
        confined = False
        while wait(process, 100) == 258:
            if not confined and (root / "output/ready").exists():
                ui = w.DWORD(0xFF)
                if not set_job(job, 4, ctypes.byref(ui), ctypes.sizeof(ui)):
                    api(kernel, "TerminateJobObject", w.BOOL, w.HANDLE, w.UINT)(job, 1)
                    raise SandboxUnavailable("Could not enable the final GUI/clipboard restrictions")
                (root / "input/permit").write_text("isolated", encoding="utf-8")
                confined = True
            output_size = output_bytes(root / "output")
            now = time.monotonic()
            try:
                waiting = interaction_clock.poll(now)
            except (ValueError, TimeoutError, OSError) as exc:
                api(kernel, "TerminateJobObject", w.BOOL, w.HANDLE, w.UINT)(job, 1)
                raise SandboxUnavailable(f"Interactive sandbox protocol failed: {exc}") from exc
            if now - started - waiting > 180 or now - started > 3600 or output_size > 256 * 1024**2:
                api(kernel, "TerminateJobObject", w.BOOL, w.HANDLE, w.UINT)(job, 1)
                raise SandboxUnavailable("Sandbox time/output size limit reached; document results discarded.")
        code = w.DWORD()
        if not api(kernel, "GetExitCodeProcess", w.BOOL, w.HANDLE, ctypes.POINTER(w.DWORD))(process, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if code.value == 0 and not confined:
            raise SandboxUnavailable("Worker exited without final confinement")
        listener.settimeout(0.1)
        try:
            connection, _ = listener.accept()
        except TimeoutError:
            pass
        else:
            connection.close()
            raise SandboxUnavailable("Unexpected sandbox network connection; results discarded")
        if (root / "output").resolve() != root / "output":
            raise SandboxUnavailable("Sandbox output must not be a link/junction")
        result_path = root / "output/output.mdsg"
        if result_path.exists() and result_path.resolve().parent != root / "output":
            raise SandboxUnavailable("Sandbox result must not be an external link")
        return int(code.value)
    finally:
        if interaction_clock is not None:
            interaction_clock.close()
        listener.close()
        for handle in (thread, process, job):
            if handle:
                api(kernel, "CloseHandle", w.BOOL, w.HANDLE)(handle)
        if attributes is not None:
            api(kernel, "DeleteProcThreadAttributeList", None, w.LPVOID)(attributes)
        api(security, "FreeSid", w.LPVOID, w.LPVOID)(sid)
        if (root / "sandbox.json").exists():
            cleanup_profile(root)
        else:
            api(userenv, "DeleteAppContainerProfile", ctypes.c_long, w.LPCWSTR)(name)


def main() -> int:
    try:
        return launch(Path(sys.argv[-1]), probe="--sandbox-probe" in sys.argv)
    except Exception as exc:
        print(f"Sandbox error: {exc}", flush=True)
        return 1
