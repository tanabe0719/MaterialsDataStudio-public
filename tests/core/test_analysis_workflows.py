"""Recipes exercise existing models; no new domain-specific fitting is added."""
import hashlib
import json
import zipfile
from threading import Event

import numpy as np
import pytest

from materials_data_studio.core.analysis_workflows import (
    capture_recipe,
    read_recipe,
    recipe_plot,
    result_rows,
    run_recipe,
    validate_recipe,
    write_json_atomic,
)
from materials_data_studio.core.exceptions import SessionFormatError
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.processing import record_operation
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import load_session, save_session
from materials_data_studio.version import __version__


def recipe(module, operation, parameters):
    return {"format": "mds-analysis-recipe", "version": 1, "module": module, "steps": [{"operation": operation, "parameters": parameters}]}


def test_recipe_settings_atomicity_and_validation(tmp_path):
    dataset = Dataset("demo", ModuleType.XRD, {"x": np.arange(10.), "intensity": np.arange(10.)+1})
    value = capture_recipe(dataset)
    value["steps"][0]["parameters"] = {"normalization": "max_100"}
    detached = run_recipe(dataset, value)
    assert "normalization" not in dataset.metadata
    assert recipe_plot(detached).curves[-1].y.max() == pytest.approx(100)
    value["steps"].append({"operation": "settings", "parameters": {"wavelength_a": -1}})
    with pytest.raises(ValueError, match="physical"):
        run_recipe(dataset, value)
    assert not dataset.metadata and not dataset.processed_data
    stop = Event()
    stop.set()
    with pytest.raises(InterruptedError):
        run_recipe(dataset, capture_recipe(dataset), stop)
    for invalid in ({}, {"format": "mds-analysis-recipe", "version": 9}, recipe("xrd", "exec", {})):
        with pytest.raises(ValueError):
            validate_recipe(invalid)
    path = tmp_path / "recipe.mdsr"
    write_json_atomic(path, capture_recipe(dataset))
    assert read_recipe(path)["module"] == "xrd"


def test_recipe_rebinds_derived_columns_by_name():
    source = Dataset("A", ModuleType.PPMS, {"raw": np.arange(5.)}, metadata={"generic_columns": [
        {"key": "raw", "name": "Signal", "symbol": "a", "kind": "numeric", "origin": "imported"},
        {"key": "calc_uuid", "name": "Double", "symbol": "b", "kind": "numeric", "origin": "calculated", "expression": "a*2"}], "sample_moles": 1})
    target = Dataset("B", ModuleType.PPMS, {"other": np.arange(5.)+1}, metadata={"generic_columns": [
        {"key": "other", "name": "Signal", "symbol": "different_alias", "kind": "numeric", "origin": "imported"}], "sample_moles": 2})
    result = run_recipe(target, capture_recipe(source))
    np.testing.assert_allclose(result.processed_data["calc_uuid"], (np.arange(5.)+1)*2)
    assert result.metadata["sample_moles"] == 2
    assert not target.processed_data


def test_existing_curie_weiss_recipe_uses_target_mass():
    t = np.linspace(100, 300, 50)
    moment = (1/(t+10)+.0002) * 1000 * 1e-5
    dataset = Dataset("CW", ModuleType.MPMS, {"temperature_k": t, "moment_emu": moment, "field_oe": np.full(50, 1000.)}, metadata={"sample_weight_mg": 1, "molar_mass_g_mol": 100})
    value = recipe("mpms", "curie_weiss", {"x": "__mpms_temperature", "y": "__mpms_inverse", "minimum_k": 100, "maximum_k": 300, "c_init": 1, "theta_init": 0, "branch": "ZFC"})
    result = run_recipe(dataset, value)
    assert result.analysis_results["recipe_curie_weiss"]["c"] == pytest.approx(1, rel=1e-5)
    assert recipe_plot(result).curves[-1].x.shape == t.shape
    rows = result_rows([result])
    assert any(row["parameter"] == "c" and row["standard_error"] is not None for row in rows)


def test_existing_heat_recipe_averages_and_converts_per_target():
    t = np.repeat(np.linspace(2, 10, 15), 2)
    cp = .02*t + .0004*t**3
    dataset = Dataset("HC", ModuleType.PPMS, {"temperature_k": t, "field_oe": np.zeros(len(t)), "cp_raw_uj_k": cp*50, "cp_raw_error_uj_k": np.full(len(t), .001)}, metadata={"sample_mass_mg": 5, "molar_mass_g_mol": 100, "atoms_per_formula_unit": 5, "ppms_kind": "heat_capacity"})
    value = recipe("ppms", "low_temperature", {"x": "__heat_temperature", "y": "__heat_cp_molar", "minimum_k": 2, "maximum_k": 10, "fix_gamma_zero": False})
    result = run_recipe(dataset, value)
    fit = result.analysis_results["recipe_low_temperature"]
    assert fit["gamma_j_mol_k2"] == pytest.approx(.02)
    assert fit["beta_j_mol_k4"] == pytest.approx(.0004)
    assert len(recipe_plot(result).curves[0].x) == 15


def test_existing_transport_recipe_uses_target_geometry():
    t = np.linspace(100, 300, 40)
    dataset = Dataset("R", ModuleType.PPMS, {"temperature_k": t, "field_oe": np.zeros(40), "measurement_order": np.arange(40), "bridge_1_resistance": np.exp(200/t)/.002}, metadata={"ppms_kind": "resistivity", "bridge_settings": {"1": {"source": "resistance", "kind": "resistance", "width_cm": .1, "thickness_cm": .01, "length_cm": .5}}})
    value = recipe("ppms", "arrhenius", {"x": "__transport_temperature", "y": "__transport_value", "bridge": 1, "vertical_source": "resistivity", "minimum_k": 100, "maximum_k": 300})
    result = run_recipe(dataset, value)
    assert result.analysis_results["recipe_arrhenius"]["slope"] == pytest.approx(200)
    assert recipe_plot(result).y_label == "ln(value)"


def test_background_recipe_capture_and_roundtrip():
    x = np.linspace(10, 80, 200)
    ds = Dataset("XRD", ModuleType.XRD, {"x": x, "intensity": 10 + np.exp(-((x-40)/2)**2)*100})
    record_operation(ds, "background", {"smoothness": 1000, "max_iterations": 10})
    detached = run_recipe(ds, capture_recipe(ds))
    assert detached.metadata["background_enabled"]
    assert np.isfinite(detached.processed_data["background"]).all()
    assert len(recipe_plot(detached).curves) == 2


def test_session_checks_source_hash_and_preserves_original(tmp_path):
    raw = b"synthetic original"
    target = tmp_path / "test.mdss"
    ds = Dataset("D", ModuleType.XRD, {"x": np.arange(3.)}, source_filename="synthetic.xy", source_bytes=raw, source_hash=hashlib.sha256(raw).hexdigest())
    save_session(ProjectState([ds]), target)
    good = target.read_bytes()
    ds.source_bytes = b"different bytes"
    with pytest.raises(SessionFormatError, match="integrity"):
        save_session(ProjectState([ds]), target)
    assert target.read_bytes() == good
    assert load_session(target).datasets[0].source_bytes == raw
    with zipfile.ZipFile(target) as archive:
        assert json.loads(archive.read("session.json"))["application_version"] == __version__
