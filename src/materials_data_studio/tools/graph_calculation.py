"""Bounded, whitelisted pointwise calculations on immutable working waves."""

from __future__ import annotations

import ast
import re
from typing import Any

import numpy as np
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from materials_data_studio.modules.ppms.generic import _CONSTANTS, _FUNCTIONS, _validate_expression
from materials_data_studio.tools.graph_document import EditableGraphDocument, GraphState
from materials_data_studio.tools.graph_waves import make_wave, put_waves
from materials_data_studio.tools.pxp_model import PxpWave


def wave_symbols(document: EditableGraphDocument) -> dict[str, PxpWave]:
    """Stable numbered aliases plus unambiguous names; functions cannot be shadowed."""
    waves = [w for w in document.render_source().waves if w.data.dtype.kind in "biufc" and 1 <= w.data.ndim <= 4]
    result = {f"w{i + 1}": wave for i, wave in enumerate(waves)}
    reserved = {*_FUNCTIONS, *_CONSTANTS, *result}
    names = [re.sub(r"\W", "_", w.name) for w in waves]
    for wave, name in zip(waves, names, strict=True):
        if name.isidentifier() and name not in reserved and names.count(name) == 1:
            result[name] = wave
    return result


def calculate_wave(document: EditableGraphDocument, name: str, expression: str, *, alignment: str = "strict") -> GraphState:
    if len(expression) > 4096:
        raise ValueError("Expressions are limited to 4096 characters.")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 256:
        raise ValueError("Expression is too complex.")
    symbols = wave_symbols(document)
    used = {node.id: symbols[node.id] for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in symbols}
    if not used:
        raise ValueError("Use at least one numeric wave in the expression.")
    template = next(iter(used.values()))
    if alignment not in {"strict", "index", "interpolate"}:
        raise ValueError("Unknown coordinate alignment")
    if alignment != "interpolate" and any(w.shape != template.shape for w in used.values()):
        raise ValueError("Referenced waves must have identical dimensions and lengths; implicit broadcasting is not used.")
    if alignment == "strict" and any(w.dimension_scaling != template.dimension_scaling or w.dimension_units != template.dimension_units or w.data_units != template.data_units for w in used.values()):
        raise ValueError("Wave coordinates or units differ. Choose explicit point-index arithmetic or coordinate interpolation.")
    values = {key: w.data for key, w in used.items()}
    if alignment == "interpolate":
        if any(w.data.ndim != 1 or w.dimension_units != template.dimension_units for w in used.values()):
            raise ValueError("Interpolation requires 1D waves with matching coordinate units")
        target = template.coordinate(0)
        for key, wave in used.items():
            coords = wave.coordinate(0)
            if len(coords) < 2 or not (np.all(np.diff(coords) > 0) or np.all(np.diff(coords) < 0)):
                raise ValueError("Interpolation requires distinct monotonic coordinates")
            order = np.argsort(coords)
            values[key] = np.interp(target, coords[order], np.asarray(wave.data[order], dtype=float), left=np.nan, right=np.nan)
    # Existing evaluator rejects attributes, subscripts, imports, builtins and calls
    # to user-defined functions. Only the explicitly listed math functions run.
    namespace = {**_FUNCTIONS, **_CONSTANTS, **values}
    _validate_expression(tree, set(namespace))
    operations = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply,
                  ast.Div: np.divide, ast.Pow: np.power, ast.Mod: np.remainder}
    def evaluate(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ValueError("Only numeric constants are allowed.")
            return float(node.value)
        if isinstance(node, ast.Name):
            return namespace[node.id]
        if isinstance(node, ast.UnaryOp):
            return np.negative(evaluate(node.operand)) if isinstance(node.op, ast.USub) else np.positive(evaluate(node.operand))
        if isinstance(node, ast.BinOp):
            return operations[type(node.op)](evaluate(node.left), evaluate(node.right))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return _FUNCTIONS[node.func.id](*(evaluate(arg) for arg in node.args))
        raise ValueError("Unsupported arithmetic expression.")
    with np.errstate(all="ignore"):
        data = np.asarray(evaluate(tree))
    if data.shape != template.shape or data.dtype.kind not in "biufc":
        raise ValueError("Calculation must return a numeric array of the input shape.")
    wave = make_wave(name, data)
    from dataclasses import replace
    wave = replace(wave, dimension_scaling=template.dimension_scaling,
                   dimension_units=template.dimension_units,
                   wave_note="MDS calculation (" + alignment + "): " + expression)
    return put_waves(document, [wave])


class WaveCalculationDialog(QDialog):
    def __init__(self, parent: Any, document: EditableGraphDocument) -> None:
        super().__init__(parent)
        self.document = document
        self.result_state: GraphState | None = None
        self.setWindowTitle("Calculate Wave")
        self.resize(660, 530)
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("Create a new wave by pointwise arithmetic. All input shapes must match."))
        form = QFormLayout()
        self.name = QLineEdit("calculated_wave")
        self.expression = QLineEdit()
        self.expression.setPlaceholderText("e.g. (w1 - w2) / maximum(abs(w2), 1e-12)")
        form.addRow("New wave name", self.name)
        form.addRow("Expression", self.expression)
        self.alignment = QComboBox()
        self.alignment.addItem("Require matching coordinates and units", "strict")
        self.alignment.addItem("Explicit point-index arithmetic", "index")
        self.alignment.addItem("Interpolate onto first wave (no extrapolation)", "interpolate")
        form.addRow("Coordinate alignment", self.alignment)
        outer.addLayout(form)
        outer.addWidget(QLabel("Available symbols (select and copy):"))
        self.symbols = QPlainTextEdit()
        self.symbols.setReadOnly(True)
        self.symbols.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.symbols.setPlainText("\n".join(f"{symbol} = {wave.igor_path}    shape {wave.shape}" for symbol, wave in wave_symbols(document).items()))
        outer.addWidget(self.symbols, 1)
        help_text = QLabel("Functions: " + ", ".join(_FUNCTIONS) + "\nConstants: pi, e. Non-finite results remain NaN/Inf; they are not silently removed.")
        help_text.setWordWrap(True)
        outer.addWidget(help_text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.calculate)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def calculate(self) -> None:
        try:
            self.result_state = calculate_wave(self.document, self.name.text().strip(), self.expression.text(), alignment=str(self.alignment.currentData()))
        except Exception as exc:
            QMessageBox.warning(self, "Calculate Wave", str(exc))
            return
        self.accept()
