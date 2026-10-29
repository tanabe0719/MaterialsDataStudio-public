"""Versioned, self-contained ZIP session persistence."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from threading import Event
from typing import Any

import numpy as np
from numpy.typing import NDArray

from materials_data_studio.config import (
    APP_NAME,
    SESSION_EXTENSION,
    SESSION_SCHEMA_VERSION,
)
from materials_data_studio.core.array_archive import validate_npz
from materials_data_studio.core.exceptions import SessionFormatError
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.tools.pxp_snapshot import decode_document, encode_document
from materials_data_studio.version import __version__

MANIFEST_NAME = "session.json"
MAX_SESSION_MEMBERS = 20_000
MAX_SESSION_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_SESSION_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_SESSION_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_SESSION_COMPRESSION_RATIO = 1_000.0


def _json_default(value: object) -> object:
    """Convert common scientific scalar values to JSON primitives."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _array_payload(dataset: Dataset) -> bytes:
    """Serialize raw and processed arrays into one compressed NPZ payload."""
    payload: dict[str, NDArray[np.generic]] = {}
    payload.update({f"raw__{name}": value for name, value in dataset.raw_data.items()})
    payload.update({f"processed__{name}": value for name, value in dataset.processed_data.items()})
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **payload)  # pyright: ignore[reportArgumentType]
    return buffer.getvalue()


def _read_arrays(
    payload: bytes,
) -> tuple[dict[str, NDArray[np.generic]], dict[str, NDArray[np.generic]]]:
    """Deserialize arrays while refusing object arrays."""
    validate_npz(
        payload,
        maximum_bytes=MAX_SESSION_TOTAL_BYTES,
        maximum_member_bytes=MAX_SESSION_MEMBER_BYTES,
    )
    raw: dict[str, NDArray[np.generic]] = {}
    processed: dict[str, NDArray[np.generic]] = {}
    with np.load(io.BytesIO(payload), allow_pickle=False) as archive:
        for name in archive.files:
            values = np.asarray(archive[name])
            if name.startswith("raw__"):
                raw[name.removeprefix("raw__")] = values
            elif name.startswith("processed__"):
                processed[name.removeprefix("processed__")] = values
    if not raw:
        raise SessionFormatError("A dataset contains no raw arrays.")
    return raw, processed


def _safe_source_name(filename: str) -> str:
    """Return only the final source path component for a ZIP entry."""
    name = Path(filename).name
    return name or "source.bin"


