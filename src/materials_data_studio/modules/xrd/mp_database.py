"""Portable Materials Project reference indexes; no network or builder dependencies."""
from __future__ import annotations

import hashlib
import json
import zlib
from dataclasses import replace
from pathlib import Path
from threading import Event

import numpy as np

from materials_data_studio.modules.xrd.mp_codec import decode_pattern
from materials_data_studio.modules.xrd.mp_metadata import metadata_index
from materials_data_studio.modules.xrd.phase_models import (
    CalculationSettings,
    Candidate,
    SearchQuery,
    elements,
)
from materials_data_studio.modules.xrd.phase_store import PhaseStore
from materials_data_studio.modules.xrd.reference_index import check_stop, readonly, retrieve

FORMAT = "mds-mp-reference-1"
NOTICES = """Materials Project — DFT-relaxed structures (not experimental confirmation).
Data: CC BY 4.0 for records explicitly marked BY-C; GNoME/BY-NC and unknown licenses excluded.
Source: https://registry.opendata.aws/materials-project/
Terms: https://materialsproject.org/about/terms
Cite Jain et al. (2013), https://doi.org/10.1063/1.4812323
MDS selects BY-C records and derives a lossless local diffraction index; it adds no restriction to the original data license.
License: https://creativecommons.org/licenses/by/4.0/
Intensities use pymatgen's standard XRD model; wavelength-dependent anomalous scattering is not recalculated.
"""


def file_hash(path: Path, stop: Event) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            check_stop(stop)
            result.update(chunk)
    return result.hexdigest()


def decode_json(value, *, limit=64 * 1024**2):
    """Read losslessly compressed patterns as well as original plain indexes."""
    if isinstance(value, bytes):
        inflater = zlib.decompressobj()
        value = inflater.decompress(value, limit)
        if not inflater.eof:
            raise ValueError("MP reference is corrupt or exceeds its decoded size limit")
    if len(value) > limit:
        raise ValueError("MP reference exceeds its decoded size limit")
    return json.loads(value)


