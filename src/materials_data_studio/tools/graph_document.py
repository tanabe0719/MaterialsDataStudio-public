"""Independent graph documents: immutable import, semantic edits, safe MDSG storage.

This module deliberately has no dependency on the MDSS session serializer or
the Igor parser. The canonical PXP codec is a data codec, not a parser.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from materials_data_studio.core.array_archive import validate_npz
from materials_data_studio.tools.pxp_model import PxpDocument, PxpGraph, PxpTable, PxpWave
from materials_data_studio.tools.pxp_snapshot import decode_document, encode_document
from materials_data_studio.version import __version__

FORMAT = "materials-data-studio-graph"
FORMAT_VERSION = 5
MAX_BYTES = 2 * 1024**3
MAX_JSON = 32 * 1024**2


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(eq=False)
class GraphState:
    """Semantic styles plus explicitly typed logical positions, never artists."""

    graphs: tuple[PxpGraph, ...]
    layout: dict[str, Any] = field(default_factory=dict)
    waves: tuple[PxpWave, ...] | None = None
    tables: tuple[PxpTable, ...] | None = None
    environment: dict[str, Any] = field(default_factory=dict)

    def clone(self) -> GraphState:
        return GraphState(self.graphs, copy.deepcopy(self.layout), self.waves, self.tables, copy.deepcopy(self.environment))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GraphState):
            return NotImplemented
        # Wave records and arrays are immutable. Identity is a revision token;
        # never scan million-point arrays on every drag or Undo checkpoint.
        same_waves = self.waves is other.waves or (
            self.waves is not None and other.waves is not None
            and len(self.waves) == len(other.waves)
            and all(a is b for a, b in zip(self.waves, other.waves, strict=True)))
        return same_waves and self.graphs == other.graphs and self.layout == other.layout and self.tables == other.tables and self.environment == other.environment


@dataclass
class EditableGraphDocument:
    source: PxpDocument
    current: GraphState
    document_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=timestamp)
    modified_at: str = field(default_factory=timestamp)

    @classmethod
    def from_pxp(cls, source: PxpDocument) -> EditableGraphDocument:
        if source.status == "OPEN_FAILED":
            raise ValueError("The experiment contains no readable data")
        return cls(source, GraphState(source.graphs))

    def render_source(self) -> PxpDocument:
        waves = self.source.waves if self.current.waves is None else self.current.waves
        folders = tuple(dict.fromkeys([*self.current.environment.get("folders", self.source.folders), *(w.igor_path.rsplit(":", 1)[0] for w in waves)]))
        tables = self.source.table_definitions if self.current.tables is None else self.current.tables
        if self.current.tables is None and not tables and self.source.tables:
            from materials_data_studio.tools.pxp_tables import parse_tables
            tables = parse_tables(self.source.recreation_raw) or tuple(PxpTable(n) for n in self.source.tables)
        return replace(self.source, waves=waves, folders=folders, graphs=self.current.graphs,
                       tables=tuple(t.name for t in tables), table_definitions=tables)

    def graph(self, name: str) -> PxpGraph:
        return next(item for item in self.current.graphs if item.name == name)

    def compatibility(self, name: str):
        """Current layered report, not the immutable parser's old status label."""
        from materials_data_studio.tools.graph_fidelity import assess_graph
        return assess_graph(self.graph(name),self.render_source())

    def fidelity(self, name: str) -> str:
        original = next((item for item in self.source.graphs if item.name == name), None)
        graph = self.graph(name)
        references = [ref for t in graph.traces for ref in (t.y_wave, t.x_wave, t.error_low, t.error_high) if ref]
        references.extend(i.wave for i in graph.images)
        references.extend(ref for a in graph.axes for ref in (a.style.user_ticks or ()))
        projection = self.render_source()
        waves_unchanged = all(projection.wave(ref) is self.source.wave(ref) for ref in references)
        return "Original" if graph == original and waves_unchanged and not self.current.layout.get(name) else "Modified"


def __getattr__(name: str) -> Any:
    # Keep the public controller import compatible without loading GUI DLLs in
    # isolated numerical workers. The data model/codec itself is Qt-free.
    if name == "GraphEditorController":
        from materials_data_studio.tools.graph_history import GraphEditorController
        return GraphEditorController
    raise AttributeError(name)


