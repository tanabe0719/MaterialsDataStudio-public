from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from materials_data_studio.core.models import PlotStyle
from materials_data_studio.plotting.igor_export import GraphExportState, GraphExportTrace
from materials_data_studio.plotting.igor_h5xp import (
    IgorExportError,
    IgorH5xpExporter,
    escape_igor_string,
    sanitize_igor_name,
    unique_igor_name,
)


def _state(x: np.ndarray, y: np.ndarray) -> GraphExportState:
    return GraphExportState(
        module="XRD",
        title='酸化物 "α" graph',
        x_label="2θ (deg)",
        y_label="Intensity (a.u.)",
        traces=[
            GraphExportTrace(
                x,
                y,
                'Observed "α" \\ sample',
                PlotStyle(
                    color="#123ABC",
                    line_style="--",
                    line_width=2.5,
                    marker="o",
                    marker_size=4.0,
                ),
                x_unit="deg",
                y_unit="a.u.",
            )
        ],
        x_range=(1.0, 90.0),
        y_range=(1.0, 1000.0),
        x_log_scale=True,
        y_log_scale=True,
        legend_enabled=True,
    )


def _wave_datasets(handle: h5py.File) -> list[h5py.Dataset]:
    waves: list[h5py.Dataset] = []

    def collect(_name: str, item: h5py.Dataset | h5py.Group) -> None:
        if isinstance(item, h5py.Dataset) and "IGORWaveType" in item.attrs:
            waves.append(item)

    handle["Packed Data/MDS"].visititems(collect)
    return waves


def test_h5xp_structure_precision_utf8_and_recreation(tmp_path: Path) -> None:
    x = np.asarray([1.0, np.nan, np.inf, -np.inf, np.pi], dtype=np.float64)
    y = np.asarray([2.0, 3.0, 4.0, 5.0, np.e], dtype=np.float64)
    destination = tmp_path / "graph.h5xp"
    result = IgorH5xpExporter().export_graph(_state(x, y), destination)

    assert result.success
    assert result.wave_count == 2
    assert result.graph_count == 1
    assert destination.exists()
    assert not (tmp_path / "graph.h5xp.tmp").exists()
    with h5py.File(destination, "r") as handle:
        assert float(handle.attrs["IGORRequiredVersion"]) == pytest.approx(9.0)
        assert handle.attrs["IGORPlatform"].decode("utf-8") == "Windows"
        assert "Packed Data/MDS/XRD" in handle
        assert "Recreation/Recreation Procedures" in handle
        waves = _wave_datasets(handle)
        assert len(waves) == 2
        np.testing.assert_array_equal(waves[0][...], x)
        np.testing.assert_array_equal(waves[1][...], y)
        assert waves[0].dtype == np.dtype("float64")
        assert waves[0].attrs["IGORWaveScaling"].shape == (2, 2)
        units = waves[0].attrs["IGORWaveUnits"]
        assert str(units[0]) == "deg"
        procedures = handle["Recreation/Recreation Procedures"].asstr()[()]
        assert procedures.startswith("MDS_XRD()\rWindow MDS_XRD() : Graph\r")
        assert "Window MDS_XRD() : Graph" in procedures
        assert "Display /W=" in procedures
        assert "AppendToGraph" not in procedures
        assert 'Label bottom "2θ (deg)"' in procedures
        assert "ModifyGraph mode(" in procedures
        assert "ModifyGraph tick=2,mirror=1,tickUnit=1" in procedures
        assert "ModifyGraph log(bottom)=1" in procedures
        assert "ModifyGraph log(left)=1" in procedures
        assert "SetAxis bottom 1,90" in procedures
        assert "SetAxis left 1,1000" in procedures
        assert "Legend/C/N=legend0/J" in procedures
        assert "酸化物" in procedures
        assert '\\"α\\"' in procedures


@pytest.mark.parametrize(
    ("dtype", "expected_type"),
    [
        (np.float32, 0x02),
        (np.float64, 0x04),
        (np.int8, 0x08),
        (np.int16, 0x10),
        (np.int32, 0x20),
        (np.int64, 0x80),
        (np.uint8, 0x48),
        (np.uint16, 0x50),
        (np.uint32, 0x60),
        (np.uint64, 0xC0),
    ],
)
def test_numeric_wave_types_are_preserved(
    tmp_path: Path, dtype: type[np.generic], expected_type: int
) -> None:
    values = np.asarray([1, 2, 3], dtype=dtype)
    destination = tmp_path / f"{np.dtype(dtype).name}.h5xp"
    IgorH5xpExporter().export_graph(_state(values, values), destination)
    with h5py.File(destination, "r") as handle:
        for wave in _wave_datasets(handle):
            assert wave.dtype == np.dtype(dtype)
            assert int(wave.attrs["IGORWaveType"]) == expected_type
            np.testing.assert_array_equal(wave[...], values)


def test_unsupported_dtype_fails_atomically(tmp_path: Path) -> None:
    destination = tmp_path / "bad.h5xp"
    values = np.asarray([True, False])
    with pytest.raises(IgorExportError, match="unsupported dtype"):
        IgorH5xpExporter().export_graph(_state(values, values), destination)
    assert not destination.exists()
    assert not (tmp_path / "bad.h5xp.tmp").exists()


def test_validation_rejects_missing_platform_attribute(tmp_path: Path) -> None:
    destination = tmp_path / "missing-platform.h5xp"
    IgorH5xpExporter().export_graph(
        _state(np.arange(3.0), np.arange(3.0)), destination
    )
    with h5py.File(destination, "r+") as handle:
        del handle.attrs["IGORPlatform"]
    with pytest.raises(IgorExportError, match="IGORPlatform is missing"):
        IgorH5xpExporter.validate(destination)


def test_naming_and_igor_string_escaping() -> None:
    assert sanitize_igor_name(" 1 sample! ") == "w_1_sample"
    assert sanitize_igor_name("日本語") == "MDSObject"
    assert len(sanitize_igor_name("a" * 100)) == 31
    used: set[str] = set()
    assert unique_igor_name("Sample", used) == "Sample"
    assert unique_igor_name("sample", used) == "sample_2"
    assert escape_igor_string('a"b\\c\r\nd') == 'a\\"b\\\\c\\r\\nd'


def test_duplicate_trace_names_create_distinct_waves(tmp_path: Path) -> None:
    state = _state(np.arange(3.0), np.arange(3.0))
    state.traces.append(state.traces[0])
    destination = tmp_path / "duplicates.h5xp"
    result = IgorH5xpExporter().export_graph(state, destination)
    assert result.wave_count == 4
    with h5py.File(destination, "r") as handle:
        names = [wave.name.rsplit("/", 1)[-1] for wave in _wave_datasets(handle)]
        assert len(names) == len(set(name.casefold() for name in names))
        procedures = handle["Recreation/Recreation Procedures"].asstr()[()]
        assert "AppendToGraph" in procedures
