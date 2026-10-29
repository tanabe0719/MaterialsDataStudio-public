from __future__ import annotations

from pathlib import Path

import numpy as np

from materials_data_studio.modules.xrd.peak_fitting.export import (
    export_analysis_log,
    export_fit_data,
    export_fit_figure,
    result_from_state,
    result_to_state,
)
from materials_data_studio.modules.xrd.peak_fitting.models import (
    FitParameter,
    FitStatistics,
    PeakFitResult,
    PeakReflection,
    ReflectionGroup,
)


def _result() -> PeakFitResult:
    reflection = PeakReflection(
        "phase",
        "Phase",
        (1, 1, 1),
        ((1, 1, 1),),
        30.0,
        2.0,
        100.0,
    )
    group = ReflectionGroup(
        1,
        "phase",
        "Phase",
        (reflection,),
        30.0,
        2.0,
        100.0,
        30.1,
        1.99,
        250.0,
        0.2,
        True,
        "Fitted",
    )
    x = np.array([29.9, 30.0, 30.1])
    observed = np.array([2.0, 10.0, 3.0])
    calculated = np.array([2.1, 9.8, 3.1])
    residual = observed - calculated
    return PeakFitResult(
        {
            "eta": FitParameter("eta", 0.3, 0.0, 1.0),
            "fwhm": FitParameter("fwhm", 0.2, 0.01, 2.0, "deg", True),
        },
        [group],
        x,
        observed,
        calculated,
        np.ones(3),
        residual,
        residual,
        np.ones(3, dtype=bool),
        np.zeros(3, dtype=bool),
        FitStatistics(
            True,
            "ok",
            3,
            1,
            1,
            1,
            0,
            float(np.sum(residual**2)),
            float(np.sqrt(np.mean(residual**2))),
            0.1,
            0.1,
            0.1,
            5,
        ),
    )


def test_fit_result_state_round_trip_and_all_numeric_exports(tmp_path: Path) -> None:
    result = _result()
    restored = result_from_state(result_to_state(result))
    np.testing.assert_allclose(restored.calculated, result.calculated)
    assert restored.groups[0].reflections[0].hkl == (1, 1, 1)
    for suffix in (".csv", ".tsv", ".dat", ".itx"):
        target = tmp_path / f"fit{suffix}"
        paths = export_fit_data(target, result, metadata=["Peak fit Å"])
        assert all(path.is_file() and path.stat().st_size > 0 for path in paths)
    log_path = tmp_path / "fit_analysis_log.txt"
    export_analysis_log(
        log_path,
        result,
        context={"source_dataset": "sample", "source_cif": "phase.cif"},
    )
    text = log_path.read_text()
    assert "application_version" in text
    assert "reflection_memberships" in text
    figure_path = tmp_path / "fit.svg"
    export_fit_figure(figure_path, result, transparent=True)
    assert figure_path.is_file() and figure_path.stat().st_size > 0
