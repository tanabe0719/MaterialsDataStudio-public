"""Parsing, safe column calculations, and curve fitting for arbitrary PPMS DAT files."""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from materials_data_studio.core.exceptions import AnalysisError, DataImportError
from materials_data_studio.modules.ppms.parser_common import (
    normalize_ppms_name,
    parse_float,
    split_ppms_sections,
)

_FUNCTIONS: dict[str, Any] = {
    "abs": np.abs,
    "sqrt": np.sqrt,
    "exp": np.exp,
    "log": np.log,
    "log10": np.log10,
    "sin": np.sin,
    "cos": np.cos,
    "tan": np.tan,
    "minimum": np.minimum,
    "maximum": np.maximum,
    "where": np.where,
}
_CONSTANTS = {"pi": np.pi, "e": np.e}


@dataclass(slots=True)
class GenericPpmsParseResult:
    """All columns and sample information recovered from one PPMS file."""

    columns: dict[str, NDArray[np.generic]]
    descriptors: list[dict[str, str]]
    sample_weight_mg: float | None
    header_rows: list[list[str]]


@dataclass(slots=True)
class ExpressionFit:
    """Result of fitting a user expression to selected x/y data."""

    parameter_names: list[str]
    parameters: NDArray[np.float64]
    standard_errors: NDArray[np.float64]
    x: NDArray[np.float64]
    y_fit: NDArray[np.float64]
    r_squared: float


def _symbol(value: str, index: int) -> str:
    symbol = re.sub(r"\W+", "_", value, flags=re.UNICODE).strip("_").lower()
    if not symbol or symbol[0].isdigit():
        symbol = f"column_{index + 1}_{symbol}".rstrip("_")
    return symbol


def _sample_weight(header_rows: list[list[str]]) -> float | None:
    for row in header_rows:
        for index, cell in enumerate(row):
            normalized = normalize_ppms_name(cell)
            if "sample weight" in normalized or "sample mass" in normalized:
                candidates = [*row[:index], *row[index + 1 :]]
                for candidate in candidates:
                    value = parse_float(candidate)
                    if value is not None and np.isfinite(value):
                        return float(value)
    return None


def parse_generic_ppms_text(text: str) -> GenericPpmsParseResult:
    """Read every PPMS data column, preserving text columns as Unicode arrays."""
    header_rows, headings, rows = split_ppms_sections(text)
    if not headings:
        raise DataImportError("The PPMS [Data] section has no column headings.")
    padded = [
        [row[index].strip() if index < len(row) else "" for index in range(len(headings))]
        for row in rows
    ]
    columns: dict[str, NDArray[np.generic]] = {}
    descriptors: list[dict[str, str]] = []
    used_symbols: set[str] = set()
    for index, heading in enumerate(headings):
        key = f"column_{index}"
        symbol = _symbol(heading or key, index)
        base_symbol = symbol
        suffix = 2
        while symbol in used_symbols or symbol in _FUNCTIONS or symbol in _CONSTANTS:
            symbol = f"{base_symbol}_{suffix}"
            suffix += 1
        used_symbols.add(symbol)
        values = [row[index] for row in padded]
        numeric = [parse_float(value) if value else np.nan for value in values]
        nonblank = [value for value in values if value]
        if all(parse_float(value) is not None for value in nonblank):
            array: NDArray[np.generic] = np.asarray(numeric, dtype=float)
            kind = "numeric"
        else:
            array = np.asarray(values, dtype=str)
            kind = "text"
        columns[key] = array
        descriptors.append(
            {
                "key": key,
                "name": heading or f"Column {index + 1}",
                "symbol": symbol,
                "kind": kind,
                "origin": "source",
            }
        )
    return GenericPpmsParseResult(
        columns,
        descriptors,
        _sample_weight(header_rows),
        header_rows,
    )


