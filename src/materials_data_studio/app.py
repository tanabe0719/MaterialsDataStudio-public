"""Qt application bootstrap."""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from materials_data_studio.config import APP_NAME, ORGANIZATION_NAME
from materials_data_studio.core.logging_config import configure_logging
from materials_data_studio.core.recovery import find_recovery_candidates
from materials_data_studio.core.session import load_session
from materials_data_studio.core.settings import create_settings
from materials_data_studio.ui.input_wheel_guard import install_input_wheel_guard
from materials_data_studio.ui.theme import style_sheet

LOGGER = logging.getLogger(__name__)


def register_files_safely() -> None:
    from materials_data_studio.integrations.windows_files import register_document_types
    try:
        register_document_types()
    except OSError:
        LOGGER.exception("Could not register MDS document types")


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
    paths = [Path(argument) for argument in sys.argv[1:] if not argument.startswith("-")]
    settings = create_settings()
    reuse = str(settings.value("documents/reuse_instance", "false")).lower() == "true"
    document_endpoint = None
    if reuse:
        from materials_data_studio.integrations.document_open import runtime_path
        document_endpoint = runtime_path()
    if reuse and document_endpoint is not None and paths and "--new-instance" not in sys.argv:
        from materials_data_studio.integrations.document_open import forward_documents
        if forward_documents(paths, document_endpoint):
            return 0
    if paths and all(path.suffix.lower() in {".mdsg", ".pxp", ".h5xp"} for path in paths) and not find_recovery_candidates():
        return run_graph_documents(application, paths, document_endpoint if reuse else None)
    from materials_data_studio.ui.main_window import MainWindow
    window = MainWindow(_recover_interrupted_session(), defer_initial_module=bool(paths))
    if reuse and document_endpoint is not None and "--new-instance" not in sys.argv:
        from materials_data_studio.integrations.document_open import DocumentOpenServer
        document_windows = []
        def open_documents(request):
            for path in request:
                if path.suffix.lower() == ".mdsg":
                    window.tool_manager.open_graph(path)
                else:
                    independent = MainWindow(defer_initial_module=True)
                    independent.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                    document_windows.append(independent)
                    independent.destroyed.connect(lambda _object=None, item=independent: document_windows.remove(item))
                    independent.show()
                    independent.open_session(path)
        server = DocumentOpenServer(document_endpoint, open_documents, application)
        application.aboutToQuit.connect(server.close)
    from materials_data_studio.core.startup_metrics import mark
    mark("main_constructed")
    window.show()
    from materials_data_studio.ui.updates import controller
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        QTimer.singleShot(1500, lambda: controller().check(window, automatic=True))
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        # Let initial document import and the first paint complete before shell I/O.
        QTimer.singleShot(3000, register_files_safely)
    if paths:
        QTimer.singleShot(0, lambda: window._import_dropped_paths(paths))
    return application.exec()


def run_graph_documents(application, paths, document_endpoint=None):
    """Open graph documents without first constructing an unused analysis shell."""
    from materials_data_studio.core.startup_metrics import mark
    from materials_data_studio.tools.graph_window import GraphEditorWindow
    windows = []
    def open_documents(request):
        for path in request:
            if path.suffix.lower() in {".mdss", ".mdsession"}:
                from materials_data_studio.ui.main_window import MainWindow
                window = MainWindow(defer_initial_module=True)
                windows.append(window)
                window.show()
                window.open_session(path)
            else:
                window = GraphEditorWindow()
                windows.append(window)
                window.show()
                mark("first_paint")
                window.open_path(path)
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            window.destroyed.connect(lambda _object=None, item=window: windows.remove(item))
    if document_endpoint is not None and "--new-instance" not in sys.argv:
        from materials_data_studio.integrations.document_open import DocumentOpenServer
        server = DocumentOpenServer(document_endpoint, open_documents, application)
        application.aboutToQuit.connect(server.close)
    QTimer.singleShot(0, lambda: open_documents(paths))
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        from materials_data_studio.ui.updates import controller
        QTimer.singleShot(1500, lambda: controller().check(windows[0] if windows else None, automatic=True))
        QTimer.singleShot(3000, register_files_safely)
    return application.exec()
