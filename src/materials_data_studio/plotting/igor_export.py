"""Qt-independent graph state used by Igor Pro experiment export."""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from materials_data_studio.core.models import PlotStyle
from materials_data_studio.plotting.plot_spec import PlotSpec

_UNIT_SUFFIX = re.compile(r"^(?P<label>.*?)\s*\((?P<unit>[^()]*)\)\s*$")


@dataclass(slots=True)
class GraphExportTrace:
    """One visible, editable Igor XY trace."""

    x: NDArray[np.generic]
    y: NDArray[np.generic]
    name: str
    style: PlotStyle = field(default_factory=PlotStyle)
    role: str = "trace"
    x_unit: str = ""
    y_unit: str = ""

    def __post_init__(self) -> None:
        self.x = np.asarray(self.x)
        self.y = np.asarray(self.y)
        if self.x.ndim != 1 or self.y.ndim != 1:
            raise ValueError("Igor graph export currently supports one-dimensional waves only.")
        if self.x.shape != self.y.shape:
            raise ValueError("Igor graph x and y arrays must have the same shape.")


@dataclass(slots=True)
class GraphExportWave:
    """A checked dataset column; stored without adding it to the graph."""
    name: str
    values: NDArray[np.generic]
    folder: str = "Data"
    unit: str = ""
    note: str = ""


@dataclass(slots=True)
class GraphExportText:
    trace: int
    point: int
    text: str
    font: str = "Arial"
    size: float = 10.
    color: str = "#334155"
    angle: float = 90.
    gap: float = 4.


def label_anchor(angle: float) -> str:
    # Native rotated annotations choose their equivalent anchor at the nearest
    # quarter turn. Keep the text's left-center start fixed at every angle.
    return ("LC", "MB", "RC", "MT")[int(np.floor((angle % 360 + 45) / 90)) % 4]


def point_texts(spec: PlotSpec) -> list[GraphExportText]:
    if not any(curve.point_labels for curve in spec.curves):
        return []
    from matplotlib.colors import to_hex
    return [GraphExportText(trace, point, text, curve.label_font, curve.label_size, to_hex(curve.label_color), curve.label_angle,
                            (curve.style.marker_size / 2 if curve.style.marker != "none" else 0) + 4)
            for trace, curve in enumerate(spec.curves) for point, text in enumerate(curve.point_labels)
            if text and np.isfinite([curve.x[point], curve.y[point]]).all()
            and (not spec.x_log_scale or curve.x[point] > 0) and (not spec.y_log_scale or curve.y[point] > 0)]


@dataclass(slots=True)
class GraphExportState:
    """Scientific and persistent presentation state for one XY graph."""

    module: str
    title: str
    x_label: str
    y_label: str
    traces: list[GraphExportTrace]
    x_unit: str = ""
    y_unit: str = ""
    x_range: tuple[float, float] | None = None
    y_range: tuple[float, float] | None = None
    x_log_scale: bool = False
    y_log_scale: bool = False
    legend_enabled: bool = True
    texts: list[GraphExportText] = field(default_factory=list)
    figure_height_points: float = 283.464566929
    waves: list[GraphExportWave] = field(default_factory=list)


def split_axis_unit(label: str) -> tuple[str, str]:
    """Return a display label and a final parenthesized unit, when present."""
    match = _UNIT_SUFFIX.match(label.strip())
    if match is None:
        return label, ""
    return match.group("label").rstrip(), match.group("unit").strip()


def graph_export_state_from_plot_spec(spec: PlotSpec, module: str) -> GraphExportState:
    """Adapt the application's existing plot abstraction without depending on Qt."""
    _x_name, x_unit = split_axis_unit(spec.x_label)
    _y_name, y_unit = split_axis_unit(spec.y_label)
    traces = [
        GraphExportTrace(
            x=curve.x,
            y=curve.y,
            name=curve.style.legend_text or curve.label,
            style=curve.style,
            x_unit=x_unit,
            y_unit=y_unit,
        )
        for curve in spec.curves
    ]
    return GraphExportState(
        module=module,
        title=spec.title or f"{module} Graph",
        x_label=spec.x_label,
        y_label=spec.y_label,
        traces=traces,
        x_unit=x_unit,
        y_unit=y_unit,
        x_range=spec.x_range,
        y_range=spec.y_range,
        x_log_scale=spec.x_log_scale,
        y_log_scale=spec.y_log_scale,
        legend_enabled=spec.legend_enabled,
        texts=point_texts(spec),
        figure_height_points=spec.figure_height_cm * 72 / 2.54,
    )


