"""Synthetic converted panels use explicit, undoable parameters and isolated Run."""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from materials_data_studio.tools.graph_document import save_graph_document
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.graph_window import GraphEditorWindow
from materials_data_studio.tools.igor_parameter_panel import (
    ENTRY_MARKER,
    IgorParameterDialog,
    callback_source,
    panel_metadata,
)

SOURCE = (
    "from mds import igor\n"
    "def mdsRoutine1():\n    return 0\n"
    "def mdsRoutine2(event):\n    return 0\n"
    + ENTRY_MARKER + "\nmdsRoutine1()\n"
)
PANEL = [{"name": "SyntheticPanel", "title": "Synthetic parameters",
          "fields": [{"name": "amplitude", "title": "Amplitude", "path": "root:settings:level"}],
          "buttons": [{"name": "apply", "title": "Calculate", "callback": "SyntheticCallback",
                       "python_callable": "mdsRoutine2"}], "groups": []}]


@pytest.fixture
def window(qtbot):
    widget = GraphEditorWindow()
    widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    widget._discard_approved = True
    document = empty_document()
    document.current.environment.update({"igor_parameter_panels": copy.deepcopy(PANEL),
                                         "variables": {"root:settings:level": 2.5},
                                         "python_source": SOURCE})
    widget.bind_graph_document(document)
    qtbot.addWidget(widget)
    yield widget
    widget.close()


@pytest.fixture
def dialog(window):
    window.command_workspace.show_macro_parameters()
    return window.command_workspace.parameter_dialog


def test_callback_source_changes_only_explicit_entry():
    result = callback_source(SOURCE, "mdsRoutine2")
    assert result.startswith(SOURCE.split(ENTRY_MARKER)[0] + ENTRY_MARKER)
    assert result.endswith("mdsRoutine2({'eventCode': 2})\n")
    assert "\nmdsRoutine1()" not in result


@pytest.mark.parametrize("name,source", [
    ("mdsRoutine2(); bad()", SOURCE), ("arbitrary", SOURCE), ("mdsRoutine999", SOURCE),
    ("mdsRoutine2", SOURCE.replace(ENTRY_MARKER, "# edited")),
    ("mdsRoutine2", SOURCE + ENTRY_MARKER + "\n"),
])
def test_callback_rejects_untrusted_or_missing_entry(name, source):
    with pytest.raises(ValueError):
        callback_source(source, name)


@pytest.mark.parametrize("metadata", [None, [None], [{}] * 65,
    [{"fields": [{}] * 257}], [{"buttons": [{}] * 65}],
    [{"fields": [{"path": "relative"}]}], [{"fields": [{"path": "root:a\nExecute bad"}]}],
    [{"name": 1}], [{"title": "x" * 4097}], [{"title": "bad\0"}],
])
def test_panel_metadata_is_bounded_and_descriptive(metadata):
    with pytest.raises(ValueError):
        panel_metadata(metadata)


def test_parameter_edits_apply_as_one_local_undo(window, dialog):
    assert window.command_workspace.parameters_button.isEnabled()
    assert dialog.fields["root:settings:level"].text() == "2.5"
    original_count = window.editor.history.count()
    dialog.fields["root:settings:level"].setText("4e-3")
    assert window.editor.document.current.environment["variables"]["root:settings:level"] == 2.5
    assert dialog.apply_parameters()
    assert window.editor.document.current.environment["variables"]["root:settings:level"] == .004
    assert window.editor.history.count() == original_count + 1
    window.editor.history.undo()
    assert window.editor.document.current.environment["variables"]["root:settings:level"] == 2.5
    assert dialog.fields["root:settings:level"].text() == "2.5"
    window.editor.history.redo()
    assert dialog.fields["root:settings:level"].text() == "0.004"


@pytest.mark.parametrize("text", ["NaN", "inf", "-Infinity", "1/2", "not a number"])
def test_invalid_values_never_commit(window, dialog, text):
    count = window.editor.history.count()
    dialog.fields["root:settings:level"].setText(text)
    assert not dialog.apply_parameters()
    assert window.editor.document.current.environment["variables"]["root:settings:level"] == 2.5
    assert window.editor.history.count() == count


