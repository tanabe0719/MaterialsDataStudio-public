"""Display caching never hides scientific edits or holds removed datasets."""
import numpy as np
import pytest

from materials_data_studio.core.models import AxisType, Dataset, ModuleType
from materials_data_studio.modules.xrd.display_cache import DisplayDataCache


def test_cache_tracks_processing_settings_and_mutable_inputs():
    data = Dataset("Synthetic", ModuleType.XRD, {"x": np.array([20., 30., 40.]), "intensity": np.array([10., 20., 30.])})
    cache = DisplayDataCache()
    x, y = cache.get(data, AxisType.TWO_THETA)
    assert cache.get(data, AxisType.TWO_THETA)[1] is y
    data.plot_style.marker_size = 12
    assert cache.get(data, AxisType.TWO_THETA)[0] is x
    data.metadata.update(scale=2., offset=3.)
    assert cache.get(data, AxisType.TWO_THETA)[1] == pytest.approx([23, 43, 63])
    data.processed_data["background"] = np.ones(3)
    data.metadata["background_enabled"] = True
    assert cache.get(data, AxisType.TWO_THETA)[1] == pytest.approx([21, 41, 61])
    data.processed_data["background"][:] = 2
    assert cache.get(data, AxisType.TWO_THETA)[1] == pytest.approx([19, 39, 59])
    data.processed_data["intensity"] = np.array([1., 2., 3.])
    assert cache.get(data, AxisType.TWO_THETA)[1] == pytest.approx([1, 3, 5])
    data.processed_data["intensity"][:] = 4
    assert cache.get(data, AxisType.TWO_THETA)[1] == pytest.approx([7, 7, 7])
    assert data.raw_data["intensity"] == pytest.approx([10, 20, 30])
    assert not x.flags.writeable and not y.flags.writeable
    cache.retain(set())
    assert not cache.values
