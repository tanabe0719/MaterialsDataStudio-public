"""Consistent, editing-friendly numeric input controls."""

from __future__ import annotations

import math

from PySide6.QtWidgets import QDoubleSpinBox


class FlexibleDoubleSpinBox(QDoubleSpinBox):
    """A double spin box that preserves in-progress edits and avoids padded zeros.

    Qt's default keyboard tracking emits a value after nearly every keystroke.
    Controls that apply constraints in response can then rewrite their own text,
    moving the caret and turning an insertion into an apparent replacement.  This
    shared control commits typed text on Enter or focus loss while programmatic
    ``setValue`` calls and arrow/step operations remain immediate.
    """

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setKeyboardTracking(False)

    def textFromValue(self, value: float) -> str:
        """Show useful precision without a distracting fixed zero suffix."""
        if not math.isfinite(value):
            return super().textFromValue(value)
        rendered = f"{value:.{self.decimals()}f}"
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return "0" if rendered in {"-0", ""} else rendered