def save_session(
    state: ProjectState,
    path: Path,
    *,
    include_sources: bool = True,
    update_state: bool = True,
    cancel_event: Event | None = None,
) -> None:
    """Atomically save a project as a non-pickle ZIP container."""
    path = path.with_suffix(SESSION_EXTENSION) if path.suffix.lower() != SESSION_EXTENSION else path
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "application": APP_NAME,
        "application_version": __version__,
        "schema_version": SESSION_SCHEMA_VERSION,
        "active_module": state.active_module.value,
        "module_states": state.module_states,
        "axis_settings": state.axis_settings,
        "window_state": state.window_state,
        "datasets": [dataset.to_manifest() for dataset in state.datasets],
        "pxp_viewers": [
            {"source_id": source_id, "view_state": state.pxp_view_states.get(source_id, {})}
            for source_id in state.pxp_documents
        ],
    }
    _validate_manifest(manifest)
    allocation = 0
    for dataset in state.datasets:
        for array in (*dataset.raw_data.values(), *dataset.processed_data.values()):
            if array.dtype.hasobject or array.nbytes > MAX_SESSION_MEMBER_BYTES:
                raise SessionFormatError(
                    "A scientific array exceeds the safety limit or contains objects."
                )
            allocation += array.nbytes
        if (
            dataset.source_bytes is not None
            and len(dataset.source_bytes) > MAX_SESSION_MEMBER_BYTES
        ):
            raise SessionFormatError("An original source exceeds the session safety limit.")
    if allocation > MAX_SESSION_TOTAL_BYTES:
        raise SessionFormatError("Scientific arrays exceed the expanded session safety limit.")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".tmp", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        with zipfile.ZipFile(
            temporary_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(
                    manifest,
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=_json_default,
                ),
            )
            for dataset in state.datasets:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Session save stopped")
                archive.writestr(f"data/{dataset.dataset_id}.npz", _array_payload(dataset))
                if include_sources and dataset.source_bytes is not None:
                    source_name = _safe_source_name(dataset.source_filename)
                    archive.writestr(
                        f"sources/{dataset.dataset_id}/{source_name}", dataset.source_bytes
                    )
            for source_id, document in state.pxp_documents.items():
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Session save stopped")
                if not re.fullmatch(r"[0-9a-f-]{36}", source_id):
                    raise ValueError("PXP source identifier is invalid")
                metadata, arrays = encode_document(document)
                archive.writestr(
                    f"pxp/{source_id}/snapshot.json",
                    json.dumps(
                        metadata,
                        ensure_ascii=False,
                        default=_json_default,
                    ),
                )
                archive.writestr(f"pxp/{source_id}/waves.npz", arrays)
                if include_sources and document.source_bytes:
                    archive.writestr(f"pxp/{source_id}/source.pxp", document.source_bytes)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Session save stopped; original file preserved")
        # Apply exactly the reader's limits to the completed container, including
        # NPZ headers, original sources, manifest, member counts and ratios.
        # A rejected write must never replace the last good session.
        load_session(temporary_path)
        with temporary_path.open("r+b") as completed:
            os.fsync(completed.fileno())
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Session validation stopped; original file preserved")
        os.replace(temporary_path, path)
    except (OSError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        raise SessionFormatError(f"Could not save the session: {exc}") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    if update_state:
        state.path = path
        state.modified = False


def _validate_entry_names(names: set[str]) -> None:
    """Reject absolute paths and traversal entries in a session archive."""
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise SessionFormatError("The session contains an unsafe internal path.")


def _validate_archive_limits(infos: list[zipfile.ZipInfo]) -> None:
    """Reject sessions that could exhaust memory during decompression."""
    if len({info.filename for info in infos}) != len(infos):
        raise SessionFormatError("The session contains duplicate archive entries.")
    if len(infos) > MAX_SESSION_MEMBERS:
        raise SessionFormatError(f"The session contains more than {MAX_SESSION_MEMBERS:,} members.")
    total_size = 0
    for info in infos:
        if info.flag_bits & 1:
            raise SessionFormatError("Encrypted session members are not supported.")
        if info.file_size < 0 or info.compress_size < 0:
            raise SessionFormatError("The session contains an invalid member size.")
        if info.file_size > MAX_SESSION_MEMBER_BYTES:
            raise SessionFormatError(
                f"Session member '{info.filename}' exceeds the 1 GiB safety limit."
            )
        total_size += info.file_size
        if total_size > MAX_SESSION_TOTAL_BYTES:
            raise SessionFormatError("The expanded session exceeds the 4 GiB safety limit.")
        if info.file_size and info.compress_size == 0:
            raise SessionFormatError(
                f"Session member '{info.filename}' has an unsafe compression ratio."
            )
        if (
            info.compress_size
            and info.file_size / info.compress_size > MAX_SESSION_COMPRESSION_RATIO
        ):
            raise SessionFormatError(
                f"Session member '{info.filename}' has an unsafe compression ratio."
            )


def _migrate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Apply sequential migrations when future schemas are introduced."""
    version = manifest.get("schema_version")
    if type(version) is not int:
        raise SessionFormatError("The session schema version is missing or invalid.")
    if version > SESSION_SCHEMA_VERSION:
        raise SessionFormatError(
            f"Session schema {version} is newer than supported schema {SESSION_SCHEMA_VERSION}."
        )
    if version < 1:
        raise SessionFormatError(f"Session schema {version} is not supported.")
    if version == 1:
        module_states = manifest.setdefault("module_states", {})
        if isinstance(module_states, dict):
            xrd_state = module_states.setdefault("xrd", {})
            if isinstance(xrd_state, dict):
                xrd_state.setdefault("peak_fitting", {})
        manifest["schema_version"] = 2
    return manifest


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise SessionFormatError("The session manifest must be an object.")
    for key in ("module_states", "axis_settings", "window_state"):
        if not isinstance(manifest.get(key, {}), dict):
            raise SessionFormatError(f"Invalid {key} settings.")
    if any(not isinstance(value, dict) for value in manifest.get("module_states", {}).values()):
        raise SessionFormatError("Invalid analysis module settings.")
    if manifest.get("active_module", "xrd") not in {item.value for item in ModuleType}:
        raise SessionFormatError("Invalid active analysis module.")
    datasets = manifest.get("datasets")
    if not isinstance(datasets, list):
        raise SessionFormatError("The dataset manifest is invalid.")
    ids: set[str] = set()
    for dataset in datasets:
        if not isinstance(dataset, dict):
            raise SessionFormatError("Invalid dataset metadata.")
        identifier = str(dataset.get("dataset_id", ""))
        if not re.fullmatch(r"[0-9a-f-]{36}", identifier) or identifier in ids:
            raise SessionFormatError("Invalid or duplicate dataset identifier.")
        ids.add(identifier)
        if not isinstance(dataset.get("display_name"), str) or dataset.get("module_type") not in {
            item.value for item in ModuleType
        }:
            raise SessionFormatError("Invalid dataset name or module.")
        for key in ("metadata", "analysis_results", "plot_style"):
            if not isinstance(dataset.get(key, {}), dict):
                raise SessionFormatError(f"Invalid dataset {key}.")
        style = dataset.get("plot_style", {})
        for key in ("line_width", "marker_size"):
            value = style.get(key, 1.0)
            if (
                not isinstance(value, (int, float))
                or not np.isfinite(value)
                or value < 0
                or value > 1000
            ):
                raise SessionFormatError(f"Invalid plot style {key}.")
        if not isinstance(style.get("line_style", "-"), (str, int)):
            raise SessionFormatError("Invalid plot line style.")


def load_session(path: Path) -> ProjectState:
    """Validate and restore a session, reporting corrupt members clearly."""
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            infos = archive.infolist()
            _validate_archive_limits(infos)
            names = {info.filename for info in infos}
            _validate_entry_names(names)
            if MANIFEST_NAME not in names:
                raise SessionFormatError("The session manifest is missing.")
            manifest_info = archive.getinfo(MANIFEST_NAME)
            if manifest_info.file_size > MAX_SESSION_MANIFEST_BYTES:
                raise SessionFormatError("The session manifest exceeds the 16 MiB safety limit.")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise SessionFormatError(f"The session member '{bad_member}' is corrupted.")
            manifest = json.loads(archive.read(MANIFEST_NAME))
            _validate_manifest(manifest)
            manifest = _migrate_manifest(manifest)
            datasets: list[Dataset] = []
            expanded_arrays = 0
            dataset_ids: set[str] = set()
            dataset_manifests = manifest.get("datasets")
            if not isinstance(dataset_manifests, list):
                raise SessionFormatError("The dataset manifest is invalid.")
            for dataset_manifest in dataset_manifests:
                if not isinstance(dataset_manifest, dict) or "dataset_id" not in dataset_manifest:
                    raise SessionFormatError("A dataset entry is invalid.")
                dataset_id = str(dataset_manifest["dataset_id"])
                if not re.fullmatch(r"[0-9a-f-]{36}", dataset_id) or dataset_id in dataset_ids:
                    raise SessionFormatError("A dataset identifier is invalid or duplicated.")
                dataset_ids.add(dataset_id)
                data_name = f"data/{dataset_id}.npz"
                if data_name not in names:
                    raise SessionFormatError(f"Dataset '{dataset_id}' has no array payload.")
                payload = archive.read(data_name)
                expanded_arrays += validate_npz(
                    payload,
                    maximum_bytes=MAX_SESSION_TOTAL_BYTES - expanded_arrays,
                    maximum_member_bytes=MAX_SESSION_MEMBER_BYTES,
                )
                raw, processed = _read_arrays(payload)
                source_bytes = None
                source_prefix = f"sources/{dataset_id}/"
                source_candidates = sorted(
                    name
                    for name in names
                    if name.startswith(source_prefix) and not name.endswith("/")
                )
                if source_candidates:
                    source_bytes = archive.read(source_candidates[0])
                    declared_hash = dataset_manifest.get("source_hash", "")
                    if isinstance(declared_hash, str) and re.fullmatch(r"[0-9a-fA-F]{64}", declared_hash) and hashlib.sha256(source_bytes).hexdigest() != declared_hash.lower():
                        raise SessionFormatError("Embedded original source failed its SHA-256 integrity check")
                datasets.append(
                    Dataset.from_manifest(
                        dataset_manifest, raw, processed, source_bytes=source_bytes
                    )
                )
            pxp_documents = {}
            pxp_view_states = {}
            for entry in manifest.get("pxp_viewers", []):
                source_id = entry["source_id"]
                if not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", source_id):
                    raise SessionFormatError("A PXP source identifier is invalid.")
                prefix = f"pxp/{source_id}/"
                if prefix + "snapshot.json" not in names or prefix + "waves.npz" not in names:
                    raise SessionFormatError("A PXP viewer snapshot is incomplete.")
                source = (
                    archive.read(prefix + "source.pxp") if prefix + "source.pxp" in names else b""
                )
                metadata = json.loads(archive.read(prefix + "snapshot.json"))
                payload = archive.read(prefix + "waves.npz")
                expanded_arrays += validate_npz(
                    payload,
                    maximum_bytes=MAX_SESSION_TOTAL_BYTES - expanded_arrays,
                    maximum_member_bytes=MAX_SESSION_MEMBER_BYTES,
                )
                document = decode_document(metadata, payload, source)
                if document.source_id != source_id:
                    raise SessionFormatError("A PXP viewer source identifier does not match.")
                if source and hashlib.sha256(source).hexdigest() != document.sha256:
                    raise SessionFormatError(
                        "A PXP original source does not match its SHA-256 digest."
                    )
                pxp_documents[source_id] = document
                pxp_view_states[source_id] = dict(entry.get("view_state", {}))
    except SessionFormatError:
        raise
    except (
        OSError,
        zipfile.BadZipFile,
        KeyError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        raise SessionFormatError(f"Could not open the session: {exc}") from exc

    try:
        active_module = ModuleType(str(manifest.get("active_module", ModuleType.XRD.value)))
    except ValueError as exc:
        raise SessionFormatError("The active module identifier is invalid.") from exc
    return ProjectState(
        datasets=datasets,
        active_module=active_module,
        module_states=dict(manifest.get("module_states", {})),
        axis_settings=dict(manifest.get("axis_settings", {})),
        window_state=dict(manifest.get("window_state", {})),
        pxp_documents=pxp_documents,
        pxp_view_states=pxp_view_states,
        schema_version=int(manifest["schema_version"]),
        path=path,
        modified=False,
    )
