"""Persisted scientific dependencies; invalidation is distinct from GUI Undo."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from typing import Any

import numpy as np

from materials_data_studio.core.models import Dataset
from materials_data_studio.version import __version__


def record_operation(dataset: Dataset, operation: str, parameters: dict[str, Any]) -> None:
    history = dataset.metadata.setdefault("processing_history", [])
    history.append({"operation": operation, "parameters": parameters, "input_sha256": dataset.source_hash,
                    "application_version": __version__, "time": datetime.now(UTC).isoformat()})


def invalidate_results(dataset: Dataset, reason: str) -> None:
    """Keep prior fits for inspection, but never silently overlay outdated fits."""
    if dataset.analysis_results:
        dataset.metadata["stale_analysis"] = {"reason": reason, "results": dataset.analysis_results}
        dataset.analysis_results = {}
    dataset.metadata["analysis_status"] = reason + " — recalculate required"
    mark_columns_stale(dataset)


def mark_columns_stale(dataset: Dataset) -> None:
    for descriptor in dataset.metadata.get("generic_columns", []):
        if descriptor.get("origin") == "calculated" and not descriptor.get("frozen"):
            descriptor["stale"] = True


def recalculate_columns(dataset: Dataset) -> None:
    """Topologically evaluate all live formulas; commit only a complete result."""
    from materials_data_studio.modules.ppms.generic import evaluate_expression

    descriptors = dataset.metadata.get("generic_columns", [])
    pending = [d for d in descriptors if d.get("origin") == "calculated" and not d.get("frozen")]
    variables: dict[str, Any] = {
        d["symbol"]: dataset.array(d["key"])
        for d in descriptors if d.get("kind") != "text" and d not in pending
    }
    variables.update(sample_weight_mg=float(dataset.metadata.get("sample_weight_mg", 0)),
                     sample_moles=float(dataset.metadata.get("sample_moles", 0)))
    updates = {}
    symbols = {d["symbol"] for d in pending}
    rows = len(next(iter(dataset.raw_data.values())))
    while pending:
        progressed = False
        for descriptor in pending[:]:
            expression = descriptor["expression"]
            referenced = {n.id for n in ast.walk(ast.parse(expression, mode="eval")) if isinstance(n, ast.Name)}
            if (referenced & symbols) - variables.keys():
                continue
            values = np.asarray(evaluate_expression(expression, variables), dtype=float)
            if values.ndim == 0:
                values = np.full(rows, values.item())
            if values.shape != (rows,):
                raise ValueError(f"{descriptor['name']}: formula changed the row count")
            updates[descriptor["key"]] = values
            variables[descriptor["symbol"]] = values
            pending.remove(descriptor)
            progressed = True
        if not progressed:
            raise ValueError("Calculated columns contain circular or missing dependencies")
    dataset.processed_data.update(updates)
    for descriptor in descriptors:
        descriptor.pop("stale", None)
    dataset.metadata.pop("analysis_status", None)
    record_operation(dataset, "recalculate_columns", {"columns": list(updates)})
