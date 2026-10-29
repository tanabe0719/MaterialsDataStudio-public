"""M–H field-sweep segmentation and conversion helpers."""

from __future__ import annotations

from itertools import pairwise

import numpy as np
from numpy.typing import ArrayLike

from materials_data_studio.core.exceptions import AnalysisError
from materials_data_studio.modules.mpms.models import SweepSegment


def detect_sweep_segments(
    temperature_k: ArrayLike,
    field_oe: ArrayLike,
    *,
    temperature_tolerance_k: float = 0.5,
    field_noise_oe: float = 1.0,
) -> list[SweepSegment]:
    """Split measurement order at temperature changes and field reversals."""
    temperature = np.asarray(temperature_k, dtype=float)
    field = np.asarray(field_oe, dtype=float)
    if temperature.shape != field.shape or temperature.ndim != 1 or len(field) == 0:
        raise AnalysisError("Temperature and field must be aligned one-dimensional arrays.")
    if temperature_tolerance_k < 0 or field_noise_oe < 0:
        raise AnalysisError("Segmentation tolerances cannot be negative.")

    boundaries = [0]
    previous_direction = 0
    for index in range(1, len(field)):
        if abs(temperature[index] - temperature[index - 1]) > temperature_tolerance_k:
            if boundaries[-1] != index:
                boundaries.append(index)
            previous_direction = 0
            continue
        delta = field[index] - field[index - 1]
        direction = 0 if abs(delta) <= field_noise_oe else (1 if delta > 0 else -1)
        if direction and previous_direction and direction != previous_direction:
            turning_index = index - 1
            if turning_index > boundaries[-1]:
                boundaries.append(turning_index)
            previous_direction = direction
        elif direction:
            previous_direction = direction
    boundaries.append(len(field))
    boundaries = sorted(set(boundaries))

    segments: list[SweepSegment] = []
    cycle = 0
    for start, exclusive_stop in pairwise(boundaries):
        stop = exclusive_stop - 1
        if stop <= start:
            continue
        delta = field[stop] - field[start]
        direction = "increasing" if delta > 0 else "decreasing" if delta < 0 else "constant"
        if segments and direction == "increasing" and segments[-1].direction == "decreasing":
            cycle += 1
        segments.append(
            SweepSegment(
                start_index=start,
                stop_index=stop,
                nominal_temperature_k=float(np.median(temperature[start : stop + 1])),
                direction=direction,
                cycle_index=cycle,
            )
        )
    if not segments:
        segments.append(
            SweepSegment(
                0,
                len(field) - 1,
                float(np.median(temperature)),
                "constant",
                0,
            )
        )
    return segments


def center_field(field_oe: ArrayLike) -> np.ndarray:
    """Center a field sweep around the midpoint of its extrema."""
    values = np.asarray(field_oe, dtype=float)
    return values - (float(np.nanmax(values)) + float(np.nanmin(values))) / 2.0
