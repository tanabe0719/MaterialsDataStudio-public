"""Resolve graph, axis and annotation fonts separately, with provenance."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from matplotlib import font_manager


@dataclass(frozen=True)
class ResolvedFont:
    family: str
    size: float
    provenance: str
    fallback: bool = False


@lru_cache(maxsize=256)
def font_family(requested: str) -> tuple[str, bool]:
    path = font_manager.findfont(font_manager.FontProperties(family=requested), fallback_to_default=True)
    actual = font_manager.FontProperties(fname=path).get_name()
    return actual, actual.casefold() != requested.casefold()


def graph_font(graph, *, axis_size: float | None = None, family: str | None = None) -> ResolvedFont:
    style = graph.style
    size = style.font_size
    provenance = "explicit gfSize"
    if size is None or size <= 0:
        size = style.automatic_font_size or 10
        provenance = style.font_provenance if style.automatic_font_size else "MDS fallback (native auto metrics unavailable)"
        if style.window_size and style.automatic_font_size is not None:
            size = max(10, round(min(style.window_size) * .03))
            provenance = "Igor 10.03 auto subset: 420x300=10 pt, 840x600=18 pt; other geometries estimated"
        if style.relative_font_size:
            width, height = style.window_size or (420, 300)
            size = min(width, height) * style.relative_font_size / 100
            provenance = "gfRelSize percentage of shorter graph dimension (supported subset)"
    if axis_size is not None and axis_size > 0:
        size, provenance = axis_size, "explicit axis fSize"
    requested = family or style.font_family or style.axis_defaults.font_family or "Arial"
    resolved, fallback = font_family(requested)
    return ResolvedFont(resolved, max(1, size * style.font_multiplier / 100), provenance, fallback)


def annotation_font(graph, style) -> ResolvedFont:
    resolved = graph_font(graph)
    if style.inherit_graph_font or style.font_size <= 0:
        return resolved
    return ResolvedFont(resolved.family, style.font_size * graph.style.font_multiplier / 100, "explicit annotation font", resolved.fallback)


def marker_size(graph, explicit: float | None) -> float:
    if explicit is not None and explicit > 0:
        return explicit * graph.style.font_multiplier / 100
    if graph.style.marker_size is not None and graph.style.marker_size > 0:
        return graph.style.marker_size * graph.style.font_multiplier / 100
    return graph_font(graph).size / 3
