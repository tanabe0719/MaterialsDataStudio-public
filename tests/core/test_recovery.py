from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from materials_data_studio.core import recovery
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import load_session


def test_recovery_snapshot_round_trip_preserves_dirty_state(tmp_path: Path) -> None:
    state = ProjectState(
        [Dataset("recover me", ModuleType.XRD, {"x": np.array([1.0, 2.0])})],
        path=tmp_path / "user.mdss",
        modified=True,
    )
    target = tmp_path / "recovery.mdss"
    recovery.save_recovery_snapshot(state, target)
    loaded = load_session(target)
    assert loaded.datasets[0].display_name == "recover me"
    assert state.path == tmp_path / "user.mdss"
    assert state.modified


def test_find_recovery_candidates_ignores_live_processes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(recovery, "recovery_directory", lambda: tmp_path)
    stale = tmp_path / "recovery-100-deadbeef.mdss"
    active = tmp_path / "recovery-200-cafebabe.mdss"
    stale.write_bytes(b"stale")
    active.write_bytes(b"active")
    monkeypatch.setattr(recovery, "_process_is_running", lambda pid: pid == 200)
    candidates = recovery.find_recovery_candidates(now=datetime.now(UTC))
    assert [candidate.path for candidate in candidates] == [stale]


def test_diagnostic_bundle_contains_metadata_and_logs_but_no_data(
    tmp_path: Path,
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "materials-data-studio.log").write_text("diagnostic line", encoding="utf-8")
    target = recovery.create_diagnostic_bundle(tmp_path / "support", log_directory=logs)
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == {
            "diagnostics.json",
            "logs/materials-data-studio.log",
        }
        metadata = json.loads(archive.read("diagnostics.json"))
        assert metadata["application"] == "Materials Data Studio"
        assert "diagnostic line" in archive.read(
            "logs/materials-data-studio.log"
        ).decode()