def _validate_expression(tree: ast.AST, allowed_names: set[str]) -> None:
    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.Mod,
        ast.USub,
        ast.UAdd,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            raise AnalysisError(f"Unsupported expression element: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in allowed_names:
            raise AnalysisError(f"Unknown variable or function: {node.id}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
                raise AnalysisError("Only the listed mathematical functions may be called.")
            if node.keywords:
                raise AnalysisError("Keyword arguments are not supported in expressions.")


def evaluate_expression(
    expression: str,
    variables: Mapping[str, NDArray[np.generic] | float],
) -> NDArray[np.generic]:
    """Evaluate a NumPy-style expression using a strict arithmetic whitelist."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise AnalysisError(f"Invalid expression: {exc.msg}") from exc
    namespace: dict[str, Any] = {**_FUNCTIONS, **_CONSTANTS, **variables}
    _validate_expression(tree, set(namespace))
    try:
        with np.errstate(all="ignore"):
            result = eval(
                compile(tree, "<PPMS expression>", "eval"), {"__builtins__": {}}, namespace
            )
    except Exception as exc:
        raise AnalysisError(f"Expression calculation failed: {exc}") from exc
    return np.asarray(result)


def _parameter_names(expression: str) -> tuple[ast.Expression, list[str]]:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise AnalysisError(f"Invalid fit formula: {exc.msg}") from exc
    names: list[str] = []
    name_nodes = sorted(
        (node for node in ast.walk(tree) if isinstance(node, ast.Name)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for node in name_nodes:
        if (
            isinstance(node, ast.Name)
            and node.id not in {"x", *_FUNCTIONS, *_CONSTANTS}
            and node.id not in names
        ):
            names.append(node.id)
    if not names:
        raise AnalysisError("The fit formula must contain at least one parameter.")
    _validate_expression(tree, {"x", *names, *_FUNCTIONS, *_CONSTANTS})
    return tree, names


def fit_user_expression(
    x: NDArray[np.generic],
    y: NDArray[np.generic],
    expression: str,
    minimum_x: float,
    maximum_x: float,
    *,
    cancel_event: Any = None,
) -> ExpressionFit:
    """Fit a formula such as ``a*x+b`` and infer its parameter names."""
    from scipy.optimize import curve_fit

    x_values = np.asarray(x, dtype=float)
    y_values = np.asarray(y, dtype=float)
    mask = (
        np.isfinite(x_values)
        & np.isfinite(y_values)
        & (x_values >= minimum_x)
        & (x_values <= maximum_x)
    )
    fit_x = x_values[mask]
    fit_y = y_values[mask]
    tree, names = _parameter_names(expression)
    if len(fit_x) <= len(names):
        raise AnalysisError("The selected x range contains too few finite data points.")
    code = compile(tree, "<PPMS fit formula>", "eval")

    def model(model_x: NDArray[np.float64], *parameters: float) -> NDArray[np.float64]:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Curve fit stopped")
        namespace: dict[str, Any] = {
            **_FUNCTIONS,
            **_CONSTANTS,
            "x": model_x,
            **dict(zip(names, parameters, strict=True)),
        }
        with np.errstate(all="ignore"):
            return np.asarray(
                eval(code, {"__builtins__": {}}, namespace),
                dtype=float,
            )

    try:
        parameters, covariance = curve_fit(
            model,
            fit_x,
            fit_y,
            p0=np.ones(len(names)),
            maxfev=50_000,
        )
    except Exception as exc:
        raise AnalysisError(f"Curve fitting failed: {exc}") from exc
    y_fit = model(fit_x, *parameters)
    residual_sum = float(np.sum((fit_y - y_fit) ** 2))
    total_sum = float(np.sum((fit_y - np.mean(fit_y)) ** 2))
    r_squared = 1.0 - residual_sum / total_sum if total_sum > 0 else 1.0
    errors = np.sqrt(np.maximum(np.diag(covariance), 0))
    return ExpressionFit(names, parameters, errors, fit_x, y_fit, r_squared)
