"""Explicit prepared input data stays transient and never grants worker I/O."""
from __future__ import annotations

from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog

from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.igor_conversion_dialog import IgorConversionDialog
from materials_data_studio.tools.igor_input_resources import read_input_files


@pytest.fixture
def window(qtbot):
    widget = GraphEditorWindow()
    widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    widget._discard_approved = True
    widget.bind_graph_document(empty_document())
    qtbot.addWidget(widget)
    yield widget
    widget.close()


@pytest.fixture
def dialog(window, qtbot):
    widget = IgorConversionDialog(window, [])
    qtbot.addWidget(widget)
    return widget


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp932", "cp1252"])
def test_input_encodings_preserve_crlf_and_selected_path_only(tmp_path, encoding):
    text = "heading\r\n0,1\r\n1,2\r\n"
    text += "// 合成\r\n" if encoding != "cp1252" else "// café\r\n"
    path = tmp_path / "synthetic.prf"
    path.write_bytes(text.encode(encoding))
    result = read_input_files([path], [])
    assert result == [{"name": path.name, "path": str(path.absolute()), "text": text}]
    assert path.read_bytes() == text.encode(encoding)


def test_input_limit_is_decoded_utf8_total_and_replace_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr("materials_data_studio.tools.igor_input_resources.MAX_INPUT_BYTES", 10)
    path = tmp_path / "first.dat"
    path.write_bytes(b"12345678")
    original = read_input_files([path], [])
    path.write_bytes(b"new")
    replaced = read_input_files([path], original)
    assert replaced[0]["text"] == "new" and original[0]["text"] == "12345678"
    other = tmp_path / "second.dat"
    other.write_bytes("合成資料".encode("cp932"))  # 8 on disk, 12 as UTF-8.
    with pytest.raises(ValueError, match="64 MiB"):
        read_input_files([other], [])
    with pytest.raises(ValueError, match="64 MiB"):
        read_input_files([path], [{"name": "old", "path": "other", "text": "x" * 11}])
    assert original[0]["text"] == "12345678"


def test_stopped_missing_and_invalid_inputs_never_modify_existing(tmp_path):
    original = [{"name": "existing.dat", "path": "C:/existing.dat", "text": "0,1\r\n"}]
    stop = Event()
    stop.set()
    with pytest.raises(InterruptedError):
        read_input_files([tmp_path / "first.dat"], original, cancelled=stop)
    with pytest.raises(ValueError, match="regular"):
        read_input_files([tmp_path / "missing.dat"], original)
    with pytest.raises(ValueError, match="control"):
        read_input_files([tmp_path / "bad\nname.dat"], original)
    assert original == [{"name": "existing.dat", "path": "C:/existing.dat", "text": "0,1\r\n"}]


def test_preparation_rejects_ambiguous_basenames_and_nontext_files(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    paths = [first / "duplicate.dat", second / "DUPLICATE.dat"]
    for path in paths:
        path.write_bytes(b"0,1\r\n")
    with pytest.raises(ValueError, match="Ambiguous"):
        read_input_files(paths, [])
    path = tmp_path / "binary.dat"
    path.write_bytes(b"not\0text")
    with pytest.raises(ValueError, match="NUL"):
        read_input_files([path], [])


def test_input_name_path_and_file_count_limits_match_worker(tmp_path):
    overlong = tmp_path / ("a" * 4097)
    with pytest.raises(ValueError, match="too long"):
        read_input_files([overlong], [])
    resources = [{"name": f"synthetic{index}.dat", "path": f"C:/synthetic{index}.dat", "text": ""}
                 for index in range(1000)]
    with pytest.raises(ValueError, match="1000"):
        read_input_files([tmp_path / "extra.dat"], resources)


def test_dialog_inputs_require_selection_and_have_atomic_stop_and_errors(dialog, monkeypatch, tmp_path):
    path = tmp_path / "synthetic.dat"
    path.write_bytes(b"Heading\r\n0,1\r\n1,2\r\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_args: ([], ""))
    dialog.open_inputs()
    assert not dialog.input_resources
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_args: ([str(path)], ""))
    dialog.open_inputs()
    assert dialog.input_resources[0]["text"] == "Heading\r\n0,1\r\n1,2\r\n"
    assert "1 prepared" in dialog.input_summary.text()
    assert dialog.report is None and not dialog.generated
    original = list(dialog.input_resources)
    dialog.open_inputs([tmp_path / "missing.dat"])
    assert "Cannot prepare" in dialog.status.text() and dialog.input_resources == original
    def stopped(*_args, **_kwargs):
        raise InterruptedError("stopped")
    monkeypatch.setattr("materials_data_studio.tools.igor_conversion_dialog.blocking_task", stopped)
    dialog.open_inputs([path])
    assert "stopped" in dialog.status.text() and dialog.input_resources == original
    assert dialog.input_button.isEnabled()
    dialog.clear_inputs()
    assert not dialog.input_resources and not dialog.clear_inputs_button.isEnabled()


def test_resources_are_accepted_only_with_insert_and_cleared_on_bind(window, monkeypatch):
    workspace = window.command_workspace
    original = [{"name": "previous.dat", "path": "C:/previous.dat", "text": "1\r\n"}]
    updated = [{"name": "prepared.dat", "path": "C:/prepared.dat", "text": "2\r\n"}]
    workspace.igor_input_resources = original
    def cancelled(dialog):
        assert dialog.input_resources == original
        dialog.input_resources = updated
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(IgorConversionDialog, "exec", cancelled)
    workspace.convert_igor()
    assert workspace.igor_input_resources == original
    def inserted(dialog):
        dialog.input_resources = updated
        dialog.generated = 'print("synthetic converted source")'
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(IgorConversionDialog, "exec", inserted)
    workspace.convert_igor()
    assert workspace.igor_input_resources == updated
    assert "igor_input_resources" not in window.editor.document.current.environment
    workspace.bind()
    assert not workspace.igor_input_resources


def test_run_prepares_resources_in_fixed_sidecar_not_mdsg(window, monkeypatch):
    workspace = window.command_workspace
    prepared = [{"name": "prepared.dat", "path": "C:/prepared.dat", "text": "2\r\n"}]
    workspace.igor_input_resources = prepared
    current = window.editor.document.current
    snapshots = []
    def capture(_path, document):
        snapshots.append(document)
        assert document.current is not current
        assert "igor_input_resources" not in document.current.environment
    sidecars = []
    def write_sidecar(path, resources):
        sidecars.append((path, resources))
        assert path.name == "igor_inputs.json" and path.parent.name == "input"
        assert resources == prepared and resources is not prepared
        raise ValueError("Synthetic stop before launching any worker")
    monkeypatch.setattr("materials_data_studio.tools.command_window.save_graph_document", capture)
    monkeypatch.setattr("materials_data_studio.tools.igor_input_resources.write_input_resources", write_sidecar)
    workspace.start('print("synthetic")')
    assert len(snapshots) == 1 and len(sidecars) == 1
    assert "igor_input_resources" not in current.environment
    assert workspace.process is None and workspace.igor_input_resources == prepared
