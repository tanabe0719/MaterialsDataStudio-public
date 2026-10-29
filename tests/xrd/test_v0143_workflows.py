"""Doublet propagation and preview layering without changing scientific inputs."""
from __future__ import annotations

import copy
from dataclasses import asdict

import numpy as np
import pytest
from PySide6.QtWidgets import QWidget

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.packaging_smoke import _SMOKE_CIF
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.modules.xrd.cif_backend import CifDocument
from materials_data_studio.modules.xrd.cif_editor import CifEditorDialog
from materials_data_studio.modules.xrd.phase_detection import DetectionSettings, alpha2_position
from materials_data_studio.modules.xrd.phase_models import (
    CalculationSettings,
    Candidate,
    match_peaks,
)
from materials_data_studio.modules.xrd.phase_score import PreparedPeakScore
from materials_data_studio.modules.xrd.phase_search import experimental_data, experimental_signature
from materials_data_studio.modules.xrd.phase_store import PhaseStore
from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
from materials_data_studio.modules.xrd.simulation import display_pattern_data, simulate_cif
from materials_data_studio.modules.xrd.widget import XrdWidget


@pytest.mark.parametrize("width", [None, .03])
def test_doublet_queue_recalculate_and_physical_hkl_preservation(qtbot, tmp_path, width):
    document = CifDocument(tmp_path / "generated.cif", _SMOKE_CIF, _SMOKE_CIF)
    state = ProjectState()
    widget = XrdWidget(state)
    qtbot.addWidget(widget)
    settings = dict(wavelength_a=1.540598, radiation="Cu Kα1/2", two_theta_range=(20,90), fwhm_deg=width,
                    profile_step_deg=.005, relative_scale=1., vertical_offset=0., target_dataset_id=None)
    widget._queue_cif(document.source_path, document, settings)
    qtbot.waitUntil(lambda: len(state.datasets) == 1, timeout=30000)
    dataset = state.datasets[0]
    expected = simulate_cif(document, wavelength_a=1.540598, radiation="Cu Kα1/2", two_theta_range=(20,90), fwhm_deg=width,profile_step_deg=.005)
    px, py = display_pattern_data(expected,"Cu Kα1/2")
    np.testing.assert_array_equal(dataset.array("x"),px)
    np.testing.assert_array_equal(dataset.array("intensity"),py)
    assert len(dataset.metadata["stick_two_theta_deg"]) == len(expected.two_theta_deg)
    assert len(dataset.metadata["stick_hkl_labels"]) == len(expected.two_theta_deg)
    if width is None:
        assert len(px) == 2 * len(expected.two_theta_deg)
        assert np.any(np.isclose(px,alpha2_position(expected.two_theta_deg[0])))
    else:
        center = float(expected.two_theta_deg[0])
        assert np.interp(alpha2_position(center),px,py) > .45 * np.interp(center,px,py)
    # The exact re-import path used by Apply Wavelength to All must keep radiation.
    from materials_data_studio.ui.tasks import _LIVE
    qtbot.waitUntil(lambda: not any(t.owner is widget for t in _LIVE),timeout=30000)
    widget._recalculate_cif_dataset(dataset)
    qtbot.waitUntil(lambda: not any(t.owner is widget for t in _LIVE),timeout=30000)
    assert dataset.metadata["radiation"] == "Cu Kα1/2"
    np.testing.assert_array_equal(dataset.array("intensity"),py)
    # Switching with unchanged primary wavelength must work for stick-only data too.
    widget.wavelength_preset.setCurrentText("Cu Kα1")
    assert dataset.metadata["radiation"] == "Cu Kα1"
    widget.wavelength_preset.setCurrentText("Cu Kα1/2")
    np.testing.assert_array_equal(dataset.array("intensity"),py)


def test_cif_editor_retains_doublet_and_manual_wavelength(qtbot,tmp_path):
    dialog=CifEditorDialog(CifDocument(tmp_path/'generated.cif',_SMOKE_CIF,_SMOKE_CIF),simulation_settings={"radiation":"Cu Kα1/2"})
    qtbot.addWidget(dialog)
    assert dialog.radiation.currentText()=="Cu Kα1/2"
    dialog.wavelength.setValue(1.55)
    assert dialog.radiation.currentText()=="Custom"


