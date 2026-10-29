"""Safe, run-based Igor label typography, rendered with the host font metrics."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from matplotlib.lines import Line2D
from matplotlib.text import Text
from matplotlib.transforms import Bbox, IdentityTransform

from materials_data_studio.tools.pxp_recreation import formatted_text
from materials_data_studio.tools.pxp_style import igor_rgb

_ESCAPE = re.compile(r"\\(F'([^']*)'|Z(\d{1,2})|f(\d{1,2})|K\(([^)]*)\)|Zr\d{3}|\]0|[BSM]|s\([^)]*\)|s[ab][+-]\d{2}|u(?:#[12])?)")


@dataclass(frozen=True)
class TextRun:
    text: str
    family: str
    size: float
    style: int
    baseline: float = 0
    color: Any = "black"
    symbol: str | None = None
    space_below: float = 0


def text_runs(raw: str, family: str, size: float, style: int = 0,
              color: Any = "black", scale: float = 1) -> list[TextRun]:
    """Decode only documented typography escapes; no evaluation/interpolation."""
    raw = raw.replace("\\\\", "\\").replace("\\r\\n", "\n").replace("\\r", "\n").replace("\\n", "\n")
    runs: list[TextRun] = []
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    main_size, baseline, script, start = size, 0.0, False, 0
    space_below = 0.0
    original = (family, size, style, color)
    for match in _ESCAPE.finditer(raw):
        if match.start() > start:
            runs.append(TextRun(raw[start:match.start()], family, size, style, baseline, color,
                                space_below=space_below))
        code = match[1]
        if code.startswith("F"):
            # Unicode Greek glyphs cannot be rendered with the legacy Symbol encoding.
            family = family if match[2] == "Symbol" else match[2]
        elif code.startswith("Zr"):
            main_size = original[1] * int(code[2:]) / 100
            size = main_size * (0.7 if script else 1)
        elif code == "]0":
            family, size, style, color = original
            main_size, baseline, script = size, 0.0, False
        elif code.startswith("Z"):
            main_size = float(match[3]) * scale
            size = main_size * (0.7 if script else 1)
        elif code.startswith("f"):
            style = int(match[4])
        elif code.startswith("K"):
            try:
                components = tuple(int(value) for value in match[5].split(","))
                if len(components) == 3:
                    color = igor_rgb(components)  # type: ignore[arg-type]
            except ValueError:
                pass
        elif code in {"B", "S"}:
            script = True
            baseline = main_size * (-0.25 if code == "B" else 0.45)
            size = main_size * 0.7
        elif code == "M":
            size, baseline, script = main_size, 0.0, False
        elif code.startswith("s("):
            runs.append(TextRun("", family, size, style, baseline, color, code[2:-1], space_below))
        elif code.startswith("sb"):
            space_below = float(code[2:]) / 2 * scale
        start = match.end()
    if start < len(raw):
        runs.append(TextRun(raw[start:], family, size, style, baseline, color, space_below=space_below))
    return runs


class IgorText(Text):
    """A Text label with independently styled runs and normal axis positioning."""

    def __init__(self, original: Text, raw: str, style: int = 0) -> None:
        super().__init__()
        self.update_from(original)
        self.set_position(original.get_position())
        self.set_rotation(original.get_rotation())
        self.set_text(formatted_text(raw))
        self.raw = raw
        self.igor_style = style
        self.font_scale = 1.0
        self.display_offset = (0.0, 0.0)
        self.symbol_handles: dict[str, Any] = {}
        self.line_spacing = 0.0
        self.symbol_width = 0.0
        self.symbol_size = 0.0
        self.symbol_min_width = 0.0
        self.symbol_max_width = 10.0
        self._metrics_key: tuple[Any, ...] | None = None
        self._metrics_value: tuple[Any, ...] | None = None

    def _metrics(self, renderer: Any) -> tuple[Any, ...]:
        """Cache local text geometry, never positions, transforms or trace styles."""
        family = self.get_fontfamily()[0]
        key = (renderer, self.raw, family, self.get_fontsize(), self.igor_style,
               repr(self.get_color()), self.font_scale, self.line_spacing, self.symbol_width,
               self.symbol_size, self.symbol_min_width, self.symbol_max_width)
        if self._metrics_key == key and self._metrics_value is not None:
            return self._metrics_value
        runs = text_runs(self.raw, family, float(self.get_fontsize()), self.igor_style,
                         self.get_color(), self.font_scale)
        # Metrics measured in display pixels; rotation is applied to the whole line.
        lines: list[list[Any]] = [[]]
        pp = renderer.points_to_pixels(1)
        for run in runs:
            for index, segment in enumerate(run.text.split("\n")):
                if index:
                    lines.append([])
                part = Text(text=segment, fontfamily=run.family, fontsize=run.size,
                            fontstyle="italic" if run.style & 2 else "normal",
                            fontweight="bold" if run.style & 1 else "normal",
                            color=run.color, ha="left", va="baseline", transform=IdentityTransform())
                if "∘" in segment and run.family == "Arial":
                    part.set_fontfamily(["Arial", "DejaVu Sans"])
                figure = self.get_figure()
                if figure is not None:
                    part.set_figure(figure)
                box = part.get_window_extent(renderer)
                length = (self.symbol_width or run.size * 2) * pp if run.symbol else box.width
                lines[-1].append((run, part, length, box.y0, box.y1))
        width = max((sum(item[2] for item in line) for line in lines), default=0)
        line_y = [0.0]
        for line in lines[:-1]:
            height = max((item[0].size * 1.15 + item[0].space_below for item in line),
                         default=float(self.get_fontsize()) * 1.15)
            line_y.append(line_y[-1] - (height + self.line_spacing) * pp)
        low = min((item[3] + item[0].baseline * pp + y for line, y in zip(lines, line_y, strict=True)
                   for item in line), default=0)
        high = max((item[4] + item[0].baseline * pp + y for line, y in zip(lines, line_y, strict=True)
                    for item in line), default=0)
        self._metrics_key = key
        self._metrics_value = lines, width, line_y, low, high
        return self._metrics_value

    def _pieces(self, renderer: Any) -> list[Any]:
        lines, width, line_y, low, high = self._metrics(renderer)
        pp = renderer.points_to_pixels(1)
        left = {"left": 0, "center": -width / 2, "right": -width}.get(self.get_horizontalalignment(), 0)
        dy = {"top": -high, "bottom": -low, "center": -(low + high) / 2}.get(self.get_verticalalignment(), 0)
        anchor = self.get_transform().transform(self.get_position()) + self.display_offset
        angle = math.radians(float(self.get_rotation()))
        pieces = []
        for line, line_offset in zip(lines, line_y, strict=True):
            dx = left
            for run, part, length, _low, _high in line:
                y = dy + line_offset + run.baseline * pp
                position = (anchor[0] + dx * math.cos(angle) - y * math.sin(angle),
                            anchor[1] + dx * math.sin(angle) + y * math.cos(angle))
                if run.symbol and run.symbol in self.symbol_handles:
                    source = self.symbol_handles[run.symbol]
                    symbol = Line2D([], [])
                    symbol.update_from(source)
                    symbol.set_transform(IdentityTransform())
                    figure = self.get_figure()
                    if figure is not None:
                        symbol.set_figure(figure)
                    symbol.set_clip_on(False)
                    if self.symbol_size:
                        symbol.set_markersize(self.symbol_size)
                    symbol.set_linewidth(min(self.symbol_max_width, max(self.symbol_min_width, symbol.get_linewidth())))
                    center = position[1] + run.size * pp * 0.35
                    symbol.set_data([position[0], position[0] + length * 0.45, position[0] + length * 0.9],
                                    [center] * 3)
                    symbol.set_markevery([1])
                    pieces.append(symbol)
                elif not run.symbol:
                    part.set_position(position)
                    part.set_rotation(self.get_rotation())
                    part.set_rotation_mode("anchor")
                    pieces.append(part)
                dx += length
        return pieces

    def draw(self, renderer: Any) -> None:
        if self.get_visible():
            for piece in self._pieces(renderer):
                piece.draw(renderer)
        self.stale = False

    def get_window_extent(self, renderer: Any = None, dpi: Any = None) -> Bbox:
        if renderer is None:
            return super().get_window_extent(renderer, dpi)
        boxes = [piece.get_window_extent(renderer) for piece in self._pieces(renderer)]
        return Bbox.union(boxes) if boxes else Bbox.from_bounds(0, 0, 0, 0)
