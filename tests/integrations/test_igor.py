from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from materials_data_studio.integrations.igor import (
    IgorLauncher,
    IgorLaunchError,
    IgorTransferService,
)
from materials_data_studio.plotting.igor_export import GraphExportState
from materials_data_studio.plotting.igor_h5xp import IgorExportResult


class FakeExporter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.destinations: list[Path] = []

    def export_graph(self, _state: GraphExportState, destination: Path) -> IgorExportResult:
        self.destinations.append(destination)
        if self.fail:
            raise RuntimeError("export failed")
        destination.write_bytes(b"complete")
        return IgorExportResult(True, destination, [], 2, 1)


class FakeLauncher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.paths: list[Path] = []

    def launch(self, path: Path, _executable: str = "") -> None:
        assert path.read_bytes() == b"complete"
        self.paths.append(path)
        if self.fail:
            raise IgorLaunchError("launch failed", path)


def _empty_state() -> GraphExportState:
    return GraphExportState("XRD", "Graph", "x", "y", [])


def test_send_exports_before_launch_and_uses_unique_names(tmp_path: Path) -> None:
    exporter = FakeExporter()
    launcher = FakeLauncher()
    tokens = iter(
        [
            uuid.UUID("11111111-1111-1111-1111-111111111111"),
            uuid.UUID("22222222-2222-2222-2222-222222222222"),
        ]
    )
    service = IgorTransferService(
        exporter,
        launcher,  # type: ignore[arg-type]
        transfer_directory=tmp_path,
        clock=lambda: datetime(2026, 8, 31, 16, 30, 12),
        uuid_factory=lambda: next(tokens),
    )
    first = service.send(_empty_state())
    second = service.send(_empty_state())
    assert first.destination != second.destination
    assert first.destination.name == "MDS_20260831_163012_11111111.h5xp"
    assert launcher.paths == exporter.destinations


def test_export_failure_does_not_launch(tmp_path: Path) -> None:
    exporter = FakeExporter(fail=True)
    launcher = FakeLauncher()
    service = IgorTransferService(exporter, launcher, transfer_directory=tmp_path)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="export failed"):
        service.send(_empty_state())
    assert launcher.paths == []


def test_launch_failure_retains_generated_experiment(tmp_path: Path) -> None:
    launcher = FakeLauncher(fail=True)
    service = IgorTransferService(FakeExporter(), launcher, transfer_directory=tmp_path)  # type: ignore[arg-type]
    with pytest.raises(IgorLaunchError) as raised:
        service.send(_empty_state())
    assert raised.value.experiment_path.exists()


def test_cleanup_removes_only_owned_old_files(tmp_path: Path) -> None:
    old_owned = tmp_path / "MDS_20200101_000000_deadbeef.h5xp"
    recent_owned = tmp_path / "MDS_20260831_000000_feedface.h5xp"
    unrelated = tmp_path / "user.h5xp"
    for path in (old_owned, recent_owned, unrelated):
        path.write_text("x", encoding="utf-8")
    old = 1_500_000_000
    os.utime(old_owned, (old, old))
    os.utime(unrelated, (old, old))
    removed = IgorTransferService(transfer_directory=tmp_path).cleanup_old_transfers()
    assert removed == [old_owned]
    assert not old_owned.exists()
    assert recent_owned.exists()
    assert unrelated.exists()


def test_launcher_prefers_configured_executable(tmp_path: Path) -> None:
    executable = tmp_path / "Igor64.exe"
    experiment = tmp_path / "graph.h5xp"
    executable.write_bytes(b"")
    experiment.write_bytes(b"")
    calls: list[tuple[list[str], bool]] = []

    def popen(command: list[str], *, close_fds: bool):
        calls.append((command, close_fds))
        return object()

    association_calls: list[str] = []
    launcher = IgorLauncher(popen=popen, association_opener=association_calls.append)  # type: ignore[arg-type]
    launcher.launch(experiment, str(executable))
    assert calls == [([str(executable), str(experiment)], True)]
    assert association_calls == []


def test_launcher_falls_back_to_file_association(tmp_path: Path) -> None:
    experiment = tmp_path / "graph.h5xp"
    opened: list[str] = []
    IgorLauncher(association_opener=opened.append).launch(experiment)
    assert opened == [str(experiment)]
