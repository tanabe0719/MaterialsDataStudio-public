"""Installer lifecycle handling before importing the Qt application."""

from __future__ import annotations

import sys


def main() -> int:
    """Handle Velopack fast-exit hooks before any normal application startup."""
    hooks = {"--veloapp-install", "--veloapp-updated", "--veloapp-obsolete", "--veloapp-uninstall"}
    if sys.platform == "win32" and (
        getattr(sys, "frozen", False) or any(arg in hooks for arg in sys.argv[1:])
    ):
        import velopack

        from materials_data_studio.integrations.windows_files import unregister_document_types

        def unregister(_version: str) -> None:
            unregister_document_types()

        # No UpdateManager, remote source, downloads, or in-app update actions.
        # Only externally executed Setup.exe performs installation/upgrades.
        (
            velopack.App()
            .set_args(sys.argv[1:])
            .set_auto_apply_on_startup(False)
            .on_before_uninstall_fast_callback(unregister)
            .run()
        )

    from materials_data_studio.app import main as application_main

    return application_main()
