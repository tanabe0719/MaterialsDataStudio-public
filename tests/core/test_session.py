from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from materials_data_studio.core.exceptions import SessionFormatError
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import (
    MAX_SESSION_COMPRESSION_RATIO,
    MAX_SESSION_MEMBER_BYTES,
    _validate_archive_limits,
    load_session,
    save_session,
)


def test_session_round_trip_includes_arrays_and_source(tmp_path: Path) -> None:
    dataset = Dataset(
        "α sample",
        ModuleType.XRD,
        {"x": np.array([1.0, 2.0]), "intensity": np.array([3.0, 4.0])},
        processed_data={"background": np.array([0.1, 0.2])},
        source_filename="C:/café data/sample.xy",
        source_hash="abc",
        source_bytes=b"1 3\n2 4\n",
        metadata={"wavelength_a": 1.540598},
    )
    state = ProjectState([dataset], active_module=ModuleType.XRD)
    target = tmp_path / "roundtrip.mdss"
    save_session(state, target)
    loaded = load_session(target)
    assert loaded.path == target
    assert loaded.datasets[0].display_name == "α sample"
    np.testing.assert_allclose(loaded.datasets[0].raw_data["intensity"], [3, 4])
    np.testing.assert_allclose(loaded.datasets[0].processed_data["background"], [0.1, 0.2])
    assert loaded.datasets[0].source_bytes == b"1 3\n2 4\n"
    assert not loaded.modified


def test_save_adds_extension(tmp_path: Path) -> None:
    state = ProjectState(
        [Dataset("a", ModuleType.XRD, {"x": np.array([1.0])})]
    )
    save_session(state, tmp_path / "plain")
    assert (tmp_path / "plain.mdss").is_file()


def test_recovery_save_does_not_change_user_session_state(tmp_path: Path) -> None:
    original = tmp_path / "original.mdss"
    state = ProjectState(
        [Dataset("a", ModuleType.XRD, {"x": np.array([1.0])})],
        path=original,
        modified=True,
    )
    recovery = tmp_path / "recovery.mdss"
    save_session(state, recovery, update_state=False)
    assert recovery.is_file()
    assert state.path == original
    assert state.modified


def test_session_archive_limits_reject_oversized_and_extreme_ratio_members() -> None:
    oversized = zipfile.ZipInfo("data/large.npz")
    oversized.file_size = MAX_SESSION_MEMBER_BYTES + 1
    oversized.compress_size = oversized.file_size
    with pytest.raises(SessionFormatError, match="1 GiB"):
        _validate_archive_limits([oversized])

    compressed = zipfile.ZipInfo("data/bomb.npz")
    compressed.compress_size = 1
    compressed.file_size = int(MAX_SESSION_COMPRESSION_RATIO) + 1
    with pytest.raises(SessionFormatError, match="compression ratio"):
        _validate_archive_limits([compressed])


def test_legacy_extension_can_still_be_loaded(tmp_path: Path) -> None:
    state = ProjectState(
        [Dataset("a", ModuleType.XRD, {"x": np.array([1.0])})]
    )
    current = tmp_path / "current.mdss"
    legacy = tmp_path / "legacy.mdsession"
    save_session(state, current)
    current.rename(legacy)
    loaded = load_session(legacy)
    assert loaded.datasets[0].display_name == "a"
    assert loaded.path == legacy


def test_missing_manifest_is_reported(tmp_path: Path) -> None:
    target = tmp_path / "bad.mdss"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("data/a.npz", b"bad")
    with pytest.raises(SessionFormatError, match="manifest"):
        load_session(target)


def test_newer_schema_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "future.mdss"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(
            "session.json",
            json.dumps({"schema_version": 999, "datasets": [], "active_module": "xrd"}),
        )
    with pytest.raises(SessionFormatError, match="newer"):
        load_session(target)


def test_missing_dataset_payload_is_reported(tmp_path: Path) -> None:
    target = tmp_path / "missing.mdss"
    manifest = {
        "schema_version": 1,
        "active_module": "xrd",
        "datasets": [{"dataset_id": "00000000-0000-0000-0000-000000000001", "display_name": "missing", "module_type": "xrd"}],
    }
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("session.json", json.dumps(manifest))
    with pytest.raises(SessionFormatError, match="array payload"):
        load_session(target)


def test_non_zip_is_reported(tmp_path: Path) -> None:
    target = tmp_path / "bad.mdss"
    target.write_text("not a zip")
    with pytest.raises(SessionFormatError, match="Could not open"):
        load_session(target)


def test_schema_one_migrates_with_empty_peak_fitting_state(tmp_path: Path) -> None:
    target = tmp_path / "version-one.mdss"
    state = ProjectState(
        [Dataset("a", ModuleType.XRD, {"x": np.array([1.0])})]
    )
    save_session(state, target)
    with zipfile.ZipFile(target, "r") as source:
        entries = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(entries["session.json"])
    manifest["schema_version"] = 1
    manifest["module_states"] = {}
    with zipfile.ZipFile(target, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(
                name,
                json.dumps(manifest) if name == "session.json" else payload,
            )
    loaded = load_session(target)
    assert loaded.schema_version == 2
    assert loaded.module_states["xrd"]["peak_fitting"] == {}
