"""Crash recovery snapshots and privacy-conscious diagnostic bundles."""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from materials_data_studio.core.logging_config import user_data_directory
from materials_data_studio.core.processes import process_is_running, process_started_at
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import save_session
from materials_data_studio.version import __version__

_RECOVERY_NAME = re.compile(r"^recovery-(\d+)-([0-9a-f]+)\.mdss$")


@dataclass(frozen=True, slots=True)
class RecoveryCandidate:
    """One recovery snapshot left by a process that is no longer running."""

    path: Path
    modified_at: datetime
    process_id: int


def recovery_directory() -> Path:
    """Return the private application recovery directory."""
    configured = os.environ.get("MDS_RECOVERY_DIRECTORY", "").strip()
    if configured:
        return Path(configured)
    return user_data_directory() / "recovery"


def create_recovery_path(*, process_id: int | None = None, token: str | None = None) -> Path:
    """Create a unique path without writing a snapshot yet."""
    from uuid import uuid4

    pid = os.getpid() if process_id is None else int(process_id)
    identifier = uuid4().hex if token is None else token
    directory = recovery_directory()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"recovery-{pid}-{identifier}.mdss"


def save_recovery_snapshot(state: ProjectState, path: Path) -> None:
    """Persist a recovery copy without changing the user's session path or dirty state."""
    save_session(state, path, include_sources=True, update_state=False)


def discard_recovery(path: Path | None) -> None:
    """Remove one application-owned recovery snapshot if present."""
    if path is not None and path.parent == recovery_directory():
        path.unlink(missing_ok=True)


def _process_is_running(process_id: int) -> bool:
    return process_is_running(process_id)


def find_recovery_candidates(*, now: datetime | None = None) -> list[RecoveryCandidate]:
    """Return newest-first snapshots whose owning process is no longer active."""
    directory = recovery_directory()
    if not directory.is_dir():
        return []
    # Never expire recovery data implicitly; failed and independent workspaces
    # remain available until explicitly recovered/discarded by their owner.
    candidates: list[RecoveryCandidate] = []
    for path in directory.glob("recovery-*.mdss"):
        match = _RECOVERY_NAME.fullmatch(path.name)
        if match is None:
            continue
        process_id = int(match.group(1))
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        except OSError:
            continue
        if process_id == os.getpid():
            continue
        running = _process_is_running(process_id)
        started = process_started_at(process_id) if running else None
        # A newer process with a reused PID does not own this snapshot.
        running = running and (started is None or started <= modified.timestamp())
        if not running:
            candidates.append(RecoveryCandidate(path, modified, process_id))
    return sorted(candidates, key=lambda item: item.modified_at, reverse=True)


def diagnostic_logs(*, redact_paths: bool = True, log_directory: Path | None = None) -> dict[str, bytes]:
    """Capture bounded logs once so the saved contents are the reviewed contents."""
    logs = user_data_directory() / "logs" if log_directory is None else log_directory
    result = {}
    for path in sorted(logs.glob("materials-data-studio.log*"))[:10] if logs.is_dir() else []:
        if path.is_file():
            with path.open("rb") as stream:
                payload = stream.read(5 * 1024**2)
            if redact_paths:
                text = payload.decode("utf-8", errors="replace")
                text = re.sub(r"(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n\"']+", "<local path>", text)
                payload = text.encode("utf-8")
            result[path.name] = payload
    return result


def create_diagnostic_bundle(target: Path, *, log_directory: Path | None = None, reviewed_logs: dict[str, bytes] | None = None) -> Path:
    """Write platform metadata and existing logs, never sessions or measurement data."""
    target = target.with_suffix(".zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    logs = user_data_directory() / "logs" if log_directory is None else log_directory
    metadata = {
        "application": "Materials Data Studio",
        "version": __version__,
        "created_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "executable_is_frozen": bool(getattr(sys, "frozen", False)),
    }
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        with zipfile.ZipFile(
            temporary, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr(
                "diagnostics.json",
                json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
            )
            if reviewed_logs is not None:
                for name, payload in reviewed_logs.items():
                    if Path(name).name != name or not name.startswith("materials-data-studio.log"):
                        raise ValueError("Invalid diagnostic log name")
                    archive.writestr(f"logs/{name}", payload)
            elif logs.is_dir():
                for log_path in sorted(logs.glob("materials-data-studio.log*")):
                    if log_path.is_file():
                        archive.write(log_path, f"logs/{log_path.name}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