def test_callback_requires_explicit_click_and_uses_existing_run(window, dialog, monkeypatch):
    calls = []
    monkeypatch.setattr(window.command_workspace, "start", calls.append)
    dialog.fields["root:settings:level"].setText("3")
    assert not calls and window.command_workspace.process is None
    dialog.callback_buttons[0].click()
    assert len(calls) == 1 and calls[0].endswith("mdsRoutine2({'eventCode': 2})\n")
    assert window.command_workspace.code.toPlainText() == calls[0]
    assert window.editor.document.current.environment["variables"]["root:settings:level"] == 3


def test_invalid_callback_source_does_not_apply_pending_edits(window, dialog, monkeypatch):
    monkeypatch.setattr(window.command_workspace, "start", lambda *_args: pytest.fail("No execution"))
    window.command_workspace.code.setPlainText('print("not converted")')
    window.command_workspace.flush()
    dialog.fields["root:settings:level"].setText("100")
    dialog.run_callback(PANEL[0]["buttons"][0])
    assert "not defined" in dialog.status.text()
    assert window.editor.document.current.environment["variables"]["root:settings:level"] == 2.5


def test_running_worker_disables_editing_and_callbacks(window, dialog, monkeypatch):
    monkeypatch.setattr(window.command_workspace, "is_running", lambda: True)
    window.command_workspace.update_buttons()
    assert not dialog.apply_button.isEnabled()
    assert not dialog.fields["root:settings:level"].isEnabled()
    assert not dialog.callback_buttons[0].isEnabled()
    assert not dialog.apply_parameters()
    monkeypatch.setattr(window.command_workspace, "is_running", lambda: False)


def test_invalid_global_metadata_does_not_leave_live_partial_controls(window, dialog):
    environment = window.editor.document.current.environment
    environment["igor_parameter_panels"].append({"name": "Broken", "fields": [{"path": "root:missing"}]})
    dialog.refresh()
    assert "Missing" in dialog.status.text()
    assert not dialog.fields and not dialog.callback_buttons
    assert not dialog.findChildren(QPushButton, options=Qt.FindChildOption.FindDirectChildrenOnly)
    assert not dialog.apply_button.isEnabled()


def test_new_document_closes_panel_and_disables_button(window, dialog):
    window.bind_graph_document(empty_document())
    assert not dialog.isVisible()
    assert window.command_workspace.parameter_dialog is None
    assert not window.command_workspace.parameters_button.isEnabled()


def test_plain_text_labels_do_not_render_imported_html(window):
    environment = window.editor.document.current.environment
    environment["igor_parameter_panels"][0]["fields"][0]["title"] = '<img src="file:///C:/private.png">'
    dialog = IgorParameterDialog(window.command_workspace)
    from PySide6.QtWidgets import QLabel
    matching = [label for label in dialog.findChildren(QLabel) if "<img" in label.text()]
    assert matching and all(label.textFormat() == Qt.TextFormat.PlainText for label in matching)
    dialog.close()


def test_imported_global_path_tooltip_escapes_html(window):
    environment = window.editor.document.current.environment
    path = 'root:<img src="file:///C:/private.png">'
    environment["variables"][path] = 1.0
    environment["igor_parameter_panels"][0]["fields"][0]["path"] = path
    dialog = IgorParameterDialog(window.command_workspace)
    assert "<img" not in dialog.fields[path].toolTip()
    assert "&lt;img" in dialog.fields[path].toolTip()
    dialog.close()


def test_source_panel_geometry_colors_and_numeric_step_buttons(window):
    panel = window.editor.document.current.environment["igor_parameter_panels"][0]
    panel.update({"rect": [50, 50, 450, 175], "groups": [{"name": "group", "title": "Load data", "pos": [10, 10], "size": [380, 110]}]})
    panel["fields"][0].update({"pos": [20, 35], "size": [180, 20], "limits": [0, 100, .25]})
    panel["buttons"][0].update({"pos": [210, 35], "size": [160, 50], "fcolor": [65535, 55000, 49151]})
    dialog = IgorParameterDialog(window.command_workspace)
    assert dialog.windowTitle() == "Synthetic parameters"
    edit = dialog.fields["root:settings:level"]
    assert edit.parentWidget().geometry().x() >= 20
    edit.up.click()
    assert edit.text() == "2.75"
    assert dialog.callback_buttons[0].text() == "Calculate"
    assert "rgb(255,214,191)" in dialog.callback_buttons[0].styleSheet()
    assert "#111827" in dialog.callback_buttons[0].styleSheet()
    dialog.close()


