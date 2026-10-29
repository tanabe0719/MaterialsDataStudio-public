"""Non-destructive measurement hierarchies and shared sample information."""

from __future__ import annotations

import copy
import uuid
from itertools import pairwise
from typing import Any

import numpy as np

from materials_data_studio.core.models import Dataset, ModuleType, PlotStyle
from materials_data_studio.core.project_state import ProjectState

SAMPLE_KEYS = (
    "sample_weight_mg",
    "sample_mass_mg",
    "mass_source",
    "formula",
    "molar_mass_g_mol",
    "molar_mass_confirmed",
    "molar_mass_source",
    "atoms_per_formula_unit",
    "atoms_source",
)


def root_dataset(state: ProjectState, dataset: Dataset) -> Dataset:
    parent = dataset.metadata.get("measurement_parent")
    return next((item for item in state.datasets if item.dataset_id == parent), dataset)


def share_sample(state: ProjectState, dataset: Dataset) -> None:
    """Propagate sample edits and invalidate all affected derived results."""
    root = root_dataset(state, dataset)
    sample = {key: dataset.metadata[key] for key in SAMPLE_KEYS if key in dataset.metadata}
    family = [
        item
        for item in state.datasets
        if item is root or item.metadata.get("measurement_parent") == root.dataset_id
    ]
    if any(
        any(item.metadata.get(key) != value for key, value in sample.items()) for item in family
    ):
        for item in family:
            item.metadata.update(copy.deepcopy(sample))
            item.processed_data.clear()
            item.analysis_results.clear()
            item.metadata["analysis_status"] = "Sample information changed"
    state.modified = True


def measurement_row_count(dataset: Dataset) -> int:
    """Use original columns for arbitrary PPMS files, without inventing channels."""
    key = "temperature_k" if "temperature_k" in dataset.raw_data else next(iter(dataset.raw_data))
    return len(dataset.raw_data[key])


