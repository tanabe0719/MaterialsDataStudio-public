"""Temporary packed-experiment transfer and non-blocking Igor Pro launch."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from materials_data_studio.tools.graph_document import EditableGraphDocument

from materials_data_studio.plotting.igor_export import GraphExportState
from materials_data_studio.plotting.igor_h5xp import IgorExportResult, IgorH5xpExporter

TRANSFER_PREFIX = "MDS_"
TRANSFER_SUFFIX = ".h5xp"
DEFAULT_RETENTION_DAYS = 7
IGOR_EXECUTABLE_SETTING = "external/igor_executable"


class _Exporter(Protocol):
    def export_graph(
        self, graph_state: GraphExportState, destination: Path
    ) -> IgorExportResult: ...


class IgorLaunchError(RuntimeError):
    """Report launch failure while retaining the successfully generated file."""

    def __init__(self, message: str, experiment_path: Path) -> None:
        super().__init__(message)
        self.experiment_path = experiment_path


class IgorLauncher:
    """Open an H5XP using a configured executable or Windows association."""

    def __init__(
        self,
        *,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        association_opener: Callable[[str], object] | None = None,
    ) -> None:
        self._popen = popen
        self._association_opener = association_opener

    def launch(self, experiment_path: Path, configured_executable: str = "") -> None:
        experiment = Path(experiment_path)
        try:
            if configured_executable.strip():
                executable = Path(configured_executable.strip())
                if not executable.exists() or not executable.is_file():
                    raise IgorLaunchError(
                        f"The configured Igor Pro executable was not found: {executable}",
                        experiment,
                    )
                self._popen([str(executable), str(experiment)], close_fds=True)
                return
            opener = self._association_opener or getattr(os, "startfile", None)
            if opener is None:
                raise IgorLaunchError(
                    "No Igor Pro executable is configured and this platform has no file association launcher.",
                    experiment,
                )
            opener(str(experiment))
        except IgorLaunchError:
            raise
        except OSError as exc:
            raise IgorLaunchError(f"Igor Pro could not be started: {exc}", experiment) from exc


class IgorTransferService:
    """Create a unique transfer experiment, validate it, then launch Igor."""

    def __init__(
        self,
        exporter: _Exporter | None = None,
        launcher: IgorLauncher | None = None,
        *,
        transfer_directory: Path | None = None,
        clock: Callable[[], datetime] = datetime.now,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self.exporter = exporter or IgorH5xpExporter()
        self.launcher = launcher or IgorLauncher()
        self.transfer_directory = transfer_directory or (
            Path(tempfile.gettempdir()) / "MaterialsDataStudio" / "IgorTransfer"
        )
        self._clock = clock
        self._uuid_factory = uuid_factory

    def send(
        self, graph_state: GraphExportState, configured_executable: str = ""
    ) -> IgorExportResult:
        destination = self._destination()
        result = self.exporter.export_graph(graph_state, destination)
        self.launcher.launch(result.destination, configured_executable)
        return result

    def send_document(
        self, document: EditableGraphDocument, configured_executable: str = ""
    ) -> IgorExportResult:
        """Transfer the editor's current document through its native H5XP writer."""
        from materials_data_studio.tools.graph_h5xp import save_h5xp

        destination = self._destination()
        save_h5xp(destination, document)
        source = document.render_source()
        result = IgorExportResult(True, destination, [], len(source.waves), len(source.graphs))
        self.launcher.launch(destination, configured_executable)
        return result

    def _destination(self) -> Path:
        self.transfer_directory.mkdir(parents=True, exist_ok=True)
        self.cleanup_old_transfers()
        timestamp = self._clock().strftime("%Y%m%d_%H%M%S")
        token = self._uuid_factory().hex[:8]
        return (
            self.transfer_directory / f"{TRANSFER_PREFIX}{timestamp}_{token}{TRANSFER_SUFFIX}"
        )

    def cleanup_old_transfers(self, retention_days: int = DEFAULT_RETENTION_DAYS) -> list[Path]:
        """Delete only old files matching this feature's exact naming policy."""
        removed: list[Path] = []
        if not self.transfer_directory.is_dir():
            return removed
        cutoff = time.time() - max(1, retention_days) * 24 * 60 * 60
        for candidate in self.transfer_directory.glob(f"{TRANSFER_PREFIX}*{TRANSFER_SUFFIX}"):
            try:
                if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
                    removed.append(candidate)
            except OSError:
                # Cleanup is best-effort and must never prevent a new transfer.
                continue
        return removed