def test_native_button_default_and_explicit_black_are_distinct(window):
    button = window.editor.document.current.environment["igor_parameter_panels"][0]["buttons"][0]
    button["fcolor"] = [0, 0, 0]
    dialog = IgorParameterDialog(window.command_workspace)
    assert not dialog.callback_buttons[0].styleSheet()
    button["fcolor"] = [1, 1, 1]
    dialog.refresh()
    assert "background-color: rgb(0,0,0)" in dialog.callback_buttons[0].styleSheet()
    assert "#ffffff" in dialog.callback_buttons[0].styleSheet()
    dialog.close()


def test_text_global_parameters_are_undoable_and_refresh_after_external_edit(window):
    environment = window.editor.document.current.environment
    environment["strings"] = {"root:settings:name": "initial"}
    environment["igor_parameter_panels"][0]["fields"].append({"name": "name", "title": "Sample", "path": "root:settings:name", "value_type": "string"})
    dialog = IgorParameterDialog(window.command_workspace)
    dialog.fields["root:settings:name"].setText("edited text")
    assert dialog.apply_parameters()
    assert window.editor.document.current.environment["strings"]["root:settings:name"] == "edited text"
    window.editor.history.undo()
    dialog.refresh()
    assert dialog.fields["root:settings:name"].text() == "initial"
    dialog.close()


@pytest.mark.parametrize("item", [
    {"rect": [0, 0, 1e100, 4]},
    {"groups": [{}] * 65},
    {"fields": [{"path": "root:x", "pos": [0]}]},
    {"fields": [{"path": "root:x", "size": [0, 20]}]},
    {"fields": [{"path": "root:x", "value_type": "python"}]},
    {"fields": [{"path": "root:x", "limits": [10, 1, .1]}]},
])
def test_source_geometry_and_control_kinds_are_validated(item):
    with pytest.raises(ValueError):
        panel_metadata([item])


def test_unbounded_limits_remain_strict_json_and_step_correctly(window):
    field = window.editor.document.current.environment["igor_parameter_panels"][0]["fields"][0]
    field["limits"] = [None, None, .5]
    json.dumps(panel_metadata(window.editor.document.current.environment["igor_parameter_panels"]), allow_nan=False)
    dialog = IgorParameterDialog(window.command_workspace)
    edit = dialog.fields["root:settings:level"]
    edit.down.click()
    assert edit.text() == "2"
    assert dialog.apply_parameters()
    dialog.close()


def test_successful_panel_initialization_opens_modeless_panel(window):
    workspace = window.command_workspace
    workspace.run_editor = window.editor
    workspace.run_state = window.editor.document.current
    workspace.run_revision = window.editor.history.index()
    workspace.work = tempfile.TemporaryDirectory(prefix="mds-synthetic-panel-")
    root = Path(workspace.work.name)
    (root / "output").mkdir()
    save_graph_document(root / "output/output.mdsg", window.editor.document)
    (root / "output/output.json").write_text("{}", encoding="utf-8")
    workspace.finished(0, None)
    assert workspace.parameter_dialog is not None and workspace.parameter_dialog.isVisible()
    assert not workspace.parameter_dialog.isModal()


def test_gui_run_enables_interaction_only_in_temporary_context(window, monkeypatch):
    from PySide6.QtCore import QProcess

    workspace = window.command_workspace
    monkeypatch.setattr(QProcess, "start", lambda *_args: None)
    context = workspace.engine.services.context.copy()
    workspace.start('print("synthetic")')
    try:
        assert workspace.work is not None
        root = Path(workspace.work.name)
        written = json.loads((root / "input/procedure.json").read_text(encoding="utf-8"))
        assert written["igor_interactive"] is True
        assert "igor_interactive" not in workspace.engine.services.context
        assert workspace.engine.services.context == context
        assert (root / "input/interaction_responses.jsonl").is_file()
        assert workspace.interaction_controller is not None
    finally:
        workspace.cleanup()