def automatic_branches(dataset: Dataset) -> list[dict[str, Any]]:
    """Describe inclusive raw-row intervals in original measurement order."""
    from materials_data_studio.modules.mpms.mh_analysis import detect_sweep_segments
    from materials_data_studio.modules.mpms.mt_analysis import automatic_split
    from materials_data_studio.modules.ppms.heat_capacity import segment_heat_capacity
    from materials_data_studio.modules.ppms.resistivity import segment_transport

    if dataset.metadata.get("ppms_kind") == "generic":
        size = measurement_row_count(dataset)
        return [{"id": str(uuid.uuid4()), "start": 0, "stop": size - 1, "name": "All rows", "mode": "generic"}] if size else []
    t = np.asarray(dataset.raw_data["temperature_k"], dtype=float)
    h = np.asarray(dataset.raw_data["field_oe"], dtype=float)
    branches: list[dict[str, Any]] = []

    def add(start: int, stop: int, name: str, **extra: Any) -> None:
        if stop >= start:
            branches.append(
                {"id": str(uuid.uuid4()), "start": start, "stop": stop, "name": name, **extra}
            )

    if dataset.module_type == ModuleType.MPMS:
        # An isothermal variable-field experiment is an M-H measurement.
        mh = (np.ptp(t) < 1.0 and np.ptp(h) > 2.0) or (
            np.count_nonzero(np.abs(np.diff(h)) > 2) >= max(2, len(h) // 10)
            and np.mean(np.abs(np.diff(t)) < 0.5) > 0.8
        )
        if mh:
            for segment in detect_sweep_segments(t, h):
                add(
                    segment.start_index,
                    segment.stop_index,
                    f"{segment.nominal_temperature_k:g} K · {segment.direction} · {segment.cycle_index + 1}",
                    mode="mh",
                )
        else:
            starts = [0, *(np.flatnonzero(np.abs(np.diff(h)) > 50) + 1).tolist(), len(t)]
            for start, end in pairwise(starts):
                split = automatic_split(t[start:end]).automatic_index + start
                label = f"{float(np.median(h[start:end])):g} Oe"
                add(start, split, f"ZFC · {label}", mode="mt", branch="ZFC")
                add(split + 1, end - 1, f"FC · {label}", mode="mt", branch="FC")
    elif dataset.metadata.get("ppms_kind") == "heat_capacity":
        for segment in segment_heat_capacity(t, h):
            add(
                int(segment.indices[0]), int(segment.indices[-1]), segment.label, average_pairs=True
            )
    else:
        for bridge, info in dataset.metadata.get("bridges", {}).items():
            if not info.get("available"):
                continue
            setting = dataset.metadata.get("bridge_settings", {}).get(bridge, {})
            values = dataset.raw_data.get(f"bridge_{bridge}_{setting.get('source', '')}")
            if values is None or not np.any(np.isfinite(values)):
                continue
            # Files can contain both R-T and R-H experiments. First identify
            # contiguous temperature regimes; never mistake each step of an
            # isothermal field sweep for a separate constant-field experiment.
            for temperature_segment in segment_transport(t, h, mode="rho_t"):
                indices = temperature_segment.indices
                is_field_sweep = np.ptp(t[indices]) <= 0.1 and np.ptp(h[indices]) > 50
                mode = "rho_h" if is_field_sweep else "rho_t"
                if is_field_sweep:
                    blocks = [indices]
                else:
                    blocks = [
                        indices[block.indices]
                        for block in segment_heat_capacity(
                            t[indices], h[indices], field_threshold_oe=50
                        )
                    ]
                for block in blocks:
                    for segment in segment_transport(t[block], h[block], mode=mode):
                        start, stop = (
                            int(block[segment.indices[0]]),
                            int(block[segment.indices[-1]]),
                        )
                        if np.any(np.isfinite(values[start : stop + 1])):
                            add(start, stop, segment.label, bridge=int(bridge), mode=mode)
    return branches


def apply_branches(state: ProjectState, root: Dataset, definitions: list[dict[str, Any]]) -> None:
    """Commit validated definitions; unchanged children keep identity and fits."""
    size = measurement_row_count(root)
    ids: set[str] = set()
    for definition in definitions:
        start, stop = int(definition["start"]), int(definition["stop"])
        if not 0 <= start <= stop < size or not str(definition["name"]).strip():
            raise ValueError("Each branch needs a name and valid inclusive raw-row bounds.")
        if definition["id"] in ids:
            raise ValueError("Branch identifiers must be unique.")
        ids.add(definition["id"])
        bridge = definition.get("bridge")
        if bridge is not None and not root.metadata.get("bridges", {}).get(str(bridge), {}).get(
            "available"
        ):
            raise ValueError("The selected bridge has no valid data.")
        if bridge is not None:
            setting = root.metadata.get("bridge_settings", {}).get(str(bridge), {})
            values = root.raw_data.get(f"bridge_{bridge}_{setting.get('source', '')}")
            if values is None or not np.isfinite(values[start : stop + 1]).any():
                raise ValueError("The selected bridge has no valid data within this branch.")
    previous = {
        item.metadata["measurement_branch"]["id"]: item
        for item in state.datasets
        if item.metadata.get("measurement_parent") == root.dataset_id
    }
    children = []
    for definition in definitions:
        child = previous.get(definition["id"])
        old = child.metadata.get("measurement_branch", {}) if child else {}
        changed = any(
            old.get(key) != definition.get(key)
            for key in ("start", "stop", "bridge", "average_pairs", "mode")
        )
        if child is None:
            metadata = copy.deepcopy(root.metadata)
            for key in ("measurement_container", "branch_definitions"):
                metadata.pop(key, None)
            child = Dataset(
                str(definition["name"]),
                root.module_type,
                {},
                source_filename=root.source_filename,
                source_hash=root.source_hash,
                metadata=metadata,
                plot_style=PlotStyle.from_dict(root.plot_style.to_dict()),
                warnings=list(root.warnings),
            )
            # Legacy fits stay on the parent for inspection, never silently
            # attributed to a different subset of measured points.
            child.analysis_results.clear()
            branch = definition.get("branch", "").lower()
            legacy_style = root.metadata.get(f"{branch}_style")
            if isinstance(legacy_style, dict):
                child.plot_style = PlotStyle.from_dict(legacy_style)
            elif root.module_type == ModuleType.MPMS and definition.get("mode") == "mt":
                child.plot_style.marker = "s" if branch == "fc" else "o"
                child.plot_style.line_style = "--" if branch == "fc" else "-"
                child.plot_style.marker_fill_color = "#FFFFFF"
            child.plot_style.legend_text = f"{root.display_name} · {definition['name']}"
            changed = True
        child.display_name = str(definition["name"]).strip()
        child.metadata["measurement_parent"] = root.dataset_id
        child.metadata["measurement_branch"] = copy.deepcopy(definition)
        child.metadata["selected_segment"] = 0
        child.metadata["manual_segment_bounds"] = {}
        if changed:
            start, stop = int(definition["start"]), int(definition["stop"]) + 1
            # Immutable contiguous views avoid copying every channel/bridge.
            child.raw_data = {
                key: value[start:stop] if value.ndim and len(value) == size else value
                for key, value in root.raw_data.items()
            }
            child.processed_data.clear()
            if root.metadata.get("ppms_kind") == "generic":
                # Preserve edited/derived columns, but never inherit fitted results.
                for descriptor in root.metadata.get("generic_columns", []):
                    key = descriptor["key"]
                    if key in root.processed_data:
                        child.processed_data[key] = root.processed_data[key][start:stop].copy()
            child.analysis_results.clear()
            child.metadata.pop("averaging_revision", None)
        bridge = definition.get("bridge")
        if bridge is not None:
            if changed:
                child.metadata["plot_type"] = definition.get("mode", "rho_t")
            for number, setting in child.metadata.get("bridge_settings", {}).items():
                setting["visible"] = int(number) == int(bridge)
                if changed and int(number) == int(bridge):
                    stored = setting.get("style")
                    if isinstance(stored, dict):
                        child.plot_style = PlotStyle.from_dict(stored)
                        child.plot_style.legend_text = (
                            f"{root.display_name} · Bridge {bridge} · {definition['name']}"
                        )
                        setting["style"] = child.plot_style.to_dict()
        children.append(child)
    state.datasets = [
        item
        for item in state.datasets
        if item.metadata.get("measurement_parent") != root.dataset_id
    ]
    position = next(index for index, item in enumerate(state.datasets) if item is root) + 1
    state.datasets[position:position] = children
    root.metadata["measurement_container"] = True
    root.metadata["branch_definitions"] = copy.deepcopy(definitions)
    for order, item in enumerate(state.datasets):
        item.plot_order = order
    state.modified = True


def ensure_hierarchies(state: ProjectState, module: ModuleType, kind: str | None = None) -> None:
    for dataset in tuple(state.datasets):
        if (
            dataset.module_type != module
            or dataset.metadata.get("measurement_parent")
            or dataset.metadata.get("measurement_container")
        ):
            continue
        if kind is not None and dataset.metadata.get("ppms_kind") != kind:
            continue
        if "temperature_k" in dataset.raw_data and "field_oe" in dataset.raw_data:
            apply_branches(state, dataset, automatic_branches(dataset))