class MPDatabaseManager:
    def __init__(self, store: PhaseStore):
        self.store = store
        self.config = store.root / "materials-project.json"

    def configuration(self) -> dict:
        if not self.config.is_file():
            return {}
        return self._cache_location(json.loads(self.config.read_text(encoding="utf-8")))

    def _cache_location(self, info: dict) -> dict:
        # Old installed indexes gain a disposable local index without rewriting the database.
        info["metadata_path"] = str(self.store.root / "reference_indexes" / ("mp-" + info["content_hash"] + ".sqlite"))
        return info

    def register(self, path: Path, stop: Event | None = None, report=lambda message: None) -> dict:
        stop = stop or Event()
        path = path.resolve()
        report("Verifying Materials Project index")
        if not path.is_file() or path.stat().st_size > 16 * 1024**3:
            raise ValueError("Select an existing MP SQLite index smaller than 16 GiB")
        with readonly(path, stop) as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("MP index integrity check failed")
            row = connection.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()
            if row is None:
                raise ValueError("MP index manifest is missing")
            manifest = json.loads(row[0])
            if manifest.get("format") not in {FORMAT, "mds-mp-reference-2"} or manifest.get("license") != "CC BY 4.0 (BY-C records only)" or manifest.get("building"):
                raise ValueError("Unsupported MP index format or license class")
            count = connection.execute("SELECT count(*) FROM cards").fetchone()[0]
            phases = connection.execute("SELECT count(*) FROM phases").fetchone()[0]
            if not count or count != phases or count != manifest.get("included"):
                raise ValueError("MP index has incomplete record counts; finish/validate its build first")
            for index, (candidate, pattern) in enumerate(connection.execute("SELECT candidate,pattern FROM cards"), 1):
                check_stop(stop)
                if index == 1 or index % 1000 == 0 or index == count:
                    report(f"Checking MP references {index:,} / {count:,}")
                card, peaks = Candidate(**json.loads(candidate)), decode_pattern(pattern, include_hkl=False, validate_hkl=True)
                if card.source != "Materials Project" or not card.identity.startswith("mp-"):
                    raise ValueError("Invalid MP record identity")
                ds, intensities = np.array(peaks["d"]), np.array(peaks["intensities"])
                if not 0 < len(ds) <= 20000 or ds.shape != intensities.shape or not np.all(np.isfinite(ds)) or not np.all(ds > 0) or not np.all(np.isfinite(intensities)) or not np.all(intensities >= 0):
                    raise ValueError("Invalid MP reference peaks")
        checksum = file_hash(path, stop)
        sidecar = path.with_suffix(path.suffix + ".sha256")
        if sidecar.is_file() and sidecar.read_text(encoding="ascii").strip() != checksum:
            raise ValueError("MP index SHA-256 differs from its build checksum")
        info = self._cache_location({**manifest, "path": str(path), "content_hash": checksum, "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns, "phase_count": count, "source": "Materials Project"})
        check_stop(stop)
        temporary = self.config.with_suffix(".tmp")
        temporary.write_text(json.dumps(info, indent=2), encoding="utf-8")
        temporary.replace(self.config)
        return info

    def verify(self, stop: Event | None = None, report=lambda message: None) -> dict:
        info = self.configuration()
        if not info:
            raise ValueError("Materials Project index is not installed")
        return self.register(Path(info["path"]), stop, report)

    def import_archive(self, path: Path, stop: Event | None = None, report=lambda message: None) -> dict:
        from materials_data_studio.modules.xrd.mp_archive import install_archive
        return install_archive(self, path, stop or Event(), report)

    def remove(self) -> None:
        self.config.unlink(missing_ok=True)


class MaterialsProjectBackend:
    def __init__(self, info: dict, stop: Event):
        path = Path(info["path"])
        if path.stat().st_size != info["size"] or path.stat().st_mtime_ns != info["mtime_ns"]:
            raise ValueError("MP index changed; verify it in Reference Databases")
        self.info, self.stop = info, stop
        self.connection = readonly(path, stop)

    def close(self) -> None:
        self.connection.close()

    def get_database_info(self) -> dict:
        return self.info

    def _field(self, identity: str, field: str):
        row = self.connection.execute(f"SELECT {field} FROM cards WHERE phase_id=?", (identity,)).fetchone()
        if row is None:
            raise ValueError("MP reference not found")
        return row[0]

    def get_metadata(self, phase_id: str) -> Candidate:
        return Candidate(**json.loads(self._field(phase_id, "candidate")))

    def get_reference_peaks(self, phase_id: str) -> dict:
        return decode_pattern(self._field(phase_id, "pattern"))

    def get_search_peaks(self, phase_id: str) -> dict:
        return decode_pattern(self._field(phase_id, "pattern"), include_hkl=False)

    def details(self, phase_id: str) -> dict:
        return decode_json(self._field(phase_id, "details"))

    def cif(self, phase_id: str) -> str:
        # Coordinates remain in the separately installed database, never all in a session.
        from pymatgen.core import Structure
        data = self._field(phase_id, "structure")
        return str(Structure.from_dict(decode_json(data, limit=16 * 1024**2)).to(fmt="cif"))

    def retrieve_candidates(self, peaks: list[dict], constraints: SearchQuery, settings: CalculationSettings,
                            stop: Event, limit: int, *, scale_limit: float = 0) -> list[Candidate]:
        # Same inverted index and votes as COD, with a bounded d-range expansion.
        ranked = retrieve(self.connection, peaks, settings, stop, scale_limit=scale_limit)
        remaining = constraints
        formula = None
        formula_elements = set()
        if constraints.formula:
            from pymatgen.core import Composition
            try:
                formula = Composition(constraints.formula).reduced_composition
                formula_elements = {str(element) for element in formula.elements}
            except (ValueError, TypeError):
                return []
            remaining = replace(constraints, formula="")
        clauses, params = [], []
        if formula is not None:
            clauses.append("element_key IN (?, '')")
            params.append(",".join(sorted(formula_elements)))
        for symbol in elements(constraints.required):
            clauses.append("instr(symbols,?)>0")
            params.append("|" + symbol + "|")
        for symbol in elements(constraints.excluded):
            clauses.append("instr(symbols,?)=0")
            params.append("|" + symbol + "|")
        if allowed := elements(constraints.allowed):
            expression = "symbols"
            for symbol in allowed:
                expression = f"replace({expression},?,'|')"
                params.append("|" + symbol + "|")
            clauses.append(expression + "='|' AND element_key<>''")
        if constraints.space_group:
            clauses.append("(space_group=? OR space_group_number=?)")
            params.extend([constraints.space_group.replace(" ", "").lower()] * 2)
        if constraints.crystal_system:
            clauses.append("crystal_system=?")
            params.append(constraints.crystal_system.lower())
        if constraints.volume_min:
            clauses.append("volume>=?")
            params.append(constraints.volume_min)
        if constraints.volume_max:
            clauses.append("volume>0 AND volume<=?")
            params.append(constraints.volume_max)
        result = []
        # Keep GLOBAL vote/rarity weights and exact candidate order. SQL filters
        # only avoid irrelevant JSON reads; the final scientific predicate remains.
        with metadata_index(self.connection, self.info, stop) as metadata:
            for offset in range(0, len(ranked), 512):
                check_stop(stop)
                identities = [key for key, _ in ranked[offset:offset + 512]]
                where = " AND ".join(["id IN (" + ",".join("?" for _ in identities) + ")", *clauses])
                rows = dict(metadata.execute("SELECT id,candidate FROM meta WHERE " + where, [*identities, *params]))
                for identity in identities:
                    if identity not in rows:
                        continue
                    candidate = Candidate(**json.loads(rows[identity]))
                    if formula is not None:
                        from pymatgen.core import Composition
                        try:
                            if Composition(candidate.formula).reduced_composition != formula:
                                continue
                        except (ValueError, TypeError):
                            continue
                    if remaining.accepts(candidate):
                        result.append(candidate)
                        if len(result) == limit:
                            return result
        return result
