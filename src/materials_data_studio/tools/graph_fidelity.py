"""Semantic, artist-independent fidelity diagnostics; never infer full Igor parity.

The report is recomputed for the current revision. Unknown source commands remain
in graph.recreation_raw / immutable source, even when not editable or rendered.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from html import escape

import numpy as np

from materials_data_studio.tools.pxp_model import PxpDocument, PxpGraph


@dataclass(frozen=True)
class FidelityDiagnostic:
    code: str
    layer: str
    property: str
    message: str
    raw: str = ""
    severity: str = "warning"


@dataclass(frozen=True)
class FidelityReport:
    graph: str
    parsed: str
    preserved: str
    rendered: str
    editable: str
    exportable: str
    diagnostics: tuple[FidelityDiagnostic, ...]

    @property
    def summary(self) -> str:
        status = "Partial fidelity" if self.diagnostics else "Supported subset"
        return f"{status} · Parsed: {self.parsed} · Preserved: {self.preserved} · Rendered: {self.rendered} · Editable: {self.editable} · Exportable: {self.exportable}"

    @property
    def detail(self) -> str:
        return "\n".join(
            f"[{d.severity}: {d.code}] {d.layer} · {d.property}: {d.message}"
            + ("\nSource: " + d.raw if d.raw else "")
            for d in self.diagnostics
        )

    def to_dict(self):
        return asdict(self)

    @property
    def tooltip(self) -> str:
        return "<pre>" + escape(self.summary + "\n" + self.detail) + "</pre>"


def assess_graph(graph: PxpGraph, document: PxpDocument | None = None) -> FidelityReport:
    source_lines = graph.recreation_raw.splitlines()

    def excerpt(property):
        selected = [line for line in source_lines if str(property).casefold() in line.casefold()]
        # Full provenance stays in the canonical document; bounded tooltips must
        # not repeat a megabyte recreation for each unsupported property.
        return "\n".join(selected[:8])[:4096]

    diagnostics = [
        FidelityDiagnostic(
            i.code,
            i.layer,
            i.property or i.scope,
            i.message,
            i.raw or excerpt(i.property or i.scope),
            i.severity,
        )
        for i in graph.issues
    ]

    def note(
        code: str, property: str, message: str, layer: str = "renderer", severity: str = "warning"
    ):
        diagnostics.append(
            FidelityDiagnostic(code, layer, property, message, excerpt(property), severity)
        )

    models = {a.name: a for a in graph.axes}
    from materials_data_studio.tools.graph_typography import graph_font
    resolved_font = graph_font(graph)
    if resolved_font.fallback:
        note("igor.font.fallback", "gFont", f"Requested font unavailable; resolved to {resolved_font.family}")
    if graph.style.font_size == -1 or graph.style.marker_size == -1:
        note("igor.font.parent_unavailable", "gfSize", "Parent/subgraph defaults are unavailable; explicit fallback retained")
    if (graph.style.window_size and graph.style.automatic_font_size
            and graph.style.font_size in {None, 0} and not graph.style.relative_font_size
            and graph.style.window_size not in {(420, 300), (840, 600)}):
        note("igor.font.auto_estimate", "gfSize", resolved_font.provenance)
    readable_layers = 0
    if graph.window_kind == "Layout":
        note("igor.layout.subset", "Layout", "Single-page graph composition; native page metrics and other object kinds are outside this subset")
    for contour in graph.contours:
        wave = document.wave(contour.wave) if document else None
        if document and (wave is None or wave.data.ndim != 2 or wave.data.dtype.kind not in "biuf"):
            note("igor.contour.unrenderable", contour.wave, "Contour requires a readable real matrix", severity="error")
        else:
            readable_layers += 1
            note("igor.contour.interpolation", contour.wave, "Matrix contour uses MDS interpolation and automatic levels; native all-style equality is not claimed")
    if graph.host:
        note("igor.subwindow.subset", "HOST", "One-level graph inset; native guides and panel inheritance are outside this subset")
    for annotation in graph.annotations:
        if "\\{" in annotation.raw:
            note("igor.annotation.dynamic_expression", annotation.name, "Arbitrary dynamic annotation expressions are preserved but not executed")
    for trace in graph.traces:
        for name, sides in ((trace.x_axis, {"bottom", "top"}), (trace.axis, {"left", "right"})):
            model = models.get(name)
            if (model is None and name not in sides) or (
                model and model.side and model.side not in sides
            ):
                note(
                    "igor.axis.assignment",
                    name,
                    "Missing or incompatible axis assignment; rendering is not guaranteed",
                    "model",
                    "error",
                )
        if document is not None:
            if trace.box or trace.violin:
                distribution = trace.box or trace.violin
                assert distribution is not None
                waves = [document.wave(ref) for ref in distribution.waves]
                if not waves or any(w is None or w.data.ndim not in {1, 2} or w.data.dtype.kind not in "biuf" for w in waves):
                    note("igor.box.unrenderable", trace.name, "Missing/non-real Box Plot source", severity="error")
                else:
                    readable_layers += 1
                    note("igor.box.jitter", trace.name, "Statistical settings are retained; raw-point jitter and box geometry use deterministic MDS layout")
                continue
            y = document.wave(trace.y_wave)
            x = document.wave(trace.x_wave) if trace.x_wave else None
            if (
                y is None
                or y.data.ndim != 1
                or y.data.dtype.kind not in "biuf"
                or (
                    trace.x_wave
                    and (x is None or x.shape != y.shape or x.data.dtype.kind not in "biufSU")
                )
            ):
                note(
                    "igor.trace.unrenderable",
                    trace.name,
                    "Missing/non-real/mismatched XY wave; trace cannot be faithfully rendered",
                    severity="error",
                )
            else:
                readable_layers += 1
    for axis in graph.axes:

        def inherited(field, axis=axis):
            value = getattr(axis.style, field)
            return value if value is not None else getattr(graph.style.axis_defaults, field)

        if axis.style.date_info is not None:
            note("igor.axis.date_locale", axis.name, "Date labels use deterministic ISO/English formatting, not OS locale preferences")
        if axis.style.tick_count and not axis.style.manual_ticks and not axis.style.user_ticks:
            note("igor.axis.auto_locator", axis.name, "Automatic tick spacing is a deterministic MDS approximation; manual/user ticks preserve explicit positions")
        if axis.style.label_lateral_fraction is not None:
            note("igor.axis.legacy_label_fraction", axis.name, "Legacy MDS normalized label position is preserved; native export converts it using the current paper extent")
        if axis.free_position is not None and not (isinstance(axis.free_position, tuple) and axis.free_position[1] == "kwFraction" and 0 <= axis.free_position[0] <= 1):
            note(
                "igor.axis.free_position",
                axis.name,
                "Free-axis coordinates use a supported subset; cross-axis/default geometry may differ",
            )
    for image in graph.images:
        if "approximation" in image.lut_provenance and not any(d.code == "igor.image.palette_approximation" for d in diagnostics):
            note("igor.image.palette_approximation", image.wave, image.lut_provenance)
        if image.color_lut:
            lut = np.asarray(image.color_lut)
            if len(lut) > 65536 or lut.ndim != 2 or lut.shape[1] not in {3,4} or lut.dtype.kind not in "iuf" or not np.isfinite(lut).all() or np.any((lut < 0) | (lut > 65535)):
                note("igor.image.invalid_lut", image.wave, "Invalid RGB16 LUT; image/export is blocked", severity="error")
        if image.log_color and image.color_range and any(v is not None and v <= 0 for v in image.color_range):
            note("igor.image.log_limits", image.wave, "Logarithmic color limits must be positive", severity="error")
        if document is not None:
            wave = document.wave(image.wave)
            if wave is None or (wave.data.ndim != 2 and not (wave.data.ndim == 3 and wave.shape[2] in {3, 4})) or wave.data.dtype.kind not in "biuf":
                note(
                    "igor.image.unrenderable",
                    image.wave,
                    "Image requires a real numeric 2D wave",
                    severity="error",
                )
            else:
                readable_layers += 1
        if image.colorbar_visible:
            note(
                "igor.colorscale.geometry",
                image.wave,
                "ColorScale metrics/default positioning are approximate",
            )
    # No claim of pixel-perfect fonts/layout or all native flags, even when this
    # supported subset has no specific diagnostic.
    partial = bool(diagnostics)
    blocked = any(d.severity == "error" for d in diagnostics)
    render_blocked = blocked and (not readable_layers or any(
        d.code in {"igor.axis.assignment"} and d.severity == "error"
        for d in diagnostics))
    return FidelityReport(
        graph.name,
        "partial" if any(i.layer == "parser" for i in graph.issues) else "parsed",
        "semantic + raw source" if graph.recreation_raw else "semantic subset",
        "blocked" if render_blocked else "partial" if partial else "supported subset",
        "partial" if partial else "supported subset",
        "blocked" if blocked else "partial" if partial else "supported subset",
        tuple(diagnostics),
    )
