"""Synthetic worker descriptions never grant arbitrary host/UI capabilities."""
from __future__ import annotations

from collections import deque
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QFileDialog, QLineEdit, QMessageBox, QWidget

from materials_data_studio.tools.igor_dialogs import (
    DirectoryInputsDialog,
    IgorInteractionController,
    IgorPromptDialog,
    directory_inputs,
    file_filter,
)


class Session:
    def __init__(self, *requests):
        self.requests = deque(requests)
        self.replies = []
        self.closed = False

    def poll(self):
        return self.requests.popleft() if self.requests else None

    def respond(self, request, response):
        self.replies.append((request, response))

    def close(self):
        self.closed = True


def request(kind, **payload):
    return {"version": 1, "sequence": 1, "kind": kind, "payload": payload}


@pytest.fixture
def workspace(qtbot):
    widget = QWidget()
    qtbot.addWidget(widget)
    logs = []
    return SimpleNamespace(window=widget, write=lambda *args: logs.append(args), stop=lambda: logs.append(("stopped",)), logs=logs)


def test_file_filter_translates_source_filters_without_execution():
    assert "*.prf" in file_filter("Profiles (*.prf):.prf;")
    assert file_filter("Data (*.dat):.dat;").count("*.dat") == 1
    assert "*.csv" in file_filter("CSV (*.csv);;All files (*)")
    assert "All files (*)" in file_filter("not code")


def test_prompt_supports_numbers_text_and_popups_without_nested_eventloop(qtbot):
    dialog = IgorPromptDialog({"title": "Synthetic parameters", "fields": [
        {"name": "mass", "label": "Mass", "type": "number", "value": 1.0},
        {"name": "name", "label": "<b>Sample</b>", "type": "text", "value": "demo"},
        {"name": "mode", "label": "Mode", "type": "choice", "value": 1, "choices": ["A", "B"]},
    ]}, None)
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog.isModal()
    mass = dialog.controls["mass"]
    assert isinstance(mass, QLineEdit)
    mass.setText("NaN")
    dialog.accept()
    assert dialog.isVisible() and dialog.error.text()
    mass.setText("2.5e-3")
    name = dialog.controls["name"]
    assert isinstance(name, QLineEdit)
    name.setText("new sample")
    choice = dialog.controls["mode"]
    assert isinstance(choice, QComboBox)
    choice.setCurrentIndex(0)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.values == {"mass": .0025, "name": "new sample", "mode": 0}


def test_controller_only_shows_file_picker_at_runtime_request(workspace):
    session = Session(request("file_open", title="Select a measurement", filter="*.prf"))
    controller = IgorInteractionController(workspace, session, [])
    assert controller.dialog is None
    controller.poll()
    dialog = controller.dialog
    assert isinstance(dialog, QFileDialog)
    assert dialog.windowTitle() == "Select a measurement"
    assert dialog.fileMode() == QFileDialog.FileMode.ExistingFile
    assert not dialog.isModal()
    assert not session.replies
    dialog.reject()
    assert session.replies[0][1] == {"status": "cancelled", "payload": {}}
    controller.close()


def test_controller_file_read_requires_user_selection_and_remains_cumulative(workspace, tmp_path):
    path = tmp_path / "synthetic.prf"
    path.write_bytes(b"1 2\n3 4\n")
    session = Session(request("file_open", title="Select text"))
    controller = IgorInteractionController(workspace, session, [])
    controller.poll()
    assert isinstance(controller.dialog, QFileDialog)
    dialog = controller.dialog
    dialog.selectFile(str(path))
    dialog.accept()
    response = session.replies[0][1]
    assert response["status"] == "ok"
    assert response["payload"]["selected"] == str(path)
    assert response["payload"]["resources"] == [{"name": path.name, "path": str(path), "text": "1 2\n3 4\n"}]
    assert controller.resources == response["payload"]["resources"]
    controller.close()


