"""Igor's numbered geometric marker shapes (0–62), in marker coordinates."""

from __future__ import annotations

import math

from matplotlib.markers import MarkerStyle
from matplotlib.path import Path
from matplotlib.transforms import Affine2D

from materials_data_studio.tools.pxp_style import MARKERS


def _polygon(vertices: list[tuple[float, float]]) -> Path:
    return Path([*vertices, vertices[0]], [Path.MOVETO, *([Path.LINETO] * (len(vertices) - 1)), Path.CLOSEPOLY])


def _lines(*segments: tuple[tuple[float, float], tuple[float, float]]) -> Path:
    return Path([point for segment in segments for point in segment],
                [code for _segment in segments for code in (Path.MOVETO, Path.LINETO)])


def _shape(symbol: str) -> Path:
    style = MarkerStyle(symbol)
    return style.get_path().transformed(style.get_transform())


def marker_path(number: int | None) -> str | Path:
    """Use native shapes where exact, otherwise construct the documented shape."""
    number = number or 0
    if number in MARKERS and number not in {2, 25, 26, 28, 29}:
        return MARKERS[number]
    plus = _lines(((-0.5, 0), (0.5, 0)), ((0, -0.5), (0, 0.5)))
    cross = _lines(((-0.5, -0.5), (0.5, 0.5)), ((-0.5, 0.5), (0.5, -0.5)))
    dot = Path.unit_circle().transformed(MarkerStyle(".").get_transform()).transformed(
        # Match the small central square dot without changing the outside bounds.
        Affine2D().scale(0.25)
    )
    if number == 2:
        return Path.make_compound_path(plus, cross)
    if number in {3, 14}:
        return _polygon([(-0.5, -0.5), (0.5, 0.5), (-0.5, 0.5), (0.5, -0.5)])
    if number in {4, 15}:
        return _polygon([(-0.5, -0.5), (0.5, 0.5), (0.5, -0.5), (-0.5, 0.5)])
    if number in {11, 12, 13, 24, 27, 30, 40, 41, 42, 43, 44, 47, 50, 53, 56}:
        base = {11: "s", 12: "s", 13: "s", 24: "v", 40: "D", 41: "o",
                42: "o", 43: "o", 44: "^", 47: "<", 50: ">", 53: "p", 56: "h"}
        outline = marker_path(25 if number == 27 else 28) if number in {27, 30} else _shape(base[number])
        assert isinstance(outline, Path)
        detail = plus if number in {11, 42} else cross if number in {12, 43} else dot
        return Path.make_compound_path(outline, detail)
    if number in {20, 21}:
        return _lines(((-0.5, -0.5 if number == 20 else 0.5),
                       (0.5, 0.5 if number == 20 else -0.5)))
    if number in {25, 26, 28, 29}:
        sx, sy = (0.5, 0.25) if number in {25, 26} else (0.25, 0.5)
        return _polygon([(-sx, 0), (0, sy), (sx, 0), (0, -sy)])
    if number in range(31, 39):
        triangles = [[(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5)],
                     [(-0.5, -0.5), (0.5, -0.5), (-0.5, 0.5)],
                     [(-0.5, 0.5), (0.5, 0.5), (0.5, -0.5)],
                     [(-0.5, 0.5), (0.5, 0.5), (-0.5, -0.5)]]
        return _polygon(triangles[(number - 31) // 2])
    if number == 39:
        return _lines(((-0.5, -0.25), (0.5, -0.25)), ((-0.5, 0.25), (0.5, 0.25)),
                      ((-0.25, -0.5), (-0.25, 0.5)), ((0.25, -0.5), (0.25, 0.5)))
    if number in {57, 58, 59, 60, 61, 62}:
        count = 3 if number in {57, 58} else 4
        offset = math.pi / 2 if number < 61 else math.pi / 4
        return _polygon([(r * math.cos(offset + i * math.pi / count),
                          r * math.sin(offset + i * math.pi / count))
                         for i in range(count * 2) for r in [0.5 if i % 2 == 0 else 0.16]])
    return "o"
