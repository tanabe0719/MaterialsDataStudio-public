"""Checked scientific data are independent of the exported visible graph."""
from pathlib import Path

import h5py
import numpy as np

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.plotting.igor_export import (
    add_checked_dataset_waves,
    graph_export_state_from_plot_spec,
)
from materials_data_studio.plotting.igor_h5xp import IgorH5xpExporter
from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_h5xp import load_h5xp
from materials_data_studio.tools.graph_workflows import from_plot_spec


def test_checked_raw_derived_processed_waves_do_not_add_traces(tmp_path: Path):
    visible = Dataset("Synthetic magnetic", ModuleType.MPMS,
                      {"temperature_k": np.array([10., 20., 30.]), "field_oe": np.full(3, 100.), "moment_emu": np.array([1., 2., 3.])},
                      metadata={"sample_weight_mg": 10., "molar_mass_g_mol": 20.})
    visible.processed_data["smoothed_moment_emu"] = np.array([1.1, 2., 2.9])
    checked_other = Dataset("Checked other", ModuleType.MPMS, {"temperature_k": np.array([4., 5.]), "moment_emu": np.array([7., 8.])})
    hidden = Dataset("Not checked", ModuleType.MPMS, {"hidden": np.ones(3)}, visible=False)
    state = graph_export_state_from_plot_spec(PlotSpec("T (K)", "Moment (emu)", [CurveSpec(visible.array("temperature_k"), visible.array("moment_emu"), "Only plotted curve")]), "MPMS")
    add_checked_dataset_waves(state, [visible, checked_other, hidden])
    assert len(state.traces) == 1
    assert not any("Not checked" in wave.folder for wave in state.waves)
    chi = next(wave.values for wave in state.waves if wave.name == "Derived_Chi_emu_mol_Oe")
    np.testing.assert_allclose(chi, [20., 40., 60.])
    result = IgorH5xpExporter().export_graph(state, tmp_path / "checked.h5xp")
    with h5py.File(result.destination) as handle:
        procedure = handle["Recreation/Recreation Procedures"][()].decode()
        assert procedure.count("Display /") == 1 and "AppendToGraph" not in procedure
        folders = handle["Packed Data/MDS/MPMS/Data"]
        assert any("Raw_moment_emu" in group for group in folders.values())
    restored = load_h5xp(result.destination)
    assert len(restored.current.graphs[0].traces) == 1
    assert len(restored.source.waves) == result.wave_count
    editor = from_plot_spec(PlotSpec("T", "Moment", [CurveSpec(visible.array("temperature_k"), visible.array("moment_emu"), "Only plotted curve")]), extra_waves=state.waves)
    assert len(editor.current.graphs[0].traces) == 1
    waves = editor.render_source().waves
    assert len(waves) == result.wave_count
    derived = next(w for w in waves if w.name == "Derived_Chi_emu_mol_Oe")
    assert derived.data is chi
    np.testing.assert_allclose(derived.data, [20., 40., 60.])
    saved = tmp_path / "checked.mdsg"
    save_graph_document(saved, editor)
    reopened = load_graph_document(saved).render_source()
    assert len(reopened.graphs[0].traces) == 1
    np.testing.assert_allclose(reopened.wave(derived.igor_path).data, chi)


def test_wave_only_and_multidimensional_complex_text_columns(tmp_path: Path):
    d = Dataset("Checked", ModuleType.XRD, {"matrix": np.arange(6.).reshape(2, 3), "complex": np.array([1+2j, 3+4j]), "labels": np.array(["one", "two"]), "flags": np.array([True, False])})
    state = graph_export_state_from_plot_spec(PlotSpec("X", "Y"), "XRD")
    add_checked_dataset_waves(state, [d])
    result = IgorH5xpExporter().export_graph(state, tmp_path / "data.h5xp")
    assert result.graph_count == 0
    loaded = load_h5xp(result.destination)
    assert not loaded.current.graphs
    by_name = {w.name: w for w in loaded.source.waves}
    np.testing.assert_array_equal(by_name["Raw_complex"].data, [1+2j, 3+4j])
    np.testing.assert_array_equal(by_name["Raw_matrix"].data, np.arange(6.).reshape(2, 3))
    np.testing.assert_array_equal(by_name["Raw_labels"].data, ["one", "two"])
