"""Installer lifecycle handling before importing the Qt application."""

from __future__ import annotations

import os
import sys


def forward_before_gui() -> bool:
    """Attempt optional document delivery before importing the application shell."""
    from pathlib import Path
    paths = [Path(arg) for arg in sys.argv[1:] if not arg.startswith("-")]
    if not paths or "--new-instance" in sys.argv or any(path.suffix.lower() not in {".mdss", ".mdsg", ".mdsession"} for path in paths):
        return False
    from PySide6.QtCore import QCoreApplication
    from shiboken6 import delete

    from materials_data_studio.core.settings import create_settings
    if str(create_settings().value("documents/reuse_instance", "false")).lower() != "true":
        return False
    from materials_data_studio.integrations.document_open import forward_documents, runtime_path
    instance = QCoreApplication.instance()
    temporary = QCoreApplication(sys.argv) if instance is None else None
    try:
        return forward_documents(paths, runtime_path())
    finally:
        if temporary is not None:
            delete(temporary)


def main() -> int:
    """Handle Velopack fast-exit hooks before any normal application startup."""
    if sys.argv[1:2] == ["--mds-update-worker"]:
        from materials_data_studio.integrations.updates import worker_main
        return worker_main(sys.argv[2:])
    hooks = {"--veloapp-install", "--veloapp-updated", "--veloapp-obsolete", "--veloapp-uninstall"}
    if sys.platform == "win32" and (
        getattr(sys, "frozen", False) or any(arg in hooks for arg in sys.argv[1:])
    ):
        import velopack

        from materials_data_studio.integrations.windows_files import unregister_document_types

        def unregister(_version: str) -> None:
            unregister_document_types()

        # Downloads require consent in the GUI; never silently apply them on launch.
        (
            velopack.App()
            .set_args(sys.argv[1:])
            .set_auto_apply_on_startup(False)
            .on_before_uninstall_fast_callback(unregister)
            .run()
        )

    if forward_before_gui():
        return 0
    from materials_data_studio.core.startup_metrics import mark
    mark("bootstrap")
    # Two GUI workers already bound job concurrency. Bound nested BLAS pools
    # before NumPy/SciPy import; explicitly configured user limits take precedence.
    for variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(variable, "2")
    from materials_data_studio.app import main as application_main

    return application_main()
