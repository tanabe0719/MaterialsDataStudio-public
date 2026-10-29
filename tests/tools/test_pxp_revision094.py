"""Synthetic regression fixtures and real OS-boundary probes; no research data."""
from __future__ import annotations

import json
import socket
import sys
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import pytest

from materials_data_studio.tools.command_engine import CommandEngine
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_wave_dialogs import TextImportDialog
from materials_data_studio.tools.graph_waves import (
    column_names,
    detect_start,
    empty_document,
    import_columns,
    text_rows,
)
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.procedure_policy import namespace, validate
from materials_data_studio.tools.procedure_sandbox import launch


@pytest.mark.parametrize("failures", [3, 40])
def test_runtime_copy_retries_transient_directory_lock(monkeypatch, tmp_path, failures):
    from materials_data_studio.tools import procedure_sandbox as sandbox
    executable = tmp_path / "original.exe"
    executable.write_bytes(b"synthetic executable for copy-only test")
    internal = tmp_path / "internal"
    internal.mkdir()
    (internal / "synthetic.txt").write_text("synthetic runtime", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sandbox.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(sandbox.time, "sleep", lambda _seconds: None)
    original_rename = Path.rename
    attempts = []

    def locked_rename(path, target):
        attempts.append(path)
        if len(attempts) <= failures:
            raise PermissionError("synthetic transient directory lock")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", locked_rename)
    if failures == 40:
        with pytest.raises(PermissionError):
            sandbox.runtime_directory()
        assert len(attempts) == 40
        assert not list(tmp_path.glob("mds-isolated-runtime-*"))
        assert executable.exists() and internal.exists()
        return
    runtime, copied_executable = sandbox.runtime_directory()
    assert len(attempts) == 4
    assert copied_executable.read_bytes() == executable.read_bytes()
    assert (runtime / "_internal/synthetic.txt").read_text(encoding="utf-8") == "synthetic runtime"
    assert (runtime / "ready.json").exists()


def test_space_operands_and_liberal_names():
    engine = CommandEngine(DocumentServices(empty_document()))
    for source in ('Make/D/N=5 a b', 'a=p; b=p*2', 'Duplicate a c', "Rename c 'Moment (emu)'", "Display 'Moment (emu)'", "ModifyGraph rgb('Moment (emu)')=(65535,0,0),lsize('Moment (emu)')=2", 'KillWaves b'):
        result = engine.execute(source)
        assert result.success, result.message
    assert engine.services.graph().traces[0].line_width == 2
    np.testing.assert_array_equal(engine.services.wave("Moment (emu)").data, np.arange(5))
    before = engine.services.document.current
    assert not engine.execute("Duplicate a another; Rename missing bad").success
    assert engine.services.document.current is before


def test_command_properties_override_gui_values(qtbot):
    viewer = GraphEditorWindow()
    qtbot.addWidget(viewer)
    command = viewer.command_workspace
    command.execute("Make/D/N=5 y; y=p; Display/N=G y")
    viewer.apply_properties("trace", "y", {"color": "#0000ff", "marker_fill": "#0000ff"})
    viewer.apply_properties("axis", "left", {"font_size": 9, "major_length": 2})
    command.execute('Variable size=18; ModifyGraph fSize=size,btLen=8,rgb(y)=(65535,0,0),mrkFillRGB(y)=(0,65535,0),minor=1,tick=2,mirror=1')
    graph = viewer.editor.document.graph("G")
    assert next(a for a in graph.axes if a.name == "left").style.font_size == 18
    assert viewer.editor.document.current.layout["G"]["view"]["traces"]["y"].get("color") is None
    assert "ValueError" not in command.output.toPlainText()
    command.execute("ModifyGraph hide(y)=1")
    assert not viewer.editor.document.current.layout["G"]["view"]["traces"]["y"]["visible"]
    viewer._discard_approved = True
    viewer.close()


def test_explicit_float_and_dock_buttons(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QToolButton
    viewer = GraphEditorWindow()
    viewer.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(viewer)
    for dock in (viewer.command_workspace.console_dock, viewer.command_workspace.procedure_dock):
        toggle = next(b for b in dock.titleBarWidget().findChildren(QToolButton) if "Separate" in b.text())
        toggle.click()
        assert dock.isFloating() and "Dock back" in toggle.text()
        toggle.click()
        assert not dock.isFloating() and "Separate" in toggle.text()
    viewer._discard_approved = True
    viewer.close()


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_quantum_design_sparse_data_and_header(qtbot, tmp_path, newline):
    headings = ["Comment", "Time Stamp (sec)", "Temperature (K)", "Magnetic Field (Oe)", "DC Moment Free Ctr (emu)", *[f"Unused {i}" for i in range(60)]]
    records = [["", "1", "100", "500", "1.2e-5", *[""]*60], ["comment", "2", "101", "500", "1.3e-5", *[""]*60]]
    text = newline.join(["[Header]", "1,2,3,4,5", "INFO,synthetic only,SAMPLE", "[Data]", ",".join(headings), *( ",".join(r) for r in records)])
    rows = text_rows(text)
    start = detect_start(rows)
    assert start == 5 and column_names(rows, start) == headings
    path = tmp_path / "synthetic_dc.dat"
    path.write_text(text, encoding="utf-8", newline="")
    dialog = TextImportDialog(None, path)
    qtbot.addWidget(dialog)
    assert dialog.start.value() == 6 and dialog.columns.item(4, 1).text() == headings[4]
    dialog.load()
    assert len(dialog.waves) == len(headings)
    np.testing.assert_allclose(dialog.waves[4].data, [1.2e-5, 1.3e-5])
    assert np.isnan(dialog.waves[-1].data).all()
    trimmed = text_rows("[Data]\nA,B,C\n1,2\n3,4")
    assert column_names(trimmed, detect_start(trimmed)) == ["A", "B", "C"]
    waves = import_columns(trimmed, 2, ["A", "B", "C"], ["Number"]*3)
    assert np.isnan(waves[2].data).all()


@pytest.mark.parametrize("code", ["import socket", "import os", "import requests", "import subprocess", "import ctypes", "from pathlib import Path", "from numpy import *", "open('secret')", "eval('1')", "np.ctypeslib", "wave.__globals__", "getattr(np,'load')", "np.load('secret.npy')", "np.asarray([1]).tofile('secret')"])
def test_scientific_policy_rejects_external_surfaces(code):
    with pytest.raises((ValueError, SyntaxError)):
        validate(code)


def test_allowlisted_modules_have_no_io(monkeypatch):
    from materials_data_studio.tools.procedure_api import install_api
    monkeypatch.setitem(sys.modules, "mds", None)
    scope = namespace(install_api(DocumentServices(empty_document())))
    importer = scope["__builtins__"]["__import__"]
    np_api = importer("numpy")
    assert not hasattr(np_api, "load") and not hasattr(np_api, "ctypeslib")
    assert "open" not in scope["__builtins__"]
    assert hasattr(importer("scipy.signal", fromlist=("savgol_filter",)), "savgol_filter")
    with pytest.raises(ImportError):
        importer("socket")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer OS integration")
def test_os_boundary_files_network_process_and_clipboard(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    sentinel = tmp_path / "host.txt"
    sentinel.write_text("synthetic protected sentinel", encoding="utf-8")
    from materials_data_studio.tools.procedure_sandbox import runtime_directory
    runtime, _ = runtime_directory()
    with ExitStack() as listeners:
        # Windows reserves TCP/UDP ports independently. A port allocated for
        # TCP's dynamic range can be entirely excluded for explicit UDP binds
        # on a hosted runner. Let UDP select an available port first, then
        # verify that TCP can use it too; keep every isolation assertion.
        for _ in range(20):
            tcp = listeners.enter_context(socket.socket())
            udp = listeners.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            udp.bind(("127.0.0.1", 0))
            port = udp.getsockname()[1]
            try:
                tcp.bind(("127.0.0.1", port))
                tcp.listen()
                break
            except OSError as exc:
                tcp.close()
                udp.close()
                if getattr(exc, "winerror", None) not in {10013, 10048}:
                    raise
        else:
            pytest.fail("Could not allocate a shared TCP/UDP probe port after 20 attempts")
        (tmp_path / "input/procedure.json").write_text(json.dumps({"host_read": str(sentinel), "host_write": str(tmp_path/"host-write.txt"), "runtime_write": str(runtime/"forbidden-write.txt"), "port": port}), encoding="utf-8")
        assert launch(tmp_path, probe=True) == 0
        report = json.loads((tmp_path/"output/output.json").read_text(encoding="utf-8"))
        assert report["policy"]["appcontainer"] == 1 and report["policy"]["capabilities"] == 0
        for key in ("host_read", "host_write", "runtime_write"):
            assert report[key]["errno"] == 13
        assert report["spawn"] != "ALLOWED" and not report["clipboard"]
        for channel in (tcp, udp):
            channel.settimeout(0.2)
            with pytest.raises(TimeoutError):
                channel.accept() if channel is tcp else channel.recvfrom(100)
        assert sentinel.read_text(encoding="utf-8") == "synthetic protected sentinel"
        assert not (tmp_path/"sandbox.json").exists()
