"""Qt application bootstrap."""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from materials_data_studio.config import APP_NAME, ORGANIZATION_NAME
from materials_data_studio.core.logging_config import configure_logging
from materials_data_studio.core.recovery import find_recovery_candidates
from materials_data_studio.core.session import load_session
from materials_data_studio.core.settings import create_settings
from materials_data_studio.ui.input_wheel_guard import install_input_wheel_guard
from materials_data_studio.ui.main_window import MainWindow
from materials_data_studio.ui.theme import style_sheet

LOGGER = logging.getLogger(__name__)


def install_exception_hook() -> None:
    """Log uncaught GUI exceptions and give users an actionable recovery path."""
    original_hook = sys.excepthook

    def handle_exception(exc_type, exc_value, exc_traceback) -> None:  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            original_hook(exc_type, exc_value, exc_traceback)
            return
        formatted = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        LOGGER.critical("Uncaught application exception\n%s", formatted)
        try:
            QMessageBox.critical(
                None,
                "Unexpected Error",
                "Materials Data Studio encountered an unexpected error. Your current "
                "work will be offered for recovery after an abnormal exit. Use "
                "Help > Create Diagnostic Bundle before reporting the problem.\n\n"
                f"{exc_type.__name__}: {exc_value}",
            )
        except Exception:
            original_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = handle_exception


def _recover_interrupted_session():  # type: ignore[no-untyped-def]
    """Offer independent recovery copies; failed or skipped copies remain intact."""
    candidates = find_recovery_candidates()
    if not candidates:
        return None
    for candidate in candidates:
        response = QMessageBox.question(
            None,
            "Recover Session",
            "Recover the interrupted workspace from "
            f"{candidate.modified_at.astimezone():%Y-%m-%d %H:%M:%S}?\n"
            f"{candidate.path.name}\n\n"
            "No keeps this copy and offers the next one. Cancel keeps all remaining copies.",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if response == QMessageBox.StandardButton.Cancel:
            return None
        if response != QMessageBox.StandardButton.Yes:
            continue
        try:
            state = load_session(candidate.path)
            state.path = None
            state.modified = True
            state.recovery_source = candidate.path
            # Keep the original until the user explicitly saves recovered work.
            return state
        except Exception as exc:
            LOGGER.exception("Could not restore recovery snapshot: %s", candidate.path)
            QMessageBox.warning(
                None, "Recovery Failed", f"{exc}\n\nThe recovery copy was preserved."
            )
    return None


def main() -> int:
    """Start Materials Data Studio and return its process exit code."""
    smoke_flag = "--packaging-smoke-test"
    if smoke_flag in sys.argv:
        flag_index = sys.argv.index(smoke_flag)
        if flag_index + 1 >= len(sys.argv):
            return 2
        from materials_data_studio.core.packaging_smoke import run_packaging_smoke_test

        return 0 if run_packaging_smoke_test(Path(sys.argv[flag_index + 1])) else 1
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setOrganizationName(ORGANIZATION_NAME)
    log_path = configure_logging()
    logging.getLogger(__name__).info("Starting %s; log=%s", APP_NAME, log_path)
    instance = QApplication.instance()
    application = instance if isinstance(instance, QApplication) else QApplication(sys.argv)
    install_input_wheel_guard()
    install_exception_hook()
    application.setApplicationDisplayName(APP_NAME)
    application.setStyle("Fusion")
    theme = str(create_settings().value("appearance/theme", "light"))
    if theme not in {"light", "dark"}:
        theme = "light"
    application.setStyleSheet(style_sheet(theme))
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    icon_path = resource_root / "resources" / "icons" / "materials-data-studio.ico"
    if icon_path.is_file():
        application.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow(_recover_interrupted_session())
    window.show()
    from materials_data_studio.ui.updates import controller
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        QTimer.singleShot(1500, lambda: controller().check(window, automatic=True))
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        from materials_data_studio.integrations.windows_files import register_document_types
        def register_files() -> None:
            try:
                register_document_types()
            except OSError:
                LOGGER.exception("Could not register MDS document types")
        QTimer.singleShot(0, register_files)
    paths = [Path(argument) for argument in sys.argv[1:] if not argument.startswith("-")]
    if paths:
        QTimer.singleShot(0, lambda: window._import_dropped_paths(paths))
    return application.exec()
