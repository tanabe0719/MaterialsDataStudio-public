"""Parser-independent, immutable-by-convention PXP document model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray

PXP_SCHEMA_VERSION = 15
PxpStatus = Literal["OPEN_FAILED", "PARTIAL", "DATA_ONLY", "GRAPH_PARTIAL", "FULL"]


@dataclass(frozen=True, slots=True)
class PxpIssue:
    scope: str
    message: str
    code: str = "igor.parser.unsupported"
    layer: str = "parser"
    property: str = ""
    raw: str = ""
    severity: str = "warning"


@dataclass(frozen=True, slots=True)
class PxpWave:
    id: str
    igor_path: str
    name: str
    dtype: str
    shape: tuple[int, ...]
    data: NDArray[np.generic]
    dimension_scaling: tuple[tuple[float, float], ...]
    dimension_units: tuple[str, ...]
    data_units: str
    wave_note: str
    dimension_labels: tuple[tuple[str, ...], ...] = ()
    dimension_names: tuple[str, ...] = ()

    def coordinate(self, dimension: int) -> NDArray[np.float64]:
        offset, delta = self.dimension_scaling[dimension]
        return offset + delta * np.arange(self.shape[dimension], dtype=np.float64)


@dataclass(frozen=True, slots=True)
class PxpTrace:
    name: str
    y_wave: str
    x_wave: str | None = None
    axis: str = "left"
    mode: int | None = None
    marker: int | None = None
    marker_size: float | None = None
    line_width: float | None = None
    color_igor: tuple[int, int, int] | None = None
    marker_fill_igor: tuple[int, int, int] | None = None
    marker_stroke_igor: tuple[int, int, int] | None = None
    marker_thickness: float | None = None
    opaque: bool = False
    use_marker_stroke: bool = False
    line_style: int = 0
    error_low: str | None = None
    error_high: str | None = None
    offset: tuple[float, float] = (0, 0)
    multiplier: tuple[float, float] = (0, 0)
    line_cap: int = 0
    line_join: int = 0
    error_axis: str = "Y"
    x_axis: str = "bottom"
    z_color: tuple[str, float | None, float | None, str, int, str] | None = None
    log_z_color: bool = False
    z_size: tuple[str, float | None, float | None, float, float, str] | None = None
    z_marker: str | None = None
    color_scale: bool = False
    box: PxpBoxPlot | None = None
    violin: PxpViolinPlot | None = None
    point_range: tuple[int, int] | None = None
    x_error_low: str | None = None
    x_error_high: str | None = None
    error_color: tuple[int, int, int] | None = None
    error_width: float = .9
    error_cap: float = 2
    error_cap_thickness: float = 1
    error_clip: bool = True


@dataclass(frozen=True, slots=True)
class PxpBoxPlot:
    waves: tuple[str, ...] = ()
    vertical: bool = False
    category_labels: bool = False
    notched: bool = False
    quartile_method: int = 0
    whisker_method: tuple[float, ...] = (0,)
    outlier_method: tuple[float, ...] = (0,)
    show_data: str = "All"
    show_mean: bool = False
    show_fences: bool = False
    median_is_marker: bool = False
    box_width: float | None = None
    cap_width: float = 0
    jitter: float = .7
    box_fill: tuple[int, ...] | None = None
    box_color: tuple[int, ...] = (0, 0, 0)
    median_color: tuple[int, ...] = (0, 0, 0)
    whisker_color: tuple[int, ...] = (0, 0, 0)


@dataclass(frozen=True, slots=True)
class PxpViolinPlot:
    waves: tuple[str, ...] = ()
    vertical: bool = False
    category_labels: bool = False
    bandwidth: float = 0
    bandwidth_method: int = 1
    kernel: int = 5
    extension: float = 1
    max_density: float = 0
    side: int = 0
    close_outline: bool = False
    box_width: float | None = None
    show_data: bool = True
    show_mean: bool = False
    show_median: bool = False
    jitter: float = .5
    line_color: tuple[int, ...] = (0, 0, 0)
    fill_color: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class PxpCursor:
    name: str
    trace: str
    point: int
    color: tuple[int, int, int] = (65535, 0, 0)


@dataclass(frozen=True, slots=True)
class PxpContour:
    wave: str
    levels: tuple[float, ...] = ()
    filled: bool = False
    color_table: str = "viridis"
    auto_levels: bool = True


@dataclass(frozen=True, slots=True)
class PxpLayoutItem:
    graph: str
    rect: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class PxpAxisStyle:
    tick: int | None = None
    mirror: int | None = None
    thickness: float | None = None
    major_length: float | None = None
    minor_length: float | None = None
    major_thickness: float | None = None
    minor_thickness: float | None = None
    medium_length: float | None = None
    medium_thickness: float | None = None
    minor_enabled: bool | None = None
    manual_ticks: tuple[float, float, int, int] | None = None
    manual_minor: tuple[int, int] | None = None
    log: int | None = None
    grid: int | None = None
    font_family: str | None = None
    font_size: float | None = None
    font_style: int | None = None
    label_margin: float | None = None
    label_rotation: float | None = None
    tick_rotation: float | None = None
    no_label: int | None = None
    tick_unit: int | None = None
    trim_trailing_zero: bool | None = None
    reverse: bool | None = None
    axis_color: tuple[int, int, int] | None = None
    label_color: tuple[int, int, int] | None = None
    tick_color: tuple[int, int, int] | None = None
    tick_label_color: tuple[int, int, int] | None = None
    grid_color: tuple[int, int, int] | None = None
    tick_count: int | None = None
    tick_separation: float | None = None
    user_ticks: tuple[str, str] | None = None
    axis_offset: float | None = None
    label_position: float | None = None
    label_position_mode: int | None = None
    label_lateral_position: float | None = None
    standoff: bool | None = None
    zero_line: int | None = None
    grid_hair: int | None = None
    low_trip: float | None = None
    high_trip: float | None = None
    notation: int | None = None
    zero_is_zero: bool | None = None
    tick_enable: tuple[float, float] | None = None
    major_tick_color: tuple[int, int, int] | None = None
    minor_tick_color: tuple[int, int, int] | None = None
    subminor_length: float | None = None
    subminor_thickness: float | None = None
    tick_zap: tuple[float, ...] | None = None
    grid_style: int | None = None
    label_line_spacing: float | None = None
    label_lateral_fraction: float | None = None
    date_info: tuple[int, int, int] | None = None


@dataclass(frozen=True, slots=True)
class PxpAxis:
    name: str
    label_raw: str = ""
    limits: tuple[float, float] | None = None
    enable: tuple[float, float] | None = None
    style: PxpAxisStyle = field(default_factory=PxpAxisStyle)
    side: str = ""
    free_position: float | tuple[float, str] | None = None


@dataclass(frozen=True, slots=True)
class PxpGraphStyle:
    width_points: float | None = None
    height_points: float | None = None
    width_aspect: float | None = None
    height_aspect: float | None = None
    margins: tuple[tuple[str, float], ...] = ()
    axis_defaults: PxpAxisStyle = field(default_factory=PxpAxisStyle)
    plot_background: tuple[int, int, int] | None = None
    window_size: tuple[float, float] | None = None
    dash_patterns: tuple[tuple[int, tuple[float, ...]], ...] = ()
    font_size: float | None = None
    automatic_font_size: float | None = None
    font_family: str | None = None
    relative_font_size: float | None = None
    font_multiplier: float = 100
    marker_size: float | None = None
    font_provenance: str = "MDS default"


@dataclass(frozen=True, slots=True)
class PxpAnnotationStyle:
    anchor: str = "MC"
    x_percent: float = 0
    y_percent: float = 0
    frame: int = 2
    transparent: bool = False
    color: tuple[int, int, int] | None = None
    font_size: float = 12
    width: float = 15
    height: float = 0
    height_percent: float = 75
    width_percent: float = 0
    vertical: bool = True
    log_axis: bool = False
    frame_width: float = 0.5
    frame_color: tuple[int, int, int] = (0, 0, 0)
    background_color: tuple[int, int, int] = (65535, 65535, 65535)
    padding: float = 0
    shadow: float = 0
    line_spacing: float = 0
    symbol_width: float = 0
    symbol_size: float = 0
    symbol_min_width: float = 0
    symbol_max_width: float = 10
    offset_mode: str = "legacy"
    frame_style: int = 0
    background_mode: int = 0
    exterior: int = 0
    rotation: float = 0
    inherit_graph_font: bool = False
    padding_explicit: bool = False


@dataclass(frozen=True, slots=True)
class PxpImage:
    wave: str
    color_table: str = ""
    color_range: tuple[float | None, float | None] | None = None
    reverse: bool = False
    log_color: bool = False
    colorbar_visible: bool = False
    colorbar_label_raw: str = ""
    hidden: bool = False
    min_color: tuple[int, int, int] | str | None = None
    max_color: tuple[int, int, int] | str | None = None
    interpolation: bool = False
    colorbar_style: PxpAnnotationStyle = field(default_factory=PxpAnnotationStyle)
    color_lut: tuple[tuple[int, ...], ...] = ()
    lut_provenance: str = ""
    nan_color: tuple[int, int, int] | str | None = "transparent"
    lut_name: str = ""
    x_wave: str | None = None
    y_wave: str | None = None
    colorbar_axis: PxpAxisStyle = field(default_factory=PxpAxisStyle)


@dataclass(frozen=True, slots=True)
class PxpAnnotation:
    name: str
    raw: str
    style: PxpAnnotationStyle = field(default_factory=PxpAnnotationStyle)
    trace: str = ""
    point: int | None = None
    arrow: int = 0


@dataclass(frozen=True, slots=True)
class PxpDrawing:
    kind: str
    coordinates: tuple[float, ...]
    text: str = ""
    xcoord: str = "prel"
    ycoord: str = "prel"
    arrow: int = 0
    arrow_length: float = 10
    arrow_fat: float = 0.5
    line_width: float = 1
    color: tuple[int, int, int] = (0, 0, 0)
    font_size: float = 12
    font_style: int = 0
    layer: str = "UserFront"
    font_family: str = "Arial"
    rotation: float = 0
    anchor: str = "LB"
    line_style: int = 0
    line_cap: int = 0
    line_join: int = 0
    fill_color: tuple[int, int, int] = (65535, 65535, 65535)
    fill_mode: int = 0
    round_radius: float = 0.1


@dataclass(frozen=True, slots=True)
class PxpGraph:
    name: str
    traces: tuple[PxpTrace, ...] = ()
    axes: tuple[PxpAxis, ...] = ()
    images: tuple[PxpImage, ...] = ()
    legend_raw: str = ""
    swap_xy: bool = False
    issues: tuple[PxpIssue, ...] = ()
    status: str = "Fully supported"
    style: PxpGraphStyle = field(default_factory=PxpGraphStyle)
    legend_style: PxpAnnotationStyle = field(default_factory=PxpAnnotationStyle)
    annotations: tuple[PxpAnnotation, ...] = ()
    drawings: tuple[PxpDrawing, ...] = ()
    recreation_raw: str = ""
    cursors: tuple[PxpCursor, ...] = ()
    contours: tuple[PxpContour, ...] = ()
    layout_items: tuple[PxpLayoutItem, ...] = ()
    window_kind: str = "Graph"
    host: str = ""
    host_rect: tuple[float, float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class PxpTable:
    name: str
    columns: tuple[str, ...] = ()
    widths: tuple[tuple[str, float], ...] = ()
    column_styles: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PxpDocument:
    source_id: str
    basename: str
    sha256: str
    size: int
    igor_version: str
    backend: str
    backend_version: str
    waves: tuple[PxpWave, ...] = ()
    folders: tuple[str, ...] = ("root",)
    graphs: tuple[PxpGraph, ...] = ()
    tables: tuple[str, ...] = ()
    procedures: tuple[str, ...] = ()
    issues: tuple[PxpIssue, ...] = ()
    status: PxpStatus = "OPEN_FAILED"
    source_bytes: bytes = field(default=b"", repr=False, compare=False)
    recreation_raw: str = ""
    table_definitions: tuple[PxpTable, ...] = ()
    command_history: tuple[str, ...] = ()
    _wave_index: dict[str, PxpWave | None] | None = field(default=None, init=False, repr=False, compare=False)

    def wave(self, reference: str) -> PxpWave | None:
        reference = reference.strip().strip("'")
        path = reference if reference.startswith("root:") else f"root:{reference}"
        index = self._wave_index
        if index is None:
            index = dict[str, PxpWave | None]((wave.igor_path, wave) for wave in self.waves)
            for wave in self.waves:
                index[wave.name] = None if wave.name in index else wave
            # Waves are immutable. dataclasses.replace creates a new, unindexed
            # revision, so edits cannot leave stale names or data in this cache.
            object.__setattr__(self, "_wave_index", index)
        return index.get(path) or index.get(reference)
