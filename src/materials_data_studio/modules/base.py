"""Common interface implemented by all analysis modules."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from PySide6.QtWidgets import QWidget


@runtime_checkable
class AnalysisModule(Protocol):
    """Contract used by the central module registry and main window."""

    module_id: str
    display_name: str

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the module's primary widget."""
        ...

    def serialize_state(self) -> dict[str, Any]:
        """Return JSON-compatible module state."""
        ...

    def restore_state(self, state: dict[str, Any]) -> None:
        """Restore module state."""
        ...

    def supported_extensions(self) -> tuple[str, ...]:
        """Return accepted file extensions in lower case."""
        ...