def test_file_dialog_survives_nested_read_and_deferred_delete(workspace, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid
    path = tmp_path / "nested.prf"
    path.write_text("1 2\n3 4\n")
    session = Session(request("file_open", title="Select text"))
    controller = IgorInteractionController(workspace, session, [])
    controller.poll()
    picker = controller.dialog
    assert isinstance(picker, QFileDialog)
    picker.selectFile(str(path))
    def nested_read(_owner, _title, function, **_kwargs):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert isValid(picker), "A nested progress loop must not delete the active accept() receiver"
        return function()
    monkeypatch.setattr("materials_data_studio.tools.igor_dialogs.blocking_task", nested_read)
    picker.accept()
    assert session.replies[0][1]["status"] == "ok"
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(picker)
    controller.close()


def test_reply_only_contains_current_selection_but_size_policy_is_cumulative(workspace, tmp_path):
    old_path = tmp_path / "old.dat"
    new_path = tmp_path / "new.dat"
    old_path.write_bytes(b"1 2\n")
    new_path.write_bytes(b"3 4\n")
    old = {"name": old_path.name, "path": str(old_path), "text": "1 2\n"}
    session = Session(request("file_open", title="Select another file"))
    controller = IgorInteractionController(workspace, session, [old])
    controller.poll()
    picker = controller.dialog
    controller._read_files([new_path], str(new_path))
    assert [item["name"] for item in session.replies[0][1]["payload"]["resources"]] == ["new.dat"]
    assert {item["name"] for item in controller.resources} == {"old.dat", "new.dat"}
    picker.hide()
    picker.deleteLater()
    controller.close()


def test_empty_directory_confirmation_does_not_reintroduce_previous_files(workspace, tmp_path):
    old = {"name": "old.dat", "path": str(tmp_path / "old.dat"), "text": "1 2\n"}
    session = Session(request("directory", title="Choose empty folder"))
    controller = IgorInteractionController(workspace, session, [old])
    controller.poll()
    picker = controller.dialog
    controller._read_files([], str(tmp_path))
    assert session.replies[0][1]["payload"] == {"resources": [], "selected": str(tmp_path)}
    assert controller.resources == [old]
    picker.hide()
    picker.deleteLater()
    controller.close()


def test_controller_prompt_close_and_stop_never_send_stale_reply(workspace):
    session = Session(request("prompt", title="Synthetic", fields=[{"name": "x", "label": "X", "type": "number", "value": 2}]))
    controller = IgorInteractionController(workspace, session, [])
    controller.poll()
    assert isinstance(controller.dialog, IgorPromptDialog)
    dialog = controller.dialog
    controller.close()
    assert not dialog.isVisible() and session.closed
    dialog.accept()
    assert not session.replies


def test_previous_dialog_cannot_reply_to_next_matching_prompt(workspace):
    fields = [{"name": "x", "label": "X", "type": "number", "value": 2}]
    session = Session(request("prompt", title="First", fields=fields), request("prompt", title="Second", fields=fields))
    controller = IgorInteractionController(workspace, session, [])
    controller.poll()
    first = controller.dialog
    assert isinstance(first, IgorPromptDialog)
    first.accept()
    controller.poll()
    second = controller.dialog
    assert isinstance(second, IgorPromptDialog) and second is not first
    first.accepted.emit()
    first.rejected.emit()
    assert len(session.replies) == 1 and controller.dialog is second
    second.accept()
    assert len(session.replies) == 2
    controller.close()


def test_controller_message_buttons_are_explicit_and_plaintext(workspace):
    session = Session(request("message", title="Confirm", text="<b>Not HTML</b>", buttons=["Yes", "No"]))
    controller = IgorInteractionController(workspace, session, [])
    controller.poll()
    assert isinstance(controller.dialog, QMessageBox)
    dialog = controller.dialog
    assert dialog.textFormat() == Qt.TextFormat.PlainText
    button = next(button for button in dialog.buttons() if button.text() == "No")
    button.click()
    assert session.replies[0][1] == {"status": "ok", "payload": {"button": 1}}
    controller.close()


def test_directory_selection_is_bounded_nonrecursive_and_explicit(tmp_path, qtbot):
    (tmp_path / "b.dat").write_text("1 2")
    (tmp_path / "a.dat").write_text("3 4")
    (tmp_path / "skip.bin").write_text("not text data")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub/nested.dat").write_text("5 6")
    paths = directory_inputs(tmp_path, "*.dat", Event())
    assert [path.name for path in paths] == ["a.dat", "b.dat"]
    dialog = DirectoryInputsDialog(paths, None)
    qtbot.addWidget(dialog)
    dialog.files.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.selected_paths() == [tmp_path / "b.dat"]
    stopped = Event()
    stopped.set()
    with pytest.raises(InterruptedError):
        directory_inputs(tmp_path, "", stopped)


def test_directory_request_shows_file_confirmation_before_reading(workspace, tmp_path):
    (tmp_path / "synthetic.dat").write_text("1 2")
    session = Session(request("directory", title="Choose folder", filter="*.dat"))
    controller = IgorInteractionController(workspace, session, [])
    controller.poll()
    picker = controller.dialog
    assert isinstance(picker, QFileDialog)
    # QFileDialog's accepted signal is tested separately above; directly feed
    # its explicit user choice to avoid platform-specific directory navigation.
    controller._selected("directory", [str(tmp_path)], {"filter": "*.dat"})
    picker.hide()
    picker.deleteLater()
    confirmation = controller.dialog
    assert isinstance(confirmation, DirectoryInputsDialog)
    assert not session.replies
    confirmation.accept()
    assert session.replies[0][1]["status"] == "ok"
    assert session.replies[0][1]["payload"]["selected"] == str(tmp_path)
    controller.close()


def test_failed_selected_file_read_is_reported_without_document_mutation(workspace, tmp_path):
    session = Session(request("file_open", title="Read selected text"))
    controller = IgorInteractionController(workspace, session, [])
    controller.poll()
    picker = controller.dialog
    controller._read_files([tmp_path / "missing.dat"], str(tmp_path / "missing.dat"))
    assert session.replies[0][1]["status"] == "error"
    assert "existing regular input text file" in session.replies[0][1]["payload"]["message"]
    assert not controller.resources
    picker.hide()
    picker.deleteLater()
    controller.close()


def test_stop_during_file_snapshot_never_sends_a_late_response(workspace, tmp_path, monkeypatch):
    session = Session(request("file_open", title="Read selected text"))
    controller = IgorInteractionController(workspace, session, [])
    controller.poll()
    def stopped(_owner, _title, _function, **_kwargs):
        controller.close()
        return []
    monkeypatch.setattr("materials_data_studio.tools.igor_dialogs.blocking_task", stopped)
    controller._read_files([tmp_path / "not-read.dat"], str(tmp_path / "not-read.dat"))
    assert session.closed and not session.replies


def test_invalid_session_request_stops_without_opening_a_dialog(workspace, monkeypatch):
    session = Session()
    controller = IgorInteractionController(workspace, session, [])
    monkeypatch.setattr(session, "poll", lambda: (_ for _ in ()).throw(ValueError("invalid schema")))
    controller.poll()
    assert controller.dialog is None and ("stopped",) in workspace.logs
    assert any("invalid schema" in str(item) for item in workspace.logs)
    controller.close()
