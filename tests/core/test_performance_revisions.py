"""Safety and scientific equivalence for shared scientific revisions."""
import copy
import json
import zipfile
from dataclasses import replace

import numpy as np
import pytest

from materials_data_studio.core.array_revision import ArrayRevisionCache
from materials_data_studio.core.immutable_json import freeze, thaw
from materials_data_studio.core.memory import retained_bytes
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import load_session, save_session
from materials_data_studio.modules.ppms.heat_capacity import _debye_integral, _debye_integrals
from materials_data_studio.tools.graph_document import (
    EditableGraphDocument,
    load_graph_document,
    save_graph_document,
)
from materials_data_studio.tools.graph_waves import make_wave
from materials_data_studio.tools.pxp_model import PxpDocument, PxpGraph, PxpTrace
from materials_data_studio.ui.undo_history import WorkspaceFingerprinter, WorkspaceSnapshot


def test_frozen_results_are_json_compatible_and_shared_across_history():
    results = freeze([{"pattern": {"positions": list(range(20000))}, "score": 90}])
    assert copy.deepcopy(results) is results
    assert json.loads(json.dumps(results)) == results
    with pytest.raises(TypeError, match="Immutable"):
        results[0]["pattern"]["positions"].append(3)
    state = ProjectState([Dataset("Synthetic", ModuleType.XRD, {"x": np.arange(10.)})])
    modules = {"xrd": {"phase_search": {"results": results, "checked_phases": ["one"]}}}
    printer = WorkspaceFingerprinter()
    before = WorkspaceSnapshot.capture(state, modules, digest=printer.digest(state, modules))
    modules["xrd"]["phase_search"]["checked_phases"] = ["two"]
    after = WorkspaceSnapshot.capture(state, modules, digest=printer.digest(state, modules))
    assert before.digest != after.digest
    assert before.module_states["xrd"]["phase_search"]["results"] is after.module_states["xrd"]["phase_search"]["results"]
    assert retained_bytes(after, [before]) < 10000
    independent = thaw(results)
    independent[0]["pattern"]["positions"].append(20001)
    assert len(results[0]["pattern"]["positions"]) == 20000


def test_revision_cache_rejects_mutable_views_and_bounds_retained_memory():
    base = np.arange(100.)
    view = base[:10]
    view.setflags(write=False)
    cache = ArrayRevisionCache(maximum_bytes=1024)
    calls = []
    def calculate():
        calls.append(True)
        return float(view.sum())
    assert cache.get_or_create((view,), "sum", calculate) == 45
    base[0] = 20
    assert cache.get_or_create((view,), "sum", calculate) == 65
    base.setflags(write=False)
    cache.get_or_create((view,), "sum", calculate)
    cache.get_or_create((view,), "sum", calculate)
    assert len(calls) == 3
    cache.get_or_create((view,), "large", lambda: np.zeros(10000))
    assert cache.retained_bytes <= 1024


def test_readonly_memoryview_of_mutable_storage_is_not_cached():
    storage = bytearray(np.arange(4., dtype=np.float64).tobytes())
    view = np.frombuffer(memoryview(storage).toreadonly(), dtype=np.float64)
    cache = ArrayRevisionCache()
    assert cache.get_or_create((view,), "sum", lambda: view.sum()) == 6
    np.frombuffer(storage, dtype=np.float64)[0] = 10
    assert cache.get_or_create((view,), "sum", lambda: view.sum()) == 16


def test_session_preserves_branch_buffer_and_original_source_sharing(tmp_path):
    source = b"synthetic instrument data\n" * 100
    parent = Dataset("Parent", ModuleType.PPMS, {"temperature_k": np.arange(200.), "field_oe": np.zeros(200)}, source_bytes=source)
    children = []
    for start, stop in ((0, 100), (100, 200), (20, 80)):
        child = Dataset("Branch", ModuleType.PPMS, {}, source_bytes=source)
        child.raw_data = {key: array[start:stop] for key, array in parent.raw_data.items()}
        children.append(child)
    path = tmp_path / "shared.mdss"
    save_session(ProjectState([parent, *children]), path)
    with zipfile.ZipFile(path) as archive:
        assert len([name for name in archive.namelist() if name.startswith("sources/")]) == 1
    restored = load_session(path).datasets
    for original, child in zip(children, restored[1:], strict=True):
        np.testing.assert_array_equal(child.array("temperature_k"), original.array("temperature_k"))
        assert np.shares_memory(restored[0].array("temperature_k"), child.array("temperature_k"))
        assert child.source_bytes is restored[0].source_bytes
        assert not child.array("temperature_k").flags.writeable
    # Independent style/analysis remain independent despite shared raw storage.
    restored[1].metadata["own"] = True
    assert "own" not in restored[2].metadata


def test_unused_graph_waves_stay_lazy_but_retain_exact_data(tmp_path):
    waves = (make_wave("x", np.arange(20.)), make_wave("y", np.arange(20.)**2), make_wave("unused", np.arange(200000, dtype=np.int32)))
    source = PxpDocument("synthetic", "Synthetic", "", 0, "", "MDS", "", waves=waves,
                         graphs=(PxpGraph("Graph", traces=(PxpTrace("y", "root:y", "root:x"),)),), status="FULL")
    path = tmp_path / "lazy.mdsg"
    save_graph_document(path, EditableGraphDocument.from_pxp(source))
    restored = load_graph_document(path)
    unused = restored.source.wave("root:unused")
    assert unused is not None and unused._deferred is not None
    np.testing.assert_array_equal(unused.data, waves[2].data)
    assert unused._deferred is None and not unused.data.flags.writeable
    changed = replace(restored, current=restored.current.clone())
    assert changed.source.wave("root:unused") is unused


def test_vector_debye_integrals_match_quadrature_across_domain():
    ratios = np.geomspace(1e-5, 1e4, 150)
    actual = _debye_integrals(ratios)
    expected = np.array([_debye_integral(float(ratio)) for ratio in ratios])
    np.testing.assert_allclose(actual, expected, rtol=3e-12, atol=3e-13)