def _json(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_JSON:
        raise ValueError("Graph document metadata exceeds the safety limit")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Invalid graph document metadata")
    return value


def _check_archive(archive: zipfile.ZipFile, *, nested: bool = False) -> None:
    members = archive.infolist()
    names = [item.filename for item in members]
    if len(members) > 10000 or len(set(names)) != len(names):
        raise ValueError("Too many or duplicate archive entries")
    if sum(item.file_size for item in members) > MAX_BYTES:
        raise ValueError("Graph document exceeds the safety limit")
    for item in members:
        if item.flag_bits & 1 or ":" in item.filename or "\\" in item.filename or item.filename.startswith("/") or ".." in Path(item.filename).parts:
            raise ValueError("Unsafe graph document archive entry")
        if nested and not item.filename.endswith(".npy"):
            raise ValueError("Only NumPy arrays are allowed in the array archive")


def load_graph_document(path: Path) -> EditableGraphDocument:
    """Load canonical state without importing igor2 or reparsing original.pxp."""
    with zipfile.ZipFile(path) as archive:
        _check_archive(archive)
        for name in ("manifest.json", "document.json"):
            if archive.getinfo(name).file_size > MAX_JSON:
                raise ValueError("Graph document metadata exceeds the safety limit")
        manifest = _json(archive.read("manifest.json"))
        if manifest.get("format") != FORMAT or manifest.get("format_version") not in {1, 2, 3, 4, FORMAT_VERSION}:
            raise ValueError("Unsupported MDS Graph Document format/version")
        metadata = _json(archive.read("document.json"))
        payload = archive.read("arrays/data.npz")
        expanded = validate_npz(payload, maximum_bytes=MAX_BYTES)
        source_bytes = archive.read("source/original.pxp") if "source/original.pxp" in archive.namelist() else b""
        imported = metadata["imported"]
        if source_bytes and (hashlib.sha256(source_bytes).hexdigest() != imported["sha256"] or len(source_bytes) != imported["size"]):
            raise ValueError("Embedded original PXP failed its integrity check")
        source = decode_document(imported, payload, source_bytes)
        # Both states reference the exact same read-only scientific arrays.
        current_metadata = {**imported, "waves": [], "graphs": metadata["current"]["graphs"]}
        current = decode_document(current_metadata, payload)
        working_waves = None
        if "working" in metadata["current"]:
            working_payload = archive.read("arrays/working.npz")
            validate_npz(working_payload, maximum_bytes=MAX_BYTES - expanded)
            changed = decode_document(metadata["current"]["working"], working_payload).waves
            # Unchanged arrays are stored only once, and retain source identity.
            catalog = {w.id: w for w in (*source.waves, *changed)}
            working_waves = tuple(catalog[key] for key in metadata["current"]["wave_order"])
        layout = metadata["current"].get("layout", {})
        if not isinstance(layout, dict):
            raise ValueError("Invalid graph layout")
        tables = metadata["current"].get("tables")
        parsed_tables = None if tables is None else tuple(
            PxpTable(t["name"], tuple(t["columns"]), tuple(tuple(p) for p in t.get("widths", ()))) for t in tables)
        environment = metadata["current"].get("environment", {})
        if not isinstance(environment, dict):
            raise ValueError("Invalid command/procedure environment")
        from materials_data_studio.tools.graph_geometry import migrate_legacy_labels
        graphs = migrate_legacy_labels(current.graphs, layout)
        return EditableGraphDocument(source, GraphState(graphs, layout, working_waves, parsed_tables, environment),
                                     str(uuid.UUID(manifest["document_id"])),
                                     manifest["created_at"], manifest["modified_at"])


def save_graph_document(path: Path, document: EditableGraphDocument) -> None:
    """Write, validate and atomically replace an MDSG; never overwrite a PXP/MDSS."""
    if path.suffix.lower() != ".mdsg":
        raise ValueError("Graph documents must use the .mdsg extension")
    metadata, arrays = encode_document(document.source)
    current, _ = encode_document(replace(document.source, waves=(), graphs=document.current.graphs))
    modified = timestamp()
    manifest = {"format": FORMAT, "format_version": FORMAT_VERSION,
                "application": "Materials Data Studio", "application_version": __version__,
                "source_type": "igor_pxp" if document.source.backend != "MDS" else "mds", "document_id": document.document_id,
                "created_at": document.created_at, "modified_at": modified}
    # Only a basename is portable provenance. Imported recreation is preserved
    # verbatim (and may itself contain Igor-authored source paths).
    metadata["basename"] = metadata["basename"].replace("\\", "/").rsplit("/", 1)[-1]
    payload = {"imported": metadata, "current": {"graphs": current["graphs"],
                                                  "layout": document.current.layout, "environment": document.current.environment}}
    if document.current.tables is not None:
        from dataclasses import asdict
        payload["current"]["tables"] = [asdict(t) for t in document.current.tables]
    working_arrays = None
    if document.current.waves is not None:
        original = {w.id: w for w in document.source.waves}
        changes = tuple(w for w in document.current.waves if original.get(w.id) is not w)
        working, working_arrays = encode_document(replace(document.source, waves=changes, graphs=()))
        payload["current"]["working"] = working
        payload["current"]["wave_order"] = [w.id for w in document.current.waves]
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, value in (("manifest.json", manifest), ("document.json", payload)):
                    archive.writestr(name, json.dumps(value, ensure_ascii=False, allow_nan=False))
                archive.writestr("arrays/data.npz", arrays)
                if working_arrays is not None:
                    archive.writestr("arrays/working.npz", working_arrays)
                if document.source.source_bytes:
                    archive.writestr("source/original.pxp", document.source.source_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        load_graph_document(temporary)
        os.replace(temporary, path)
        document.modified_at = modified
    finally:
        temporary.unlink(missing_ok=True)
