from __future__ import annotations

import numpy as np
import pytest

from materials_data_studio.core.exceptions import AnalysisError
from materials_data_studio.modules.ppms.generic import (
    evaluate_expression,
    fit_user_expression,
    parse_generic_ppms_text,
)

TEXT = """[Header]
INFO,12.5,Sample Weight (mg)
[Data]
Temperature (K),Signal (V),Comment
1,2.5,ok
2,4.5,check
3,6.5,ok
"""


def test_generic_parser_keeps_all_columns_and_sample_weight() -> None:
    result = parse_generic_ppms_text(TEXT)
    assert result.sample_weight_mg == pytest.approx(12.5)
    assert [item["name"] for item in result.descriptors] == [
        "Temperature (K)",
        "Signal (V)",
        "Comment",
    ]
    np.testing.assert_allclose(result.columns["column_0"], [1, 2, 3])
    assert result.columns["column_2"].tolist() == ["ok", "check", "ok"]


def test_safe_column_expression_and_unknown_name_rejection() -> None:
    values = evaluate_expression(
        "signal / sample_moles",
        {"signal": np.array([2.0, 4.0]), "sample_moles": 2.0},
    )
    np.testing.assert_allclose(values, [1, 2])
    with pytest.raises(AnalysisError, match="Unknown"):
        evaluate_expression("secret + 1", {"signal": np.array([1.0])})


def test_user_formula_fit_infers_parameters() -> None:
    x = np.linspace(0, 5, 30)
    fit = fit_user_expression(x, 3 * x + 2, "a*x+b", 0, 5)
    assert fit.parameter_names == ["a", "b"]
    np.testing.assert_allclose(fit.parameters, [3, 2], rtol=1e-8)
    assert fit.r_squared == pytest.approx(1.0)
