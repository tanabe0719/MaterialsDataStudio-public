"""Ordered color-gradient palettes for dataset comparison plots."""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QColor

PALETTE_STOPS: dict[str, tuple[str, ...]] = {
    "Viridis": ("#440154", "#3B528B", "#21918C", "#5EC962", "#FDE725"),
    "Plasma": ("#0D0887", "#7E03A8", "#CC4778", "#F89540", "#F0F921"),
    "Ocean": ("#081D58", "#225EA8", "#1D91C0", "#41B6C4", "#C7E9B4"),
    "Blue–Red": ("#2166AC", "#67A9CF", "#F7F7F7", "#EF8A62", "#B2182B"),
    "Warm": ("#7F0000", "#D7301F", "#FC8D59", "#FEC44F", "#FFF7BC"),
}


def gradient_colors(stops: tuple[str, ...], count: int) -> list[str]:
    """Interpolate RGB stops into exactly ``count`` ordered colors."""
    if count < 0:
        raise ValueError("Color count must not be negative.")
    if count == 0:
        return []
    if not stops:
        raise ValueError("At least one gradient stop is required.")
    if count == 1:
        return [QColor(stops[0]).name()]
    positions = np.linspace(0.0, len(stops) - 1, count)
    result: list[str] = []
    colors = [QColor(value) for value in stops]
    if not all(color.isValid() for color in colors):
        raise ValueError("Gradient stops must be valid colors.")
    for position in positions:
        lower = min(int(position), len(colors) - 1)
        upper = min(lower + 1, len(colors) - 1)
        fraction = position - lower
        red = round(colors[lower].red() * (1 - fraction) + colors[upper].red() * fraction)
        green = round(
            colors[lower].green() * (1 - fraction)
            + colors[upper].green() * fraction
        )
        blue = round(
            colors[lower].blue() * (1 - fraction)
            + colors[upper].blue() * fraction
        )
        result.append(QColor(red, green, blue).name())
    return result
