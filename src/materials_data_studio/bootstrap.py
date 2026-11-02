"""Installer lifecycle handling before importing the Qt application."""

from __future__ import annotations

import os
import sys


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

    from materials_data_studio.core.startup_metrics import mark
    mark("bootstrap")
    # Two GUI workers already bound job concurrency. Bound nested BLAS pools
    # before NumPy/SciPy import; explicitly configured user limits take precedence.
    for variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(variable, "2")
    from materials_data_studio.app import main as application_main

    return application_main()
