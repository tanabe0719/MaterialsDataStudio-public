"""Scientific transfer and shared lazy-storage regressions with synthetic data."""
import copy
import gc
import io
import json
from types import SimpleNamespace

import numpy as np
import pytest

from materials_data_studio.core.array_revision import ArrayRevisionCache
from materials_data_studio.core.immutable_json import HistoryLog, prepare_revision
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.wave_store import WaveStore
from materials_data_studio.plotting.igor_export import (
    GraphExportState,
    add_checked_dataset_waves,
    snapshot_transfer_context,
)


def test_incomplete_transport_geometry_keeps_raw_waves():
    from materials_data_studio.modules.ppms.resistivity_widget import ResistivityWidget
    settings = {"source": "resistance", "kind": "resistance", "unit": "ohm", "length_cm": 0., "width_cm": 0., "thickness_cm": 0.}
    dataset = Dataset("Synthetic transport", ModuleType.PPMS,
        {"temperature_k": np.array([1., 2., 3.]), "bridge_1_resistance": np.array([10., 20., 30.])},
        metadata={"bridge_settings": {"1": settings}})
    workspace = SimpleNamespace(_bridge_values=True, _bridge_setting=lambda d, i: ResistivityWidget._bridge_setting(SimpleNamespace(_settings=lambda d: d.metadata["bridge_settings"]), d, i))
    datasets, context = snapshot_transfer_context(workspace, [dataset])
    state = GraphExportState("Resistivity", "Synthetic", "T", "R", [])
    add_checked_dataset_waves(state, datasets, context)
    assert {w.name for w in state.waves} == {"Raw_temperature_k", "Raw_bridge_1_resistance"}
    settings.update(length_cm=1., width_cm=1., thickness_cm=1.)
    assert context._bridge_setting(datasets[0], 1)["length_cm"] == 0.


def test_lazy_store_concurrent_read_and_streamed_save(tmp_path):
    import zipfile
    from concurrent.futures import ThreadPoolExecutor
    buffer = io.BytesIO()
    np.savez_compressed(buffer, a=np.arange(20.), b=np.ones(100))
    store = WaveStore(buffer.getvalue())
    with ThreadPoolExecutor(2) as pool:
        arrays = list(pool.map(store.load, ["a", "b", "a", "b"]))
    np.testing.assert_array_equal(arrays[0], np.arange(20.))
    assert not arrays[0].flags.writeable
    output = tmp_path / "copy.npz"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        store.copy_member("a", target, "renamed.npy")
    with np.load(output, allow_pickle=False) as result:
        np.testing.assert_array_equal(result["renamed"], arrays[0])


def test_persistent_history_is_json_compatible_and_shares_prefix():
    first = HistoryLog([], {"title": "first"})
    history = first
    for i in range(2000):
        history = HistoryLog(history, {"title": str(i)})
    assert len(history) == 2001 and history[-1]["title"] == "1999"
    assert copy.deepcopy(history) is history
    assert json.loads(json.dumps(history))[0] == {"title": "first"}
    assert first == [{"title": "first"}]
    with pytest.raises(TypeError):
        history.append({})


def test_dead_input_releases_cached_result():
    array = np.arange(100.)
    array.setflags(write=False)
    cache = ArrayRevisionCache()
    cache.get_or_create((array,), "sum", lambda values=array: float(values.sum()))
    assert cache.entries
    del array
    gc.collect()
    assert not cache.entries and cache.retained_bytes == 0


def test_prepared_revision_has_worker_ready_bookkeeping():
    value = prepare_revision({"results": list(range(10000))})
    assert value._retained_size > 0 and len(value.content_digest) == 64


def test_store_backing_is_written_in_bounded_chunks(monkeypatch):
    from materials_data_studio.core import wave_store
    factory = wave_store.tempfile.SpooledTemporaryFile
    writes = []
    class ObservedStream:
        def __init__(self, *args, **kwargs):
            self.stream = factory(*args, **kwargs)
        def write(self, data):
            writes.append(len(data))
            return self.stream.write(data)
        def __getattr__(self, name):
            return getattr(self.stream, name)
    buffer = io.BytesIO()
    np.savez(buffer, values=np.arange(200000.))
    monkeypatch.setattr(wave_store.tempfile, "SpooledTemporaryFile", ObservedStream)
    store = WaveStore(buffer.getvalue())
    np.testing.assert_array_equal(store.load("values"), np.arange(200000.))
    assert len(writes) > 1 and max(writes) <= 1024**2


def test_large_graph_verification_never_claims_pending_script(qtbot, monkeypatch):
    from dataclasses import replace
    from threading import Event

    from materials_data_studio.tools import command_recording
    from materials_data_studio.tools.graph_document import EditableGraphDocument
    from materials_data_studio.tools.graph_history import GraphEditorController
    from materials_data_studio.tools.graph_waves import empty_document, make_wave
    from materials_data_studio.tools.pxp_model import PxpGraph, PxpTrace
    gate = Event()
    def verify(*_args):
        assert gate.wait(10)
        return "ModifyGraph lsize(y)=2"
    monkeypatch.setattr(command_recording, "verified_replay", verify)
    source = empty_document().source
    graph = PxpGraph("G", traces=(PxpTrace("y", "root:y"),))
    source = replace(source, waves=(make_wave("y", np.zeros(1200000)),), graphs=(graph,))
    editor = GraphEditorController(EditableGraphDocument.from_pxp(source))
    try:
        editor.edit_graph(replace(graph, traces=(replace(graph.traces[0], line_width=2.),)), "Synthetic width")
        record = editor.document.current.environment["operation_history"][-1]
        assert not record["replayable"] and "script" not in record
        editor.history.undo()
        gate.set()
        qtbot.waitUntil(lambda: not editor._pending_verifications)
        editor.history.redo()
        record = editor.document.current.environment["operation_history"][-1]
        assert record["replayable"] and record["script"] == "ModifyGraph lsize(y)=2"
        for i in range(6):
            current = editor.document.current.graphs[0]
            editor.edit_graph(replace(current, traces=(replace(current.traces[0], line_width=3. + i),)), "Synthetic backlog")
        editor.finish_verifications()
        assert all(record["replayable"] for record in editor.document.current.environment["operation_history"])
    finally:
        gate.set()
        editor.finish_verifications()


def test_legend_and_reflection_text_allow_line_style_reuse():
    from dataclasses import replace

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from materials_data_studio.core.models import PlotStyle
    from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
    from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer
    from materials_data_studio.tools.graph_workflows import from_plot_spec
    document = from_plot_spec(PlotSpec("x", "y", [CurveSpec(np.arange(3.), np.arange(3.), "Synthetic", PlotStyle(), point_labels=("001", "002", "003"))]))
    graph = document.current.graphs[0]
    renderer = IgorMatplotlibRenderer()
    figure = Figure()
    FigureCanvasAgg(figure)
    renderer.render(figure, document, graph.name)
    figure.canvas.draw()
    previous = (graph, document.current.layout[graph.name], document.render_source().waves)
    artists = tuple(figure.axes[0].lines)
    changed = replace(graph, traces=(replace(graph.traces[0], line_width=3.),))
    assert renderer.update_trace_styles(figure, previous, changed, previous[1], previous[2])
    figure.canvas.draw()
    assert tuple(figure.axes[0].lines) == artists
