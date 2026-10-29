"""Peak-fit numerical, log, figure, and session-state exports."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.figure import Figure

from materials_data_studio.core.export import export_numeric
from materials_data_studio.modules.xrd.peak_fitting.models import (
    FitParameter,
    FitStatistics,
    PeakFitResult,
    PeakReflection,
    ReflectionGroup,
)
from materials_data_studio.version import __version__


def result_to_state(result: PeakFitResult) -> dict[str, Any]:
    """Convert a fit result to JSON-compatible session state."""
    return {
        "parameters": {
            name: {
                "name": parameter.name,
                "value": parameter.value,
                "lower": parameter.lower,
                "upper": parameter.upper,
                "unit": parameter.unit,
                "refine": parameter.refine,
                "status": parameter.status,
                "standard_error": parameter.standard_error,
            }
            for name, parameter in result.parameters.items()
        },
        "groups": [
            {
                "group_id": group.group_id,
                "phase_id": group.phase_id,
                "phase_name": group.phase_name,
                "reflections": [
                    {
                        "phase_id": reflection.phase_id,
                        "phase_name": reflection.phase_name,
                        "hkl": list(reflection.hkl),
                        "member_hkls": [
                            list(hkl) for hkl in reflection.member_hkls
                        ],
                        "calculated_two_theta_deg": reflection.calculated_two_theta_deg,
                        "calculated_d_a": reflection.calculated_d_a,
                        "calculated_intensity": reflection.calculated_intensity,
                        "enabled": reflection.enabled,
                    }
                    for reflection in group.reflections
                ],
                "calculated_two_theta_deg": group.calculated_two_theta_deg,
                "calculated_d_a": group.calculated_d_a,
                "calculated_intensity": group.calculated_intensity,
                "fitted_two_theta_deg": group.fitted_two_theta_deg,
                "fitted_d_a": group.fitted_d_a,
                "fitted_area": group.fitted_area,
                "fwhm_deg": group.fwhm_deg,
                "enabled": group.enabled,
                "status": group.status,
            }
            for group in result.groups
        ],
        "x": result.x.tolist(),
        "observed": result.observed.tolist(),
        "calculated": result.calculated.tolist(),
        "background": result.background.tolist(),
        "residual": result.residual.tolist(),
        "weighted_residual": result.weighted_residual.tolist(),
        "fit_mask": result.fit_mask.tolist(),
        "excluded_mask": result.excluded_mask.tolist(),
        "statistics": {
            name: getattr(result.statistics, name)
            for name in result.statistics.__dataclass_fields__
        },
        "warnings": result.warnings,
        "correlation_names": result.correlation_names,
        "correlation_matrix": (
            result.correlation_matrix.tolist()
            if result.correlation_matrix is not None
            else None
        ),
    }


def result_from_state(state: dict[str, Any]) -> PeakFitResult:
    """Restore an optional fit result without touching source datasets."""
    parameters = {
        name: FitParameter(**value)
        for name, value in dict(state.get("parameters", {})).items()
    }
    groups = []
    for stored_group in state.get("groups", []):
        reflections = tuple(
            PeakReflection(
                phase_id=str(value["phase_id"]),
                phase_name=str(value["phase_name"]),
                hkl=(
                    int(value["hkl"][0]),
                    int(value["hkl"][1]),
                    int(value["hkl"][2]),
                ),
                member_hkls=tuple(
                    (int(hkl[0]), int(hkl[1]), int(hkl[2]))
                    for hkl in value["member_hkls"]
                ),
                calculated_two_theta_deg=float(
                    value["calculated_two_theta_deg"]
                ),
                calculated_d_a=float(value["calculated_d_a"]),
                calculated_intensity=float(value["calculated_intensity"]),
                enabled=bool(value.get("enabled", True)),
            )
            for value in stored_group["reflections"]
        )
        groups.append(
            ReflectionGroup(
                group_id=int(stored_group["group_id"]),
                phase_id=str(stored_group["phase_id"]),
                phase_name=str(stored_group["phase_name"]),
                reflections=reflections,
                calculated_two_theta_deg=float(
                    stored_group["calculated_two_theta_deg"]
                ),
                calculated_d_a=float(stored_group["calculated_d_a"]),
                calculated_intensity=float(
                    stored_group["calculated_intensity"]
                ),
                fitted_two_theta_deg=float(stored_group["fitted_two_theta_deg"]),
                fitted_d_a=float(stored_group["fitted_d_a"]),
                fitted_area=float(stored_group["fitted_area"]),
                fwhm_deg=float(stored_group["fwhm_deg"]),
                enabled=bool(stored_group.get("enabled", True)),
                status=str(stored_group.get("status", "Fitted")),
            )
        )
    statistics = FitStatistics(**dict(state["statistics"]))
    correlation = state.get("correlation_matrix")
    return PeakFitResult(
        parameters,
        groups,
        np.asarray(state["x"], dtype=float),
        np.asarray(state["observed"], dtype=float),
        np.asarray(state["calculated"], dtype=float),
        np.asarray(state["background"], dtype=float),
        np.asarray(state["residual"], dtype=float),
        np.asarray(state["weighted_residual"], dtype=float),
        np.asarray(state["fit_mask"], dtype=bool),
        np.asarray(state["excluded_mask"], dtype=bool),
        statistics,
        list(state.get("warnings", [])),
        list(state.get("correlation_names", [])),
        np.asarray(correlation, dtype=float) if correlation is not None else None,
    )


def export_fit_data(
    path: Path,
    result: PeakFitResult,
    *,
    metadata: list[str],
) -> list[Path]:
    """Export point data plus parameter/reflection companion tables."""
    fit_range_flag = result.fit_mask | result.excluded_mask
    export_numeric(
        path,
        {
            "TwoTheta_deg": result.x,
            "Intensity_observed": result.observed,
            "Intensity_calculated": result.calculated,
            "Background": result.background,
            "Residual_observed_minus_calculated": result.residual,
            "Weighted_residual": result.weighted_residual,
            "Fit_range_flag": fit_range_flag.astype(float),
            "Excluded_range_flag": result.excluded_mask.astype(float),
        },
        metadata=metadata,
    )
    parameter_path = path.with_name(f"{path.stem}_parameters{path.suffix}")
    parameter_values = list(result.parameters.values())
    export_numeric(
        parameter_path,
        {
            "Index": np.arange(1, len(parameter_values) + 1),
            "Value": [item.value for item in parameter_values],
            "Standard_error": [
                np.nan if item.standard_error is None else item.standard_error
                for item in parameter_values
            ],
            "Lower_bound": [item.lower for item in parameter_values],
            "Upper_bound": [item.upper for item in parameter_values],
            "Refined": [float(item.refine) for item in parameter_values],
        },
        metadata=[
            *metadata,
            "Parameter names: "
            + ", ".join(item.name for item in parameter_values),
            "Units: " + ", ".join(item.unit or "unitless" for item in parameter_values),
        ],
    )
    reflection_path = path.with_name(f"{path.stem}_reflections{path.suffix}")
    export_numeric(
        reflection_path,
        {
            "Group_ID": [group.group_id for group in result.groups],
            "h": [group.reflections[0].hkl[0] for group in result.groups],
            "k": [group.reflections[0].hkl[1] for group in result.groups],
            "l": [group.reflections[0].hkl[2] for group in result.groups],
            "Calculated_two_theta_deg": [
                group.calculated_two_theta_deg for group in result.groups
            ],
            "Fitted_two_theta_deg": [
                group.fitted_two_theta_deg for group in result.groups
            ],
            "Calculated_d_A": [group.calculated_d_a for group in result.groups],
            "Fitted_d_A": [group.fitted_d_a for group in result.groups],
            "Calculated_relative_intensity": [
                group.calculated_intensity for group in result.groups
            ],
            "Fitted_integrated_area": [
                group.fitted_area for group in result.groups
            ],
            "FWHM_deg": [group.fwhm_deg for group in result.groups],
            "Eta": [
                result.parameters["eta"].value for _group in result.groups
            ],
            "Enabled": [float(group.enabled) for group in result.groups],
        },
        metadata=[
            *metadata,
            "Phase: " + ", ".join(group.phase_name for group in result.groups),
            "Unresolved hkl memberships are retained in the analysis log.",
        ],
    )
    return [path, parameter_path, reflection_path]


def export_analysis_log(
    path: Path,
    result: PeakFitResult,
    *,
    context: dict[str, Any],
) -> None:
    """Write a human-readable, JSON-based reproducibility log."""
    payload = {
        "application_version": __version__,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        **context,
        "parameters": {
            name: {
                "initial_or_current_value": parameter.value,
                "lower": parameter.lower,
                "upper": parameter.upper,
                "refined": parameter.refine,
                "standard_error": parameter.standard_error,
                "unit": parameter.unit,
            }
            for name, parameter in result.parameters.items()
        },
        "statistics": {
            name: getattr(result.statistics, name)
            for name in result.statistics.__dataclass_fields__
        },
        "warnings": result.warnings,
        "reflection_memberships": [
            {
                "group_id": group.group_id,
                "phase": group.phase_name,
                "hkl": [
                    list(hkl)
                    for reflection in group.reflections
                    for hkl in reflection.member_hkls
                ],
            }
            for group in result.groups
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def export_fit_figure(
    path: Path, result: PeakFitResult, *, transparent: bool = False
) -> None:
    """Export observed/calculated/background and residual in linked axes."""
    figure = Figure(figsize=(7.0, 6.0), dpi=300)
    main = figure.add_subplot(211)
    residual_axis = figure.add_subplot(212, sharex=main)
    main.plot(result.x, result.observed, color="#334155", linewidth=0.8, label="Observed")
    main.plot(result.x, result.calculated, color="#DC2626", linewidth=1.4, label="Calculated")
    main.plot(result.x, result.background, color="#F59E0B", linestyle="--", label="Background")
    tick_min = float(np.nanmin(result.observed))
    tick_span = float(np.nanmax(result.observed) - tick_min)
    for group in result.groups:
        main.vlines(
            group.fitted_two_theta_deg,
            tick_min - 0.08 * tick_span,
            tick_min - 0.02 * tick_span,
            color="#2563EB",
            linewidth=0.8,
        )
    residual_axis.plot(result.x, result.residual, color="#475569", linewidth=0.8)
    residual_axis.axhline(0, color="#94A3B8", linestyle="--", linewidth=0.8)
    main.set_ylabel("Intensity")
    residual_axis.set_xlabel("2θ (deg)")
    residual_axis.set_ylabel("Obs. − calc.")
    main.legend()
    main.tick_params(direction="in", top=True, right=True)
    residual_axis.tick_params(direction="in", top=True, right=True)
    figure.tight_layout()
    figure.savefig(path, transparent=transparent, format=path.suffix.removeprefix("."))
