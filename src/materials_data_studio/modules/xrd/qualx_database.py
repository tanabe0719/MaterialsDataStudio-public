"""Independent, read-only access to the published QualX COD SQLite format."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from threading import Event

import numpy as np

from materials_data_studio.modules.xrd.phase_models import (
    CalculationSettings,
    Candidate,
    SearchQuery,
    elements,
)
from materials_data_studio.modules.xrd.phase_store import PhaseStore, timestamp
from materials_data_studio.modules.xrd.reference_index import (
    ENGINE_VERSION,
    DatabaseConnection,
    check_stop,
    create_index,
    numbers,
    readonly,
    retrieve,
)

RECORD_URL = "https://zenodo.org/records/23297507"
ARCHIVE_MD5 = "1dfabaa994de364344628043f6dfe811"
OFFICIAL_CONTENT_HASH = "331673c8c2ec06208bc988f8538b5f722da4da4ca254dc0ec762b7f67eb186ca"
DATABASE_NOTICES = """QualX COD Inorganic reference data
Official source: https://zenodo.org/records/23297507
DOI: 10.5281/zenodo.23297507; version 2026-07; COD snapshot 2026-07-02.
Processed database: CC BY 4.0 (Zenodo record metadata).
Original COD data: CC0. MDS does not redistribute the database in its EXE or Git repository.
QualX software: README says LGPL-3.0, while the repository LICENSE contains GPL-3.0.
MDS uses an independent Python reader; no QualX software code is copied or linked.
MDS Match Score is not the QualX FOM or a phase-presence probability.
Cite: Altomare et al. (2015), https://doi.org/10.1107/S1600576715002319
COD: Gražulis et al. (2009), https://doi.org/10.1107/S0021889809016690
COD validation: Vaitkus et al. (2021), https://doi.org/10.1107/S1600576721000418
For other imported databases, consult their original distribution terms.
"""
SUFFIXES = ("", ".info", ".infostat", ".search")


def find_database(path: Path) -> Path:
    if path.is_file() and path.suffix.lower() == ".sq":
        return path.resolve()
    if not path.is_dir():
        raise ValueError("Choose a QualX .sq file or its database folder")
    files = list(path.glob("*.sq")) + list(path.glob("*/*.sq"))
    if len(files) != 1:
        raise ValueError("Choose the folder containing exactly one QualX .sq database")
    return files[0].resolve()


def validate_database(path: Path, stop: Event, report=lambda message: None) -> dict:
    main = find_database(path)
    sizes, hashes, mtimes = {}, {}, {}
    required = {"": {"id": {"id", "chemical_formula", "spacegroup", "nd", "dvalue", "intensita"},
                         "chemical": {"id", "chemical_element"}, "infodb": {"date", "ncard", "type"}},
                ".info": {"info": {"id", "a", "b", "c", "alpha", "beta", "gamma", "volume", "type", "h", "k", "l"}},
                ".infostat": {"stat_type": {"val", "ids", "n"}}, ".search": {"top": {"id", "n", "dval"}}}
    total = 0
    for suffix in SUFFIXES:
        check_stop(stop)
        file = Path(str(main) + suffix)
        if not file.is_file() or not 100 <= file.stat().st_size <= 2 * 1024**3:
            raise ValueError(f"Missing/invalid QualX companion: {file.name}")
        sizes[suffix] = file.stat().st_size
        mtimes[suffix] = file.stat().st_mtime_ns
        total += sizes[suffix]
        if total > 3 * 1024**3:
            raise ValueError("QualX database exceeds the 3 GiB installation limit")
        report("Verifying " + file.name)
        with readonly(file, stop) as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Damaged SQLite database: " + file.name)
            for table, columns in required[suffix].items():
                present = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                if not columns <= present:
                    raise ValueError(f"Unsupported QualX schema: {file.name}/{table}")
        hashed = hashlib.sha256()
        with file.open("rb") as source:
            while block := source.read(1024 * 1024):
                check_stop(stop)
                hashed.update(block)
        hashes[suffix] = hashed.hexdigest()
    with readonly(main, stop) as connection:
        row = connection.execute("SELECT date,ncard,type FROM infodb LIMIT 1").fetchone()
        count = connection.execute("SELECT COUNT(*),COUNT(DISTINCT id) FROM id").fetchone()
        if not row or str(row[2]).upper() != "COD" or count[0] != count[1] or count[0] != int(row[1]) or not 1 <= count[0] <= 600000:
            raise ValueError("Inconsistent or unsupported QualX COD database identity/count")
    companion_counts = {}
    for suffix, table in ((".info", "info"), (".search", "top")):
        with readonly(Path(str(main) + suffix), stop) as connection:
            companion_counts[suffix] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if not 0 <= companion_counts[suffix] <= count[0]:
                raise ValueError("QualX companion entry count exceeds the main database")
    identity = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {"path": str(main), "content_hash": identity, "file_hashes": hashes, "file_sizes": sizes, "file_mtimes": mtimes,
            "snapshot": str(row[0]), "phase_count": int(count[0]), "companion_counts": companion_counts, "source": "QualX COD Inorganic" if identity == OFFICIAL_CONTENT_HASH else "QualX COD (external)",
            "record_url": RECORD_URL if identity == OFFICIAL_CONTENT_HASH else "", "registered_at": timestamp(), "engine": ENGINE_VERSION}


def build_peak_index(main: Path, destination: Path, info: dict, stop: Event, report=lambda message: None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".sqlite", delete=False) as f:
        temporary = Path(f.name)
    try:
        with sqlite3.connect(temporary, factory=DatabaseConnection) as target, readonly(Path(str(main) + ".search"), stop) as source, readonly(main, stop) as original:
            create_index(target)
            counts = {str(i): int(n) for i, n in original.execute("SELECT id,nd FROM id")}
            seen = set()
            for index, (identity, count, values) in enumerate(source.execute("SELECT id,n,dval FROM top")):
                check_stop(stop)
                ds = numbers(values, limit=32)
                identity = str(identity)
                if len(ds) != int(count) or identity not in counts or not len(ds) or np.any(ds <= 0):
                    raise ValueError("Invalid strong-d reference index entry")
                seen.add(identity)
                target.execute("INSERT INTO phases VALUES(?,?,?)", (identity, counts[identity], len(ds)))
                target.executemany("INSERT INTO peaks VALUES(?,?,?)", [(identity, rank, float(d)) for rank, d in enumerate(ds)])
                if index % 2000 == 0:
                    report(f"Indexing stored strong d values {index:,}/{info['phase_count']:,}")
            # The official release has one main entry with nd=0 and no companions.
            # Retain its identity without inventing peaks; supplement other missing
            # top entries only from their already stored full peak lists.
            unavailable = []
            for identity in set(counts) - seen:
                check_stop(stop)
                row = original.execute("SELECT dvalue,intensita FROM id WHERE id=?", (identity,)).fetchone()
                ds, intensities = numbers(row[0]), numbers(row[1])
                if len(ds) != len(intensities) or len(ds) != counts[identity] or np.any(ds <= 0):
                    raise ValueError("Invalid stored peaks for missing strong-d entry")
                order = np.argsort(intensities)[::-1][:3]
                target.execute("INSERT INTO phases VALUES(?,?,?)", (identity, counts[identity], len(order)))
                target.executemany("INSERT INTO peaks VALUES(?,?,?)", [(identity, rank, float(ds[i])) for rank, i in enumerate(order)])
                if not len(order):
                    unavailable.append(identity)
            info["unsearchable_phase_ids"] = unavailable
            info["searchable_phase_count"] = len(counts) - len(unavailable)
            target.execute("INSERT INTO metadata VALUES('identity',?)", (info["content_hash"],))
            target.execute("INSERT INTO metadata VALUES('engine',?)", (ENGINE_VERSION,))
        check_stop(stop)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


class ReferenceDatabaseManager:
    def __init__(self, store: PhaseStore):
        self.store = store

    def configuration(self) -> dict:
        return self.store.read("reference-database.json", {})

    def register(self, path: Path, stop: Event | None = None, report=lambda message: None) -> dict:
        stop = stop or Event()
        started = time.perf_counter()
        info = validate_database(path, stop, report)
        index = self.store.root / "reference_indexes" / (info["content_hash"] + ".sqlite")
        build_peak_index(Path(info["path"]), index, info, stop, report)
        info["index_path"] = str(index.resolve())
        info["index_build_seconds"] = time.perf_counter() - started
        check_stop(stop)
        self.store.write("reference-database.json", info)
        return info

    def import_zip(self, archive: Path, stop: Event | None = None, report=lambda message: None) -> dict:
        stop = stop or Event()
        if not archive.is_file() or archive.stat().st_size > 2 * 1024**3:
            raise ValueError("Invalid/oversized QualX ZIP")
        md5 = hashlib.md5(usedforsecurity=False)
        with archive.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                check_stop(stop)
                md5.update(chunk)
        folder = self.store.root / "reference_databases"
        folder.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder, prefix="import-") as temporary:
            root = Path(temporary)
            with zipfile.ZipFile(archive) as zipped:
                members = [i for i in zipped.infolist() if not i.is_dir()]
                notices = [i for i in members if i.filename in {"NOTICE.txt", "NOTICE_JA.txt"}]
                if any(i.file_size > 128 * 1024 or stat.S_ISLNK(i.external_attr >> 16) for i in notices) or len({i.filename for i in members}) != len(members):
                    raise ValueError("Duplicate or oversized database notice")
                members = [i for i in members if i not in notices]
                if len(members) != 4 or sum(i.file_size for i in members) > 3 * 1024**3:
                    raise ValueError("ZIP must contain exactly the four QualX database files (maximum 3 GiB)")
                required_space = sum(i.file_size for i in members) + 100 * 1024**2
                if shutil.disk_usage(folder).free < required_space:
                    raise ValueError("Not enough free space to install the reference database")
                seen = set()
                for member in members:
                    relative = PurePosixPath(member.filename.replace("\\", "/"))
                    if relative.is_absolute() or ".." in relative.parts or ":" in str(relative) or stat.S_ISLNK(member.external_attr >> 16):
                        raise ValueError("Unsafe ZIP member path/type")
                    name = relative.name
                    if name in seen or member.file_size > 2 * 1024**3:
                        raise ValueError("Duplicate/oversized ZIP entry")
                    seen.add(name)
                    target = root / name
                    report("Installing " + name)
                    with zipped.open(member) as source, target.open("wb") as output:
                        while chunk := source.read(1024 * 1024):
                            check_stop(stop)
                            output.write(chunk)
            main = find_database(root)
            if seen != {main.name + s for s in SUFFIXES}:
                raise ValueError("ZIP companion file names do not match")
            # Validate before replacing the registered configuration; failures retain the old DB.
            info = validate_database(main, stop, report)
            installed = folder / info["content_hash"]
            if not installed.exists():
                shutil.copytree(root, installed)
            outcome = self.register(installed / main.name, stop, report)
            outcome["archive_md5"] = md5.hexdigest()
            outcome["official_archive_verified"] = md5.hexdigest() == ARCHIVE_MD5
            self.store.write("reference-database.json", outcome)
            return outcome

    def verify(self, stop: Event | None = None, report=lambda message: None) -> dict:
        current = self.configuration()
        if not current:
            raise ValueError("Reference database is not installed")
        verified = validate_database(Path(current["path"]), stop or Event(), report)
        if verified["content_hash"] != current["content_hash"]:
            raise ValueError("Database contents changed; import/register the new version")
        with readonly(Path(current["index_path"])) as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
            if not row or row[0] != current["content_hash"] or connection.execute("PRAGMA quick_check").fetchone()[0] != "ok" or connection.execute("SELECT COUNT(*) FROM phases").fetchone()[0] != current["phase_count"]:
                raise ValueError("Reference index identity mismatch; register the database again")
        current["file_mtimes"] = verified["file_mtimes"]
        self.store.write("reference-database.json", current)
        return current

    def remove(self) -> None:
        """Unregister only; never delete an external/QualX-owned database."""
        self.store.write("reference-database.json", {})


class QualXCODInorganicBackend:
    def __init__(self, info: dict, stop: Event | None = None):
        self.info = info
        self.stop = stop or Event()
        path = Path(info["path"])
        for suffix in SUFFIXES:
            file = Path(str(path) + suffix)
            if not file.is_file() or file.stat().st_size != info["file_sizes"][suffix] or file.stat().st_mtime_ns != info.get("file_mtimes", {}).get(suffix, file.stat().st_mtime_ns):
                raise ValueError("Reference database is missing/changed; use Reference Databases > Verify")
        self.main = readonly(path, self.stop)
        self.details = readonly(Path(str(path) + ".info"), self.stop)
        self.index = readonly(Path(info["index_path"]), self.stop)
        row = self.index.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
        if not row or row[0] != info["content_hash"]:
            self.close()
            raise ValueError("Reference index identity mismatch; register/verify again")
        self._metadata: dict[str, Candidate] = {}

    def close(self) -> None:
        self.main.close()
        self.details.close()
        self.index.close()

    def get_database_info(self) -> dict:
        return {**self.info, "notices": DATABASE_NOTICES}

    def get_metadata(self, phase_id: str) -> Candidate:
        if phase_id in self._metadata:
            return self._metadata[phase_id]
        row = self.main.execute("SELECT id,chemical_formula,spacegroup,name,mineralname FROM id WHERE id=?", (phase_id,)).fetchone()
        extra = self.details.execute("SELECT a,b,c,alpha,beta,gamma,volume,type FROM info WHERE id=?", (phase_id,)).fetchone()
        if row is None:
            raise ValueError("Reference phase metadata not found: " + phase_id)
        chemical = [str(r[0]) for r in self.main.execute("SELECT chemical_element FROM chemical WHERE id=?", (phase_id,))]
        return self._remember_metadata(phase_id, row, extra, chemical)

    def _remember_metadata(self, phase_id, row, extra, chemical) -> Candidate:
        has_details = extra is not None
        extra = extra or (0, 0, 0, 0, 0, 0, 0, "")
        import gemmi
        group = gemmi.find_spacegroup_by_name(str(row[2] or ""))
        candidate = Candidate(str(row[0]), "COD", str(row[1] or ""),
                              chemical,
                              str(row[2] or ""), group.number if group else 0, str(extra[7] or "").lower(),
                              [float(v or 0) for v in extra[:6]] if has_details else [], float(extra[6] or 0),
                              str(row[3] or row[4] or ""), url=f"https://www.crystallography.net/cod/{phase_id}.html",
                              content_hash=hashlib.sha256((self.info["content_hash"] + ":" + phase_id).encode()).hexdigest(),
                              revision=self.info["snapshot"])
        self._metadata[phase_id] = candidate
        return candidate

    def _prefetch_metadata(self, identities: list[str]) -> None:
        missing = [identity for identity in identities if identity not in self._metadata]
        if not missing:
            return
        slots = ",".join("?" for _ in missing)
        rows = self.main.execute("SELECT id,chemical_formula,spacegroup,name,mineralname FROM id WHERE id IN (" + slots + ")", missing).fetchall()
        details = {str(row[0]): row[1:] for row in self.details.execute("SELECT id,a,b,c,alpha,beta,gamma,volume,type FROM info WHERE id IN (" + slots + ")", missing)}
        chemicals: dict[str, list[str]] = {}
        for identity, symbol in self.main.execute("SELECT id,chemical_element FROM chemical WHERE id IN (" + slots + ")", missing):
            chemicals.setdefault(str(identity), []).append(str(symbol))
        for row in rows:
            identity = str(row[0])
            self._remember_metadata(identity, row, details.get(identity), chemicals.get(identity, []))

    def get_reference_peaks(self, phase_id: str) -> dict:
        row = self.main.execute("SELECT nd,dvalue,intensita FROM id WHERE id=?", (phase_id,)).fetchone()
        if row is None:
            raise ValueError("Reference phase not found")
        ds, intensity = numbers(row[1]), numbers(row[2])
        if len(ds) != int(row[0]) or len(ds) != len(intensity) or np.any(ds <= 0) or np.any(intensity < 0):
            raise ValueError("Inconsistent reference peak arrays")
        scale = float(np.max(intensity)) if len(intensity) else 0
        if scale <= 0:
            raise ValueError("Reference intensities are empty")
        return {"d": ds, "intensities": intensity * (100 / scale)}

    def retrieve_candidates(self, peaks: list[dict], constraints: SearchQuery, settings: CalculationSettings,
                            stop: Event, limit: int) -> list[Candidate]:
        constraints.validate()
        clauses, parameters = [], []
        for symbol in elements(constraints.required):
            clauses.append("EXISTS(SELECT 1 FROM chemical c WHERE c.id=m.id AND c.chemical_element=?)")
            parameters.append(symbol)
        for values, exclude in ((elements(constraints.allowed), False), (elements(constraints.excluded), True)):
            if values:
                slots = ",".join("?" for _ in values)
                clauses.append(f"NOT EXISTS(SELECT 1 FROM chemical c WHERE c.id=m.id AND c.chemical_element {'IN' if exclude else 'NOT IN'} ({slots}))")
                parameters.extend(values)
        if constraints.cod_ids:
            import re
            ids = re.findall(r"\d{7}", constraints.cod_ids)
            clauses.append("m.id IN (" + ",".join("?" for _ in ids) + ")")
            parameters.extend(ids)
        allowed = {str(r[0]) for r in self.main.execute("SELECT m.id FROM id m WHERE " + " AND ".join(clauses), parameters)} if clauses else None
        ranked = retrieve(self.index, peaks, settings, stop, allowed_ids=allowed)
        candidates = []
        for offset in range(0, len(ranked), 256):
            check_stop(stop)
            batch = ranked[offset:offset + 256]
            self._prefetch_metadata([identity for identity, _ in batch])
            for identity, _vote in batch:
                check_stop(stop)
                candidate = self.get_metadata(identity)
                if constraints.accepts(candidate):
                    candidates.append(candidate)
                    if len(candidates) >= limit:
                        return candidates
        return candidates
