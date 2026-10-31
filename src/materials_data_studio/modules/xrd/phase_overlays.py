"""Shared phase colors, peak ownership and parent-relative display annotations."""
from __future__ import annotations

import colorsys
from typing import Any

import numpy as np

from materials_data_studio.core.models import Dataset, ModuleType, PlotStyle
from materials_data_studio.modules.xrd.phase_detection import DOUBLET, alpha2_position
from materials_data_studio.modules.xrd.phase_search import stick_segments


def phase_key(result: dict[str, Any]) -> str:
    candidate = result["candidate"]
    return candidate["source"] + ":" + candidate["identity"]


def phase_label(result: dict[str, Any]) -> str:
    candidate = result["candidate"]
    return f"{candidate['formula']} ({candidate['identity'][:18]})"


def phase_color(index: int) -> str:
    palette = ("#16a34a", "#ea580c", "#2563eb", "#a855f7", "#0891b2", "#db2777")
    if index < len(palette):
        return palette[index]
    return "#" + "".join(f"{round(v * 255):02x}" for v in colorsys.hsv_to_rgb((index * .61803398875) % 1, .7, .8))


def reflections(result: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    pattern, settings = result["pattern"], result["settings"]
    x, y = np.asarray(pattern["positions"], float), np.asarray(pattern["intensities"], float)
    if settings["radiation"] == DOUBLET:
        second = alpha2_position(x)
        valid = np.isfinite(second)
        x, y = np.concatenate((x, second[valid])), np.concatenate((y, y[valid] * settings.get("alpha2_ratio", .5)))
    valid = np.isfinite(x) & np.isfinite(y) & (y > 0)
    return x[valid] + settings["zero_shift"], y[valid]


def peak_owners(results: list[dict[str, Any]]) -> dict[int, int]:
    """Resolve shared observations by calculated intensity on a common 100 scale.

    Ties keep the first (higher ranked) phase. Compare grouped intensity, using
    the same individual-reflection normalization as the displayed sticks.
    """
    winners: dict[int, tuple[float, int]] = {}
    for index, result in enumerate(results):
        maximum = max(result["pattern"]["intensities"], default=0) or 1
        groups = result["match"]["groups"]
        for assignment in result["match"]["assignments"]:
            observed = assignment["observed"]
            intensity = groups[assignment["predicted"]]["intensity"] * 100 / maximum
            if observed not in winners or intensity > winners[observed][0]:
                winners[observed] = intensity, index
    return {observed: item[1] for observed, item in winners.items()}


def above_peaks(x, y, positions, percent: float = 8.) -> np.ndarray:
    """Use local observed heights; remain above zero and negative peaks too."""
    x, y = np.asarray(x), np.asarray(y)
    if np.all(x[1:] >= x[:-1]):
        values = np.interp(positions, x, y)
    else:
        order = np.argsort(x)
        values = np.interp(positions, x[order], y[order])
    finite = np.asarray(y)[np.isfinite(y)]
    floor = max(float(np.ptp(finite)) * .01, np.finfo(float).eps) if finite.size else 1.
    return values + np.maximum(np.abs(values), floor) * percent / 100


def annotation_dataset(parent: Dataset, result: dict[str, Any], color: str, kind: str,
                       *, positions=None, strengths=None, row: int = 0) -> Dataset:
    if kind == "matched":
        assert positions is not None
        x, heights = np.asarray(positions, float), np.ones(len(positions))
    else:
        x, intensity = reflections(result)
        # Every alpha1 tick has equal length; alpha2 ticks use its intensity ratio.
        counts = np.count_nonzero(np.asarray(result["pattern"]["intensities"], float) > 0)
        heights = np.ones(len(intensity))
        if result["settings"]["radiation"] == DOUBLET:
            heights[counts:] = result["settings"].get("alpha2_ratio", .5)
    name = phase_label(result) + " · " + ("Matched" if kind == "matched" else "Phase ticks")
    arrays = {"x": x, "intensity": heights}
    if kind == "matched":
        arrays["match_strength"] = np.asarray(strengths, float) if strengths is not None else np.ones(len(x))
    return Dataset(name, ModuleType.XRD, arrays,
                   metadata={"phase_parent": parent.dataset_id, "phase_annotation": kind,
                             "phase_identity": phase_key(result), "source_axis": "two_theta",
                             "wavelength_a": result["settings"]["wavelength"],
                             "marker_lift_percent": 8., "tick_offset_percent": 12. + row * 8.,
                             "tick_length_percent": 5.},
                   plot_style=PlotStyle(color=color, line_style="none" if kind == "matched" else "-",
                                        marker="o" if kind == "matched" else "none",
                                        marker_fill_color=color, marker_edge_color=color))


def visible_marker_masks(datasets: list[Dataset]) -> dict[str, np.ndarray]:
    """Assign shared peaks among visible children, so hiding a phase reveals others."""
    markers = [dataset for dataset in datasets if dataset.visible and dataset.metadata.get("phase_annotation") == "matched"]
    winners: dict[tuple[str, float], tuple[float, str]] = {}
    for dataset in markers:
        strengths = dataset.raw_data.get("match_strength", np.ones(len(dataset.array("x"))))
        for position, strength in zip(dataset.array("x"), strengths, strict=True):
            key = dataset.metadata["phase_parent"], float(position)
            if key not in winners or strength > winners[key][0]:
                winners[key] = float(strength), dataset.dataset_id
    return {dataset.dataset_id: np.array([winners[dataset.metadata["phase_parent"], float(position)][1] == dataset.dataset_id
                                          for position in dataset.array("x")], dtype=bool) for dataset in markers}


def annotation_data(dataset: Dataset, parent_x, parent_y, converted_positions, *, log=False):
    """Compute from the current displayed parent, never store stale Y samples."""
    if dataset.metadata["phase_annotation"] == "matched":
        return converted_positions, above_peaks(parent_x, parent_y, converted_positions,
                                                dataset.metadata.get("marker_lift_percent", 8.))
    finite = np.asarray(parent_y)[np.isfinite(parent_y)]
    if log:
        finite = finite[finite > 0]
    if not finite.size:
        return np.array([]), np.array([])
    low, high = float(np.min(finite)), float(np.max(finite))
    span = max(high - low, abs(high) * .1, 1e-12)
    offset = dataset.metadata.get("tick_offset_percent", 12.) / 100
    length = dataset.metadata.get("tick_length_percent", 5.) / 100
    if log:
        baseline = low / (1 + offset)
        heights = baseline * length * dataset.array("intensity")
    else:
        baseline = low - span * offset
        heights = span * length * dataset.array("intensity")
    x, y = stick_segments(converted_positions, heights)
    return x, y + baseline
