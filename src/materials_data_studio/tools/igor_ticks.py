"""Explicit bounded tick locators; manual placement is separate from visibility."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from matplotlib.ticker import Formatter, Locator


class IgorLinearLocator(Locator):
    """Documented approximation of native auto ticks, deterministic after zoom."""
    def __init__(self, count: int = 5) -> None:
        super().__init__()
        self.count = max(2, min(int(count), 1000))

    def __call__(self) -> Any:
        axis: Any = self.axis
        return self.tick_values(*axis.get_view_interval())

    def tick_values(self, vmin: float, vmax: float) -> Any:
        low, high = sorted((vmin, vmax))
        if not np.isfinite((low, high)).all() or high <= low:
            return np.array([])
        raw = (high-low) / max(1, self.count-1)
        power = 10**math.floor(math.log10(raw))
        step = min((1, 2, 2.5, 5, 10), key=lambda n: abs(math.log(n*power/raw))) * power
        first, last = math.ceil(low/step-1e-10), math.floor(high/step+1e-10)
        return np.arange(first, last+1, dtype=float) * step


class ZapFormatter(Formatter):
    def __init__(self, source: Formatter, values: tuple[float, ...]) -> None:
        self.source, self.values = source, values

    def set_axis(self, axis: Any) -> None:
        super().set_axis(axis)
        self.source.set_axis(axis)

    def set_locs(self, locs: Any) -> None:
        self.source.set_locs(locs)

    def __call__(self, value: float, pos: int | None = None) -> str:
        return "" if value in self.values else self.source(value, pos)

    def get_offset(self) -> str:
        return self.source.get_offset()


class IgorLogMinorLocator(Locator):
    def __init__(self, style: Any, scale: float) -> None:
        super().__init__()
        self.style, self.scale = style, scale

    def __call__(self) -> Any:
        axis: Any = self.axis
        low, high = sorted(axis.get_view_interval())
        if low <= 0 or not np.isfinite((low, high)).all():
            return np.array([])
        base = 2 if self.style.log == 2 else 10
        start, end = math.floor(math.log(low, base)), math.ceil(math.log(high, base))
        if end-start > 12:
            return np.array([])
        tiny = base == 10 and math.log10(high/low) < 1
        values, kinds = [], []
        for exponent in range(start, end+1):
            with np.errstate(over="ignore", under="ignore"):
                power = float(np.power(float(base), float(exponent)))
            if not np.isfinite(power) or power == 0:
                continue
            multipliers = np.arange(11, 100) / 10 if tiny else range(2, base)
            for multiplier in multipliers:
                value = float(multiplier * power)
                if low <= value <= high:
                    values.append(value)
                    kinds.append("minor" if float(multiplier).is_integer() else "subminor")
        # sep is a display filter, never an alteration of the semantic tick grid.
        transform = axis.get_transform()
        size = axis.axes.bbox.width if axis.axis_name == "x" else axis.axes.bbox.height
        span = abs(float(transform.transform(high))-float(transform.transform(low)))
        previous = -np.inf
        filtered, types = [], []
        for value, kind in zip(values, kinds, strict=True):
            position = float(transform.transform(value)) * size / max(span, 1e-30)
            if kind == "minor" or abs(position-previous) >= (self.style.tick_separation or 3):
                filtered.append(value)
                types.append(kind)
                previous = position
        self.kinds = dict(zip(filtered, types, strict=True))
        self.style_ticks(filtered)
        return np.asarray(filtered)

    def style_ticks(self, values: Any) -> None:
        axis: Any = self.axis
        for tick, value in zip(axis.get_minor_ticks(len(values)), values, strict=True):
            kind = self.kinds.get(float(value), "minor")
            tiny_tick = kind == "subminor"
            length = self.style.subminor_length if tiny_tick else self.style.minor_length
            width = self.style.subminor_thickness if tiny_tick else self.style.minor_thickness
            length = (1.5 if tiny_tick else 3) if length is None else length
            width = (self.style.thickness if self.style.thickness is not None else .5) if width is None else width
            for line in (tick.tick1line, tick.tick2line):
                line.set_markersize(0 if self.style.tick == 3 else length*self.scale)
                line.set_markeredgewidth(width*self.scale)