def snapshot_transfer_context(workspace, datasets):
    """Snapshot only scientific settings on the GUI thread; retain immutable buffers."""
    import copy
    from types import MethodType, SimpleNamespace

    from materials_data_studio.core.array_revision import immutable_array
    def snapshot_arrays(values):
        result = {}
        for key, value in values.items():
            array = np.asarray(value)
            if not immutable_array(array):
                array = array.copy()
                array.setflags(write=False)
            result[key] = array
        return result
    snapshots = []
    for dataset in datasets:
        if not dataset.visible:
            continue
        snapshot = copy.copy(dataset)
        snapshot.metadata = copy.deepcopy(dataset.metadata)
        snapshot.raw_data = snapshot_arrays(dataset.raw_data)
        snapshot.processed_data = snapshot_arrays(dataset.processed_data)
        snapshot.warnings = list(dataset.warnings)
        snapshot.analysis_results = {}
        snapshots.append(snapshot)
    context = SimpleNamespace()
    if hasattr(workspace, "magnetization_unit"):
        unit = workspace.magnetization_unit.currentData()
        context.magnetization_unit = SimpleNamespace(currentData=lambda: unit)
    if hasattr(workspace, "_bridge_values"):
        from materials_data_studio.modules.ppms.resistivity_widget import ResistivityWidget
        settings = {d.dataset_id: {str(i): copy.deepcopy(workspace._bridge_setting(d, i)) for i in range(1, 5)} for d in snapshots}
        context._bridge_setting = lambda d, i: settings[d.dataset_id][str(i)]
        context._factor = MethodType(ResistivityWidget._factor, context)
        context._bridge_values = MethodType(ResistivityWidget._bridge_values, context)
    if hasattr(workspace, "_molar_arrays"):
        for name in ("_averaged_arrays", "_data_array", "_molar_arrays"):
            setattr(context, name, MethodType(getattr(type(workspace), name), context))
    return snapshots, context


def add_checked_dataset_waves(state: GraphExportState, datasets, workspace=None) -> None:
    """Retain raw and processed columns; displayed transforms remain XY traces."""
    for dataset in datasets:
        if not dataset.visible:
            continue
        folder = f"{dataset.display_name}_{dataset.dataset_id[:8]}"
        derived = derived_dataset_columns(dataset, workspace)
        for role, arrays in (("Raw", dataset.raw_data), ("Processed", dataset.processed_data), ("Derived", derived)):
            for column, values in arrays.items():
                array = np.asarray(values)
                if array.flags.writeable:
                    array = array.copy()
                    array.setflags(write=False)
                if not 1 <= array.ndim <= 4 or array.dtype.kind not in "biufcSU":
                    raise ValueError(f"{folder}: column {column} cannot be represented as an Igor wave")
                name = f"{role}_{column}"
                note = f"Dataset={dataset.display_name}\rColumn={column}\rData={role}"
                if array.dtype.kind == "b":
                    array = array.astype(np.uint8)
                state.waves.append(GraphExportWave(name, array, folder, note=note))


def derived_dataset_columns(dataset, workspace=None):
    """Compute available scientific columns with the same analysis functions."""
    from materials_data_studio.core.exceptions import AnalysisError
    columns = {}
    raw, metadata = dataset.raw_data, dataset.metadata
    if dataset.module_type.value == "mpms" and {"moment_emu", "field_oe"} <= raw.keys():
        from materials_data_studio.modules.mpms.mt_analysis import calculate_susceptibility
        weight, molar_mass = float(metadata.get("sample_weight_mg", 0)), float(metadata.get("molar_mass_g_mol", 1.))
        if weight > 0 and molar_mass > 0:
            try:
                chi, inverse = calculate_susceptibility(dataset.array("moment_emu"), dataset.array("field_oe"), sample_weight_mg=weight, molar_mass_g_mol=molar_mass)
                columns.update(Chi_emu_mol_Oe=chi, ChiInv_mol_Oe_emu=inverse)
            except AnalysisError:
                pass
        if workspace is not None and hasattr(workspace, "magnetization_unit"):
            from materials_data_studio.core.units import MagnetizationUnit, convert_moment
            unit = MagnetizationUnit(str(workspace.magnetization_unit.currentData()))
            with suppress(ValueError):
                columns["Magnetization_" + unit.name] = convert_moment(dataset.array("moment_emu"), unit, sample_weight_mg=weight, molar_mass_g_mol=molar_mass)
    if workspace is not None and hasattr(workspace, "_bridge_values"):
        bridges = [metadata["measurement_branch"]["bridge"]] if metadata.get("measurement_branch", {}).get("bridge") else range(1, 5)
        for bridge in bridges:
            try:
                rho, error = workspace._bridge_values(dataset, int(bridge), resistivity=True)
            except (ValueError, KeyError, AnalysisError):
                continue
            columns[f"Bridge{bridge}_Rho_ohm_cm"] = rho
            if error is not None:
                columns[f"Bridge{bridge}_RhoError_ohm_cm"] = error
            with np.errstate(divide="ignore", invalid="ignore"):
                columns[f"Bridge{bridge}_LnRho"] = np.where(rho > 0, np.log(rho), np.nan)
    if workspace is not None and hasattr(workspace, "_molar_arrays") and {"cp_raw_uj_k", "temperature_k"} <= raw.keys():
        try:
            cp, error = workspace._molar_arrays(dataset)
            t = workspace._data_array(dataset, "temperature_k")
            columns.update(Cp_J_mol_K=cp, CpError_J_mol_K=error, TemperatureSquared_K2=t*t)
            with np.errstate(divide="ignore", invalid="ignore"):
                columns["CpOverT_J_mol_K2"] = np.where(t != 0, cp/t, np.nan)
        except (ValueError, KeyError, AnalysisError):
            pass
    if dataset.module_type.value == "xrd" and "intensity" in raw:
        from materials_data_studio.core.models import NormalizationMode
        from materials_data_studio.modules.xrd.transforms import process_intensity
        if metadata.get("background_enabled") or metadata.get("normalization", "none") != "none":
            columns["CorrectedIntensity"] = process_intensity(dataset.array("x"), dataset.array("intensity"),
                normalization=NormalizationMode(metadata.get("normalization", "none")), scale=1., offset=0.,
                background=dataset.processed_data.get("background"), subtract_background=bool(metadata.get("background_enabled")))
    return columns
