"""Isolated scientific Python; no direct/unrestricted execution path."""
from __future__ import annotations

import json
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Protocol

from materials_data_studio.tools.procedure_policy import BoundedOutput
from materials_data_studio.tools.procedure_sandbox import (
    require_isolation,
    token_policy,
    verify_denials,
)


class ProcedureEngine(Protocol):
    def start(self, source: str) -> None: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...


def await_confinement(source_path: Path, output_path: Path) -> None:
    (output_path.parent / "ready").write_text("initialized", encoding="utf-8")
    deadline = time.monotonic() + 30
    while not (source_path.parent / "permit").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("Sandbox broker did not enable the final execution restrictions")
        time.sleep(0.02)


def run(input_path: Path, source_path: Path, output_path: Path) -> int:
    interaction = None
    try:
        require_isolation()
        from materials_data_studio.tools.command_services import DocumentServices
        from materials_data_studio.tools.graph_document import (
            load_graph_document,
            save_graph_document,
        )
        from materials_data_studio.tools.procedure_api import install_api
        from materials_data_studio.tools.procedure_policy import ExecutionGuard, namespace, validate
        document = load_graph_document(input_path)
        from materials_data_studio.tools.igor_input_resources import read_input_resources
        from materials_data_studio.tools.igor_procedure_runtime import IgorAPI
        from materials_data_studio.tools.igor_virtual_io import INPUT_RESOURCES_KEY
        # GUI-selected text is a per-run capability, never persisted in MDSG.
        document.current.environment.pop(INPUT_RESOURCES_KEY, None)
        inputs_path = source_path.parent / "igor_inputs.json"
        if inputs_path.exists():
            document.current.environment[INPUT_RESOURCES_KEY] = read_input_resources(inputs_path)
        context_path = source_path.with_suffix(".json")
        context = json.loads(context_path.read_text(encoding="utf-8")) if context_path.exists() else {}
        interactive = context.pop("igor_interactive", False) is True
        services = DocumentServices(document, context)
        mds = install_api(services)
        igor = mds.igor  # type: ignore[attr-defined]
        # Canonical Label/ModifyGraph formatting lazily imports Matplotlib's
        # text module, whose first font discovery reads Windows font metadata.
        # Initialize trusted dependency state before activating the unchanged
        # audit policy; never permit user registry access or host-font I/O.
        from scipy.signal import upfirdn  # noqa: F401
        from scipy.signal.windows import get_window  # noqa: F401
        from scipy.stats import t  # noqa: F401

        # Trusted scientific kernels are initialized before the unchanged I/O
        # guard. They grant no extra imports/files/network to user procedures.
        from materials_data_studio.tools import (  # noqa: F401
            graph_render,
            igor_box,
            igor_canvas,
            igor_discovery_analysis,
            igor_graph_extensions,
            igor_trace_channels,
            igor_violin,
            igor_wave_ops,
        )
        code = source_path.read_text(encoding="utf-8")
        tree = validate(code)
        scope, guard = namespace(mds), ExecutionGuard()
        from materials_data_studio.tools.procedure_interaction import WorkerInteraction
        # Fixed handles are opened by trusted bootstrap only. ExecutionGuard
        # stays enabled during rendezvous; user Python gets no file handles.
        if interactive:
            interaction = WorkerInteraction(source_path.parent.parent)
            igor._interaction = interaction.request
        await_confinement(source_path, output_path)
        verify_denials(source_path)
        try:
            guard.active = True
            exec(compile(tree, "MDS isolated Python Procedure", "exec"), scope)
            # Call trusted implementation, not a public method that user code
            # could replace; retain the guard and original facade identity.
            IgorAPI.finish(igor)
        finally:
            guard.active = False
        save_graph_document(output_path, document)
        output_path.with_suffix(".json").write_text(json.dumps(services.context), encoding="utf-8")
        return 0
    except BaseException:
        traceback.print_exc()
        return 1
    finally:
        if interaction is not None:
            interaction.close()


def main() -> int:
    input_path, source_path, output_path = (Path(value) for value in sys.argv[-3:])
    # Windowed frozen executables have no Python stdout. A UTF-8 log also
    # guarantees diagnostics reach the editor and can stream before completion.
    if "--sandbox-probe" in sys.argv:
        require_isolation()
        await_confinement(source_path, output_path)
        import socket
        report: dict[str, object] = {"policy": token_policy()}
        paths = json.loads(source_path.with_suffix(".json").read_text(encoding="utf-8"))
        for key in ("host_read", "host_write", "runtime_write"):
            try:
                with open(paths[key], "r" if key == "host_read" else "w"):
                    pass
                report[key] = "ALLOWED"
            except OSError as exc:
                report[key] = {"errno": exc.errno, "winerror": exc.winerror}
        for family, address in ((socket.AF_INET, ("127.0.0.1", paths["port"])), (socket.AF_INET6, ("::1", paths["port"]))):
            with socket.socket(family) as channel:
                channel.settimeout(1)
                report["network" + str(family)] = channel.connect_ex(address)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
            try:
                report["udp"] = channel.sendto(b"MDS synthetic sandbox probe", ("127.0.0.1", paths["port"]))
            except OSError as exc:
                report["udp"] = {"errno": exc.errno, "winerror": exc.winerror}
        import ctypes
        from ctypes import wintypes as w
        user = ctypes.WinDLL("user32", use_last_error=True)
        from materials_data_studio.tools.procedure_sandbox import api
        report["clipboard"] = bool(api(user, "OpenClipboard", w.BOOL, w.HWND)(None))
        if report["clipboard"]:
            api(user, "CloseClipboard", w.BOOL)()
        try:
            import subprocess
            subprocess.run([sys.executable, "-c", "pass"], check=True)
            report["spawn"] = "ALLOWED"
        except OSError as exc:
            report["spawn"] = exc.winerror
        output_path.with_suffix(".json").write_text(json.dumps(report), encoding="utf-8")
        return 0
    with (output_path.parent / "procedure.log").open("w", encoding="utf-8", buffering=1) as stream, redirect_stdout(BoundedOutput(stream)), redirect_stderr(BoundedOutput(stream)):
        return run(input_path, source_path, output_path)
