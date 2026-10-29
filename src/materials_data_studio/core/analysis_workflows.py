"""Versioned, whitelisted analysis recipes and scalar result projections."""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from materials_data_studio.core.models import Dataset
from materials_data_studio.core.processing import recalculate_columns, record_operation
from materials_data_studio.version import __version__

OPERATIONS = ("settings", "background", "clear_background", "derive_column", "recalculate_columns", "expression_fit", "low_temperature", "debye_einstein", "arrhenius", "mott_vrh", "curie_weiss")
SETTINGS = {"normalization", "scale", "offset", "wavelength_a", "manual_split_index", "selected_segment", "formula", "sample_mass_mg", "sample_weight_mg", "molar_mass_g_mol", "sample_moles", "atoms_per_formula_unit"}


def validate_recipe(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("format") != "mds-analysis-recipe" or value.get("version") != 1:
        raise ValueError("Unsupported analysis recipe format")
    steps = value.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise ValueError("A recipe must contain 1–100 ordered operations")
    for step in steps:
        if not isinstance(step, dict) or step.get("operation") not in OPERATIONS or not isinstance(step.get("parameters", {}), dict):
            raise ValueError("Recipe contains an unknown operation or invalid parameters")
    return value


def capture_recipe(dataset: Dataset) -> dict[str, Any]:
    sample_keys = {"formula", "sample_mass_mg", "sample_weight_mg", "molar_mass_g_mol", "sample_moles", "atoms_per_formula_unit"}
    steps = [{"operation": "settings", "parameters": {k: copy.deepcopy(v) for k, v in dataset.metadata.items() if k in SETTINGS - sample_keys}}]
    columns = dataset.metadata.get("generic_columns", [])
    for descriptor in columns:
        if descriptor.get("origin") == "calculated" and not descriptor.get("frozen"):
            steps.append({"operation": "derive_column", "parameters": {key: descriptor[key] for key in ("key", "name", "symbol", "expression")}})
    steps.extend({"operation": h["operation"], "parameters": copy.deepcopy(h["parameters"])} for h in dataset.metadata.get("processing_history", []) if h.get("operation") in OPERATIONS and h.get("operation") not in {"settings", "derive_column"})
    return {"format": "mds-analysis-recipe", "version": 1, "application_version": __version__, "module": dataset.module_type.value, "name": dataset.display_name, "column_bindings": {d["key"]: d["name"] for d in columns if d.get("origin") != "calculated"}, "symbol_bindings": {d["symbol"]: d["name"] for d in columns if d.get("origin") != "calculated"}, "steps": steps}


def write_json_atomic(path: Path, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, default=lambda v: v.item() if isinstance(v, np.generic) else v.tolist() if isinstance(v, np.ndarray) else str(v))
    if len(payload.encode("utf-8")) > 2 * 1024**2:
        raise ValueError("Workflow metadata exceeds the 2 MiB limit")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_recipe(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 2 * 1024**2:
        raise ValueError("Recipe exceeds the 2 MiB limit")
    return validate_recipe(json.loads(path.read_text(encoding="utf-8")))


def run_recipe(dataset: Dataset, recipe: dict[str, Any], cancel: Any = None) -> Dataset:
    """Return a detached result; a late failure cannot partly edit the workspace."""
    result = copy.copy(dataset)
    result.raw_data = dict(dataset.raw_data)
    result.processed_data = dict(dataset.processed_data)
    result.metadata = copy.deepcopy(dataset.metadata)
    result.analysis_results = copy.deepcopy(dataset.analysis_results)
    result.warnings = list(dataset.warnings)
    validate_recipe(recipe)
    if recipe.get("module") != dataset.module_type.value:
        raise ValueError("Recipe and dataset modules differ")
    target_columns = result.metadata.get("generic_columns", [])
    column_map = {}
    for source_key, name in recipe.get("column_bindings", {}).items():
        candidates = [d for d in target_columns if d.get("name") == name]
        if len(candidates) != 1:
            raise ValueError(f"Cannot unambiguously bind input column: {name}")
        column_map[source_key] = candidates[0]["key"]
    for step in recipe["steps"]:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Recipe stopped; original dataset unchanged")
        operation, parameters = step["operation"], copy.deepcopy(step.get("parameters", {}))
        if operation == "settings":
            if set(parameters) - SETTINGS:
                raise ValueError("Recipe settings contain unsupported fields")
            for key, value in parameters.items():
                if key in {"scale", "offset", "wavelength_a", "sample_mass_mg", "sample_weight_mg", "molar_mass_g_mol", "sample_moles", "atoms_per_formula_unit"}:
                    number = float(value)
                    if not np.isfinite(number) or (key not in {"scale", "offset"} and number <= 0):
                        raise ValueError(f"Invalid physical setting: {key}")
                if key == "normalization":
                    from materials_data_studio.core.models import NormalizationMode
                    NormalizationMode(value)
                if key == "manual_split_index" and value is not None and not 0 <= int(value) < len(next(iter(result.raw_data.values()))):
                    raise ValueError("Manual split lies outside the target dataset")
            result.metadata.update(parameters)
            # Current results belong to the previous input settings.
            from materials_data_studio.core.processing import invalidate_results
            invalidate_results(result, "Recipe processing settings applied")
        elif operation == "background":
            from materials_data_studio.modules.xrd.background import arpls_background
            result.processed_data["background"] = arpls_background(result.raw_data["intensity"], **parameters, cancel_event=cancel)
            result.metadata["background_enabled"] = True
        elif operation == "clear_background":
            result.processed_data.pop("background", None)
            result.metadata["background_enabled"] = False
        elif operation == "recalculate_columns":
            recalculate_columns(result)
        elif operation == "derive_column":
            from uuid import uuid4

            from materials_data_studio.modules.ppms.generic import evaluate_expression
            descriptors = result.metadata.setdefault("generic_columns", [])
            variables: dict[str, Any] = {d["symbol"]: result.array(d["key"]) for d in descriptors if d.get("kind") != "text"}
            for symbol, source_name in recipe.get("symbol_bindings", {}).items():
                matching = [d for d in descriptors if d.get("name") == source_name]
                if len(matching) != 1:
                    raise ValueError(f"Cannot bind expression symbol: {symbol}")
                variables[symbol] = result.array(matching[0]["key"])
            variables.update(sample_weight_mg=float(result.metadata.get("sample_weight_mg", 0)), sample_moles=float(result.metadata.get("sample_moles", 0)))
            values = np.asarray(evaluate_expression(parameters["expression"], variables), dtype=float)
            row_count = len(next(iter(result.raw_data.values())))
            if values.ndim == 0:
                values = np.full(row_count, values.item())
            if values.shape != (row_count,):
                raise ValueError("Calculated recipe column changed the row count")
            existing = next((d for d in descriptors if d.get("symbol") == parameters["symbol"]), None)
            if existing is not None and (existing.get("origin") != "calculated" or existing.get("frozen")):
                raise ValueError("Recipe cannot overwrite an imported or explicitly frozen column")
            key = existing["key"] if existing else parameters.get("key", "calculated_" + uuid4().hex)
            result.processed_data[key] = values
            if existing is None:
                descriptors.append({**parameters, "key": key, "kind": "numeric", "origin": "calculated"})
            else:
                existing.update(expression=parameters["expression"], stale=False)
            column_map[parameters.get("key", key)] = key
        else:
            x_key, y_key = parameters.pop("x"), parameters.pop("y")
            x_key, y_key = column_map.get(x_key, x_key), column_map.get(y_key, y_key)
            error_key = parameters.pop("sigma", None)
            error_key = column_map.get(error_key, error_key)
            if x_key == "__mpms_temperature" and y_key == "__mpms_inverse":
                from materials_data_studio.modules.mpms.models import BranchSplit
                from materials_data_studio.modules.mpms.mt_analysis import (
                    automatic_split,
                    calculate_susceptibility,
                    split_branches,
                )
                t = np.asarray(result.raw_data["temperature_k"], dtype=float)
                _chi, inv = calculate_susceptibility(result.raw_data["moment_emu"], result.raw_data["field_oe"], sample_weight_mg=float(result.metadata["sample_weight_mg"]), molar_mass_g_mol=float(result.metadata["molar_mass_g_mol"]))
                split = BranchSplit(automatic_split(t).automatic_index, result.metadata.get("manual_split_index"))
                index = 1 if parameters.get("branch") == "FC" else 0
                x, y = (t, inv) if result.metadata.get("measurement_parent") else (split_branches(t, split)[index], split_branches(inv, split)[index])
                x, y, sigma = np.asarray(x), np.asarray(y), None
            elif x_key == "__heat_temperature" and y_key == "__heat_cp_molar":
                from materials_data_studio.modules.ppms.heat_capacity import (
                    average_adjacent_heat_capacity,
                    convert_to_molar_heat_capacity,
                    segment_heat_capacity,
                )
                t = np.asarray(result.raw_data["temperature_k"], dtype=float)
                averaged = ({k: np.asarray(v, dtype=float) for k, v in result.raw_data.items()}
                            if result.metadata.get("measurement_branch", {}).get("average_pairs") is False
                            else average_adjacent_heat_capacity(t, {k: v for k, v in result.raw_data.items() if k != "temperature_k" and v.shape == t.shape}))
                cp, err = convert_to_molar_heat_capacity(averaged["cp_raw_uj_k"], averaged["cp_raw_error_uj_k"], sample_mass_mg=float(result.metadata["sample_mass_mg"]), molar_mass_g_mol=float(result.metadata["molar_mass_g_mol"]))
                segments = segment_heat_capacity(averaged["temperature_k"], averaged["field_oe"])
                selected = segments[min(int(result.metadata.get("selected_segment", 0)), len(segments)-1)]
                indices = np.arange(len(cp), dtype=np.int64) if result.metadata.get("measurement_parent") else selected.indices
                override = result.metadata.get("manual_segment_bounds", {}).get(str(selected.segment_id))
                if override:
                    indices = np.arange(max(0, int(override[0])), min(len(cp), int(override[1])+1))
                x, y = averaged["temperature_k"][indices], cp[indices]
                sigma = err[indices] if error_key else None
                if operation == "low_temperature":
                    parameters["atoms_per_formula_unit"] = float(result.metadata.get("atoms_per_formula_unit", 0)) or None
                if parameters.pop("use_sample_atoms", False):
                    parameters["constrain_atoms"] = float(result.metadata.get("atoms_per_formula_unit", 0)) or None
            elif x_key == "__transport_temperature":
                from materials_data_studio.modules.ppms.resistivity import (
                    geometry_factor,
                    segment_transport,
                    source_resistivity_to_ohm_cm,
                )
                bridge = int(parameters.pop("bridge"))
                resistivity = parameters.pop("vertical_source", "resistivity") == "resistivity"
                setting = result.metadata["bridge_settings"][str(bridge)]
                t, field = result.array("temperature_k"), result.array("field_oe")
                values = np.asarray(result.array(f"bridge_{bridge}_{setting['source']}"), dtype=float)
                error_name = f"bridge_{bridge}_standard_deviation"
                err = np.asarray(result.array(error_name), dtype=float) if error_name in result.raw_data else None
                if resistivity:
                    if setting["kind"] == "resistivity":
                        factor = {"ohm_cm": 1., "ohm_m": 100., "mohm_cm": 1e-3}[setting["unit"]]
                        values = source_resistivity_to_ohm_cm(values, setting["unit"])
                    else:
                        factor = geometry_factor("width_thickness_length", width_cm=float(setting["width_cm"]), thickness_cm=float(setting["thickness_cm"]), length_cm=float(setting["length_cm"]))
                        values = values * factor
                    if err is not None:
                        err = err * factor
                elif setting["kind"] != "resistance":
                    raise ValueError("Selected bridge is already resistivity, not raw resistance")
                segments = segment_transport(t, field, mode="rho_t", measurement_order=result.array("measurement_order"))
                selected = segments[min(int(result.metadata.get("selected_segment", 0)), len(segments)-1)]
                indices = np.arange(len(t), dtype=np.int64) if result.metadata.get("measurement_parent") else selected.indices
                override = result.metadata.get("manual_segment_bounds", {}).get(str(selected.segment_id))
                if override:
                    indices = np.arange(max(0, int(override[0])), min(len(t), int(override[1])+1))
                x, y = t[indices], values[indices]
                sigma = err[indices] if error_key and err is not None else None
            else:
                x, y = result.array(x_key), result.array(y_key)
                sigma = result.array(error_key) if error_key else None
            if operation == "expression_fit":
                fitted: Any
                from materials_data_studio.modules.ppms.generic import fit_user_expression
                fitted = fit_user_expression(x, y, parameters.pop("expression"), parameters.pop("minimum_x"), parameters.pop("maximum_x"), cancel_event=cancel)
            elif operation == "low_temperature":
                from materials_data_studio.modules.ppms.heat_capacity import fit_low_temperature
                fitted = fit_low_temperature(x, y, error_j_mol_k=sigma, **parameters)
            elif operation == "debye_einstein":
                from materials_data_studio.modules.ppms.heat_capacity import fit_debye_einstein
                fitted = fit_debye_einstein(x, y, errors=sigma, cancel_event=cancel, **parameters)
            elif operation == "arrhenius":
                from materials_data_studio.modules.ppms.resistivity import fit_arrhenius
                fitted = fit_arrhenius(x, y, error=sigma, **parameters)
            elif operation == "mott_vrh":
                from materials_data_studio.modules.ppms.resistivity import fit_mott_vrh
                fitted = fit_mott_vrh(x, y, error=sigma, **parameters)
            else:
                from materials_data_studio.modules.mpms.curie_weiss import fit_curie_weiss
                lower, upper = parameters.pop("minimum_k", -np.inf), parameters.pop("maximum_k", np.inf)
                mask = (x >= lower) & (x <= upper)
                fitted = fit_curie_weiss(x[mask], y[mask], cancel_event=cancel, **parameters)
            stored = dataclasses.asdict(fitted)
            for key, value in list(stored.items()):
                if isinstance(value, np.ndarray):
                    result.processed_data[f"recipe_{operation}_{key}"] = value
                    del stored[key]
            result.analysis_results[f"recipe_{operation}"] = stored
            display_x, display_y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
            if operation == "expression_fit":
                config = step["parameters"]
                mask = np.isfinite(display_x) & np.isfinite(display_y) & (display_x >= config["minimum_x"]) & (display_x <= config["maximum_x"])
                display_x, display_y, calculated = display_x[mask], display_y[mask], fitted.y_fit
            elif operation == "curie_weiss":
                from materials_data_studio.modules.mpms.curie_weiss import curie_weiss_inverse
                calculated = curie_weiss_inverse(display_x, fitted.c, fitted.theta, fitted.chi0)
            else:
                calculated = fitted.fitted_values
                if operation == "low_temperature":
                    display_y = display_y / display_x
                    display_x = display_x**2
                elif operation in {"arrhenius", "mott_vrh"}:
                    display_y = np.log(display_y)
                    display_x = display_x ** (-1 if operation == "arrhenius" else -1 / (step["parameters"].get("dimension", 3) + 1))
            result.processed_data.update(recipe_preview_x=display_x, recipe_preview_observed=display_y, recipe_preview_calculated=calculated)
            result.metadata["recipe_plot"] = {"method": operation, "x_label": "T² (K²)" if operation == "low_temperature" else "1/T (K⁻¹)" if operation == "arrhenius" else "T^(-p)" if operation == "mott_vrh" else x_key,
                                               "y_label": "Cp/T (J mol⁻¹ K⁻²)" if operation == "low_temperature" else "ln(value)" if operation in {"arrhenius", "mott_vrh"} else y_key}
        record_operation(result, operation, step.get("parameters", {}))
    if any(d.get("stale") for d in result.metadata.get("generic_columns", [])):
        result.metadata["analysis_status"] = "Dependent columns remain stale — recalculate required"
    else:
        result.metadata.pop("analysis_status", None)
    result.metadata["last_recipe"] = {"name": recipe.get("name", ""), "version": 1, "application_version": __version__, "input_sha256": dataset.source_hash}
    return result


def recipe_plot(dataset: Dataset):
    from materials_data_studio.core.models import ModuleType, NormalizationMode, PlotStyle
    from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
    if "recipe_preview_x" in dataset.processed_data:
        context = dataset.metadata["recipe_plot"]
        x = np.asarray(dataset.processed_data["recipe_preview_x"], dtype=float)
        return PlotSpec(context["x_label"], context["y_label"], [CurveSpec(x, np.asarray(dataset.processed_data["recipe_preview_observed"], dtype=float), "Observed"), CurveSpec(x, np.asarray(dataset.processed_data["recipe_preview_calculated"], dtype=float), context["method"], PlotStyle(color="#dc2626", line_style="--"))])
    if dataset.module_type == ModuleType.XRD:
        from materials_data_studio.modules.xrd.transforms import process_intensity
        x, y = np.asarray(dataset.raw_data["x"], dtype=float), np.asarray(dataset.raw_data["intensity"], dtype=float)
        processed = process_intensity(x, y, normalization=NormalizationMode(dataset.metadata.get("normalization", "none")), scale=float(dataset.metadata.get("scale", 1)), offset=float(dataset.metadata.get("offset", 0)), background=dataset.processed_data.get("background"), subtract_background=bool(dataset.metadata.get("background_enabled")))
        return PlotSpec("Source X", "Intensity", [CurveSpec(x, y, "Raw"), CurveSpec(x, processed, "Recipe result", PlotStyle(color="#dc2626"))])
    raise ValueError("This recipe has no plotted analysis step; inspect the results and processing-history tables")


def result_rows(datasets: list[Dataset], *, include_stale: bool = False) -> list[dict[str, Any]]:
    """Flatten scalar values without losing method, uncertainty or provenance."""
    rows = []
    units = {"activation_energy_ev": "eV", "characteristic_temperature_k": "K", "gamma_j_mol_k2": "J mol^-1 K^-2", "beta_j_mol_k4": "J mol^-1 K^-4", "debye_temperature_k": "K", "theta": "K"}
    for dataset in datasets:
        results = dataset.analysis_results
        sources = [(results, "Current")]
        if include_stale and dataset.metadata.get("stale_analysis"):
            sources.append((dataset.metadata["stale_analysis"]["results"], "Stale — do not use without recalculation"))
        for entries, status in sources:
            for method, result in entries.items():
                if not isinstance(result, dict):
                    continue
                parameters = result.get("parameters", {})
                if isinstance(parameters, (list, tuple)):
                    parameters = dict(zip(result.get("parameter_names", []), parameters, strict=False))
                scalars = {k: v for k, v in result.items() if isinstance(v, (int, float, str, bool)) or v is None}
                scalars.update(parameters if isinstance(parameters, dict) else {})
                errors = result.get("standard_errors", {})
                if isinstance(errors, (list, tuple)):
                    errors = dict(zip(result.get("parameter_names", list(parameters)), errors, strict=False))
                for key, value in scalars.items():
                    error = errors.get(key) if isinstance(errors, dict) else None
                    if key in {"gamma_j_mol_k2", "beta_j_mol_k4"}:
                        error = result.get(key.split("_")[0] + "_error")
                    for error_key in (key + "_err", key + "_error", key.replace("_ev", "_error_ev"), key.replace("_k", "_error_k")):
                        if error_key != key and error_key in result:
                            error = result[error_key]
                    rows.append({"dataset": dataset.display_name, "sample": dataset.metadata.get("sample_id", ""), "method": method, "parameter": key, "value": value, "standard_error": error, "unit": units.get(key, ""), "status": status,
                                 "conditions": json.dumps({k: v for k, v in result.items() if k in {"point_count", "excluded_count", "minimum_k", "maximum_k", "t_min", "t_max", "branch", "segment_id", "converged", "covariance_warning", "fix_gamma_zero"}}, ensure_ascii=False), "input_sha256": dataset.source_hash})
    return rows
