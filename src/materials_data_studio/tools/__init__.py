"""Independent laboratory utilities; UI imports remain lazy on startup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from materials_data_studio.tools.launcher import ToolMenuManager

__all__ = ["ToolMenuManager"]


def __getattr__(name: str) -> Any:
    if name == "ToolMenuManager":
        from materials_data_studio.tools.launcher import ToolMenuManager

        return ToolMenuManager
    raise AttributeError(name)
