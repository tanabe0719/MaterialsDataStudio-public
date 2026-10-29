"""Startup opt-out, shared manual flow and cancelled restart preserve work."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

from materials_data_studio.core.settings import create_settings
from materials_data_studio.ui import updates
from materials_data_studio.ui.localization import set_language


def make_controller(qapp, qtbot):
    result = updates.UpdateController(qapp)
    dialog = result.show()
    qtbot.addWidget(dialog)
    return result


def test_startup_opt_out_is_persistent_manual_check_still_works(qapp, qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("MDS_SETTINGS_DIRECTORY", str(tmp_path))
    c = make_controller(qapp, qtbot)
    calls = []
    monkeypatch.setattr(c, "start", lambda operation: calls.append(operation))
    c.dialog.disable_startup.setChecked(True)
    assert not updates.startup_enabled()
    c.check(automatic=True)
    assert calls == []
    c.check()
    assert calls == ["check"]
    c.dialog.disable_startup.setChecked(False)
    assert updates.startup_enabled()
    c.deleteLater()


def test_release_notes_and_exact_version_link_are_shown_for_both_paths(qapp, qtbot):
    c = make_controller(qapp, qtbot)
    for automatic in (False, True):
        c.automatic = automatic
        c.plan = {"version": "0.15.1", "notes": "## English\nNew fitting\n## 日本語\n新しい機能"}
        c.offer(True)
        assert "New fitting" in c.dialog.notes.toPlainText()
        assert "新しい機能" in c.dialog.notes.toPlainText()
        assert c.release_url().endswith("/releases/tag/v0.15.1")
    c.deleteLater()


def test_cancelled_save_never_arms_update_or_closes_any_window(qapp, qtbot, monkeypatch):
    c = make_controller(qapp, qtbot)
    c.plan = {"version": "0.15.1"}
    class DirtyWindow(QWidget):
        def confirm_update_shutdown(self):
            return False
    window = DirtyWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(QApplication, "topLevelWidgets", lambda: [window])
    monkeypatch.setattr("materials_data_studio.ui.tasks.has_active_tasks", lambda: False)
    monkeypatch.setattr(updates.updates, "arm_restart", lambda *_a: __import__("pytest").fail("Must not apply"))
    c.restart()
    c.deleteLater()


def test_active_jobs_do_not_arm_updater_exit_timeout(qapp, qtbot, monkeypatch):
    c = make_controller(qapp, qtbot)
    c.plan = {"version": "0.15.1"}
    messages = []
    monkeypatch.setattr("materials_data_studio.ui.tasks.has_active_tasks", lambda: True)
    monkeypatch.setattr(QMessageBox, "information", lambda *_a: messages.append(True))
    monkeypatch.setattr(updates.updates, "arm_restart", lambda *_a: __import__("pytest").fail("Must not apply"))
    c.restart()
    assert messages == [True]
    c.deleteLater()


def test_update_actions_have_japanese_labels_and_restore_startup_check(qapp, qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("MDS_SETTINGS_DIRECTORY", str(tmp_path))
    create_settings().setValue(updates.updates.STARTUP_SETTING, False)
    menu = QMenu()
    qtbot.addWidget(menu)
    updates.add_update_actions(menu, menu)
    set_language("ja")
    updates.translate_tree(menu)
    assert menu.actions()[0].text() == "アップデートを確認…"
    assert not menu.actions()[1].isChecked()
    menu.actions()[1].setChecked(True)
    assert updates.startup_enabled()
    set_language("en")


def test_notes_never_load_external_or_local_resources(qapp, qtbot):
    from PySide6.QtCore import QUrl
    browser = updates.NotesBrowser()
    qtbot.addWidget(browser)
    assert browser.loadResource(2, QUrl("file:///private.txt")) is None
    assert browser.loadResource(2, QUrl("https://example.com/pixel.png")) is None


def test_worker_keeps_gui_responsive_and_returns_a_visible_result(qapp, qtbot, monkeypatch):
    c = make_controller(qapp, qtbot)
    original = QProcess.setArguments
    response = json.dumps({"event": "result", "value": {"supported": True, "plan": None}})
    monkeypatch.setattr(QProcess, "setArguments", lambda process, _args: original(process, [
        "-c", "import time; time.sleep(.2); print(" + repr(response) + ",flush=True)"]))
    ticks = []
    QTimer.singleShot(50, lambda: ticks.append(True))
    c.check()
    qtbot.waitUntil(lambda: c.process is None, timeout=10000)
    assert ticks == [True]
    assert "latest" in c.dialog.status.text()
    c.deleteLater()


def test_failed_apply_keeps_documents_open(qapp, qtbot, monkeypatch):
    c = make_controller(qapp, qtbot)
    c.plan = {"version": "0.15.1"}
    class Document(QWidget):
        def confirm_update_shutdown(self):
            return True
    document = Document()
    qtbot.addWidget(document)
    document.show()
    monkeypatch.setattr(QApplication, "topLevelWidgets", lambda: [document])
    monkeypatch.setattr("materials_data_studio.ui.tasks.has_active_tasks", lambda: False)
    monkeypatch.setattr(updates.updates, "other_instances_running", lambda: False)
    def fail(*_args):
        raise OSError("cannot start updater")
    monkeypatch.setattr(updates.updates, "arm_restart", fail)
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a: messages.append(True))
    c.restart()
    assert document.isVisible() and not getattr(document, "_update_shutdown_approved", False)
    assert messages == [True]
    c.deleteLater()


def test_restart_checks_all_documents_before_arming_and_clean_shutdown(qapp, qtbot, monkeypatch):
    c = make_controller(qapp, qtbot)
    c.plan = {"version": "0.15.1"}
    events = []
    class Document(QWidget):
        def confirm_update_shutdown(self):
            events.append("confirm")
            return True
    document = Document()
    qtbot.addWidget(document)
    monkeypatch.setattr(QApplication, "topLevelWidgets", lambda: [document])
    monkeypatch.setattr("materials_data_studio.ui.tasks.has_active_tasks", lambda: False)
    monkeypatch.setattr(updates.updates, "other_instances_running", lambda: False)
    monkeypatch.setattr(updates.updates, "arm_restart", lambda *_a: events.append("arm"))
    monkeypatch.setattr(QApplication, "closeAllWindows", lambda: events.append("close"))
    monkeypatch.setattr(QApplication, "quit", lambda: events.append("quit"))
    c.restart()
    assert events == ["confirm", "arm", "close", "quit"]
    assert document._update_shutdown_approved
    c.deleteLater()


def test_downloaded_update_can_be_reopened_without_network(qapp, qtbot, monkeypatch):
    c = make_controller(qapp, qtbot)
    c.ready = True
    c.plan = {"version": "0.15.1"}
    calls = []
    monkeypatch.setattr(c, "start", lambda operation: calls.append(operation))
    c.check()
    assert calls == [] and c.ready
    c.check(force=True)
    assert calls == ["check"] and not c.ready
    c.deleteLater()


def test_a_job_started_during_save_confirmation_also_blocks_apply(qapp, qtbot, monkeypatch):
    c = make_controller(qapp, qtbot)
    c.plan = {"version": "0.15.1"}
    busy = [False]
    class Document(QWidget):
        def confirm_update_shutdown(self):
            busy[0] = True
            return True
    document = Document()
    qtbot.addWidget(document)
    monkeypatch.setattr(QApplication, "topLevelWidgets", lambda: [document])
    monkeypatch.setattr("materials_data_studio.ui.tasks.has_active_tasks", lambda: busy[0])
    monkeypatch.setattr(QMessageBox, "information", lambda *_a: None)
    monkeypatch.setattr(updates.updates, "arm_restart", lambda *_a: __import__("pytest").fail("Must not apply"))
    c.restart()
    assert not getattr(document, "_update_shutdown_approved", False)
    c.deleteLater()


def test_update_dialog_does_not_keep_application_alive_after_main_close():
    code = """
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
from materials_data_studio.ui.updates import UpdateController
app=QApplication([])
app.setQuitOnLastWindowClosed(True)
owner=QWidget()
owner.show()
controller=UpdateController(app)
dialog=controller.show(owner)
assert dialog.parentWidget() is owner
timed_out=[]
def timeout():
    timed_out.append(True)
    app.quit()
QTimer.singleShot(100,owner.close)
QTimer.singleShot(1500,timeout)
app.exec()
assert not timed_out
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, timeout=15)
    assert result.returncode == 0, result.stderr


def test_help_setting_and_open_dialog_stay_synchronized(qapp, qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("MDS_SETTINGS_DIRECTORY", str(tmp_path))
    c = make_controller(qapp, qtbot)
    monkeypatch.setattr(qapp, "_mds_update_controller", c, raising=False)
    c.dialog.disable_startup.setChecked(True)
    assert not updates.startup_enabled()
    updates.set_startup_enabled(True)
    assert not c.dialog.disable_startup.isChecked()
    create_settings().setValue(updates.updates.STARTUP_SETTING, False)
    c.show()
    assert c.dialog.disable_startup.isChecked()
    c.deleteLater()


def test_synchronous_windows_start_failure_restores_retry_controls(qapp, qtbot, monkeypatch):
    c = make_controller(qapp, qtbot)
    monkeypatch.setattr(QProcess, "start", lambda process: c.failed_start(process, QProcess.ProcessError.FailedToStart))
    c.check()
    assert c.process is None and c.dialog.action.isEnabled()
    assert c.dialog.progress.isHidden()
    c.deleteLater()