def test_preview_layers_cached_updates_and_main_background(qtbot,tmp_path):
    x=np.linspace(10,90,8001)
    background=np.full_like(x,20.)
    y=background+100*np.exp(-.5*((x-40)/.08)**2)
    dataset=Dataset("Generated",ModuleType.XRD,{"x":x,"intensity":y})
    parent=QWidget()
    qtbot.addWidget(parent)
    window=PhaseSearchWindow(ProjectState(datasets=[dataset]),parent,store=PhaseStore(tmp_path/'store'))
    qtbot.addWidget(window)
    window.form.numeric['lower'].setValue(10)
    signature=experimental_signature(dataset)
    peaks=[dict(position=40.,intensity=100.,included=True)]
    window.analysis=dict(signature=signature,settings=asdict(DetectionSettings(**window.form.detection_values(),doublet=True)),wavelength=1.540598,baseline_x=[10,90],baseline=[20,20])
    window._peaks_ready(peaks)
    settings=window.form.settings()
    result=dict(candidate=Candidate('1000000','COD',formula='NaCl').to_dict(),settings=asdict(settings),pattern=dict(positions=[40.],intensities=[100.]),
                match=match_peaks(peaks,[40.],[100.],settings))
    window.results=[result]
    window.matched_peaks=copy.deepcopy(peaks)
    window.matched_signature=signature
    window._candidate_rows()
    window.candidate_table.selectRow(0)
    for _ in range(2):
        window.preview()
        spec=window.plot._materials_plot_spec
        assert [c.label for c in spec.curves]==['Before automatic background','Observed','Matched','Calculated sticks']
        assert [item.zValue() for item in window.plot._materials_curve_items]==[0,1,2,3]
        np.testing.assert_allclose(spec.curves[0].y,y)
        np.testing.assert_allclose(spec.curves[1].y,y-background,atol=1e-12)
    dataset.processed_data['background']=background
    dataset.metadata['background_enabled']=True
    window.analysis={}
    window.preview()
    spec=window.plot._materials_plot_spec
    assert spec.curves[0].label=='Before automatic background'
    np.testing.assert_allclose(spec.curves[0].y,y)
    # Input changes must still invalidate results even after preview optimizations.
    assert not window.current_results()


@pytest.mark.parametrize("resolution", [0.,.08])
def test_isolated_score_fast_path_exact_and_sorted_data_copy(resolution):
    settings=CalculationSettings(lower=10,resolution=resolution)
    positions=np.array([20.,40.,60.])
    intensity=np.array([100.,25.,50.])
    peaks=[dict(position=float(p+.03),intensity=float(i)) for p,i in zip(positions,intensity,strict=True)]
    assert PreparedPeakScore(peaks,settings).score(positions,intensity)==match_peaks(peaks,positions.tolist(),intensity.tolist(),settings)['score']
    dataset=Dataset('Generated',ModuleType.XRD,{'x':positions,'intensity':intensity})
    x,y=experimental_data(dataset,1.540598)
    x[0]=0
    y[0]=0
    assert dataset.array('x')[0]==20 and dataset.array('intensity')[0]==100


def test_signature_matches_original_bytes_for_strided_inputs_and_edits():
    import hashlib
    import json
    dataset=Dataset('Generated',ModuleType.XRD,{'x':np.arange(6.),'intensity':np.arange(6.)})
    dataset.processed_data['intensity']=np.arange(12.,dtype=np.float64)[::2]
    expected=hashlib.sha256()
    expected.update(dataset.dataset_id.encode())
    expected.update(json.dumps({k:dataset.metadata.get(k) for k in ('source_axis','wavelength_a','tof_calibration','background_enabled')},sort_keys=True).encode())
    for key in ('x','intensity'):
        expected.update(np.asarray(dataset.array(key),dtype=np.float64).tobytes())
    assert experimental_signature(dataset)==expected.hexdigest()
    previous=experimental_signature(dataset)
    dataset.processed_data['intensity'][0]=1.
    assert experimental_signature(dataset)!=previous
