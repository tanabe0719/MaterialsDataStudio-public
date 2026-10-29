"""Developer-only, resumable Delta/JSONL -> offline MP diffraction index builder.

Install optional builder tools with: uv sync --extra mp-builder --extra dev
No credentials are needed for public S3. Never enumerate uncommitted Parquet files.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import importlib.metadata
import json
import os
import sqlite3
import sys
import time
import warnings
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from materials_data_studio.modules.xrd.mp_database import FORMAT, MPDatabaseManager
from materials_data_studio.modules.xrd.phase_models import Candidate
from materials_data_studio.modules.xrd.phase_store import PhaseStore, timestamp
from materials_data_studio.modules.xrd.reference_index import DatabaseConnection, create_index

COLUMNS = ["material_id", "formula_pretty", "structure", "builder_meta", "symmetry", "energy_above_hull", "theoretical", "deprecated", "database_IDs", "origins"]


def delta_source(uri, snapshot, version, formulas=None, prioritize=None, cache=None):
    import pyarrow.compute as pc
    from deltalake import DeltaTable
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
    table = DeltaTable(uri, version=snapshot, storage_options={"AWS_SKIP_SIGNATURE": "true", "AWS_REGION": "us-east-1"})
    dataset = table.to_pyarrow_dataset()
    names = dataset.schema.names
    required = {"material_id", "structure", "builder_meta", "version"}
    if not required <= set(names):
        raise ValueError("Source schema lacks fields: " + str(required - set(names)))
    files = sorted(table.file_uris(partition_filters=[("version", "=", version)]))
    if not files:
        raise ValueError("Pinned database partition does not exist in selected Delta snapshot")
    checksums = None
    if cache:
        import pyarrow.fs as fs
        from scripts.mp_source_cache import cache_snapshot
        checksums = cache_snapshot(uri, files, cache)
        dataset = table.to_pyarrow_dataset(filesystem=fs.SubTreeFileSystem(str(cache.resolve()), fs.LocalFileSystem()))
    source = {"collection": uri, "delta_snapshot": snapshot, "partition": version, "files": files,
              "schema": str(dataset.schema), "columns": [c for c in COLUMNS if c in names]}
    if checksums:
        source["source_file_sha256"] = checksums
    partition = pc.field("version") == version
    eligible = partition & (pc.field(("builder_meta", "license")) == "BY-C") & (pc.field(("builder_meta", "batch_id")) != "gnome_r2scan_statics")
    if formulas:
        eligible = eligible & pc.field("formula_pretty").isin(formulas)
        source["selected_formulas"] = sorted(formulas)
    source["total_records"] = dataset.count_rows(filter=partition)
    source["eligible_records"] = dataset.count_rows(filter=eligible)
    def records():
        selections = [eligible]
        if prioritize:
            selections.insert(0, eligible & pc.field("formula_pretty").isin(prioritize))
        for selection in selections:
            scanner = dataset.scanner(columns=source["columns"], filter=selection,
                                      batch_size=64, batch_readahead=1, fragment_readahead=1, use_threads=False)
            for batch in scanner.to_batches():
                yield from batch.to_pylist()
    return source, records


def json_source(path):
    with path.open("rb") as handle:
        checksum = hashlib.file_digest(handle, "sha256").hexdigest()
    def records():
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as source:
            for line in source:
                if len(line) > 16 * 1024**2:
                    raise ValueError("JSONL record exceeds 16 MiB")
                if line.strip():
                    yield json.loads(line)
    return {"collection": path.name, "sha256": checksum}, records


def calculate(record, version, max_sites, engine="pymatgen"):
    """Runs in a separate process; never loads an entire dataset."""
    identity = str(record.get("material_id", ""))
    meta = record.get("builder_meta") or {}
    if not identity.startswith("mp-"):
        return identity, "excluded", "Missing MP identity"
    if meta.get("license") != "BY-C" or meta.get("batch_id") == "gnome_r2scan_statics":
        return identity, "excluded", "Unknown/restricted license or GNoME"
    if str(meta.get("database_version", "")).replace(".", "-").removeprefix("v") != version:
        return identity, "excluded", "Missing/mismatched database version"
    if record.get("deprecated"):
        return identity, "excluded", "Deprecated"
    if max_sites and len((record.get("structure") or {}).get("sites", [])) > max_sites:
        return identity, "excluded", "Above selected site-count limit"
    try:
        from pymatgen.analysis.diffraction.xrd import XRDCalculator
        from pymatgen.core import Structure
        data = record["structure"]
        # Parquet expands optional species keys to null; pymatgen expects absence.
        for site in data.get("sites", []):
            for species in site.get("species", []):
                for key in list(species):
                    if species[key] is None:
                        del species[key]
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Not all sites have property.*", category=UserWarning)
            structure = Structure.from_dict(data)
        # Magnetic moments, forces and trajectories do not enter this XRD model.
        # Keep only the lattice, coordinates, species and occupancies locally.
        for key in list(structure.site_properties):
            structure.remove_site_property(key)
        structure.properties.clear()
        if engine == "batch":
            from scripts.mp_diffraction import batch_pattern
            pattern = batch_pattern(structure)
        else:
            pattern = XRDCalculator(wavelength=1.540598).get_pattern(structure, two_theta_range=(1, 150))
        if not len(pattern.x) or len(pattern.x) > 20000:
            raise ValueError("Empty or excessive diffraction pattern")
        if not np.all(np.isfinite(pattern.y)) or not np.all(np.isfinite(pattern.d_hkls)) or not np.all(np.asarray(pattern.d_hkls) > 0):
            raise ValueError("Invalid diffraction peak arrays")
        symmetry = record.get("symmetry") or {}
        serialized = structure.as_dict()
        fingerprint = hashlib.sha256(json.dumps(serialized, sort_keys=True).encode()).hexdigest()
        card = Candidate(identity, "Materials Project", structure.composition.reduced_formula,
                         sorted(str(e) for e in structure.composition.elements), symmetry.get("symbol", ""),
                         int(symmetry.get("number") or 0), str(symmetry.get("crystal_system") or ""),
                         [*structure.lattice.abc, *structure.lattice.angles], structure.volume,
                         title="DFT-relaxed structure — not experimental confirmation", url="https://materialsproject.org/materials/" + identity,
                         content_hash=fingerprint, revision=version, site_count=len(structure))
        peaks = {"d": np.asarray(pattern.d_hkls).tolist(), "intensities": np.asarray(pattern.y).tolist(), "hkl": pattern.hkls}
        details = {"energy_above_hull": record.get("energy_above_hull"), "theoretical": record.get("theoretical"),
                   "origin": "DFT-relaxed", "builder_meta": meta, "license": "CC BY 4.0", "database_IDs": record.get("database_IDs"), "origins": record.get("origins")}
        return identity, "included", (card.to_dict(), peaks, serialized, details)
    except Exception as exc:
        return identity, "failed", str(exc)[:1000]


def build(output, source, records, version, *, limit=0, workers=2, max_sites=0, retry=False, engine="pymatgen"):
    started = time.perf_counter()
    identity = hashlib.sha256(json.dumps(source, sort_keys=True, default=str).encode()).hexdigest()
    existed = output.exists()
    output.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(output, factory=DatabaseConnection)
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='cards'").fetchone():
        if existed:
            connection.close()
            raise ValueError("Existing output is not an MP index; choose a new file")
        create_index(connection)
        connection.executescript("CREATE TABLE cards(phase_id TEXT PRIMARY KEY,candidate TEXT,pattern TEXT,structure TEXT,details TEXT); CREATE TABLE checkpoint(phase_id TEXT PRIMARY KEY,status TEXT,reason TEXT);")
    existing = connection.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()
    old = json.loads(existing[0]) if existing else {}
    calculator = importlib.metadata.version("pymatgen") + (" / mds-mp-batch-xrd-1" if engine == "batch" else "")
    if old and (old["snapshot_identity"] != identity or old["snapshot"] != version or old["max_sites"] != max_sites or old["calculator"] != calculator):
        connection.close()
        raise ValueError("Resume requires identical source snapshot, version, site limit and calculator")
    done = {row[0] for row in connection.execute("SELECT phase_id FROM checkpoint" + (" WHERE status!='failed'" if retry else ""))}
    manifest = {"format": FORMAT, "source": "Materials Project", "snapshot": version, "snapshot_identity": identity,
                "source_dataset": source, "engine": "mds-reference-1", "calculator": calculator,
                "wavelength": 1.540598, "range": [1, 150], "normalization": "maximum=100; pymatgen standard XRD scattering",
                "license": "CC BY 4.0 (BY-C records only)", "max_sites": max_sites, "created": old.get("created", timestamp()),
                "building": True, "included": 0, "excluded": 0, "failed": 0}
    def checkpoint():
        counts = dict(connection.execute("SELECT status,count(*) FROM checkpoint GROUP BY status"))
        manifest.update({k: counts.get(k, 0) for k in ("included", "excluded", "failed")})
        manifest["elapsed_seconds"] = old.get("elapsed_seconds", 0) + time.perf_counter() - started
        connection.execute("INSERT OR REPLACE INTO metadata VALUES('manifest',?)", (json.dumps(manifest, default=str),))
        connection.commit()
    checkpoint()
    processed = 0
    exhausted = True
    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            pending = []
            def accept(future):
                nonlocal processed
                name, status, payload = future.result()
                for table in ("cards", "peaks", "phases"):
                    connection.execute(f"DELETE FROM {table} WHERE phase_id=?", (name,))
                if status == "included":
                    card, peaks, structure, details = payload
                    connection.execute("INSERT INTO cards VALUES(?,?,?,?,?)", (name, json.dumps(card), zlib.compress(json.dumps(peaks).encode(), level=6),
                                       zlib.compress(json.dumps(structure).encode(), level=6), json.dumps(details, default=str)))
                    indices = np.argsort(peaks["intensities"])[::-1][:16]
                    connection.execute("INSERT INTO phases VALUES(?,?,?)", (name, len(peaks["d"]), len(indices)))
                    connection.executemany("INSERT INTO peaks VALUES(?,?,?)", [(name, rank, peaks["d"][int(i)]) for rank, i in enumerate(indices)])
                connection.execute("INSERT OR REPLACE INTO checkpoint VALUES(?,?,?)", (name, status, "" if status == "included" else str(payload)))
                processed += 1
                checkpoint()
                if processed % 100 == 0:
                    print(json.dumps({k: manifest[k] for k in ("included", "excluded", "failed", "elapsed_seconds")}), flush=True)
            for record in records():
                if record.get("material_id") in done:
                    continue
                if limit and processed + len(pending) >= limit:
                    exhausted = False
                    break
                pending.append(pool.submit(calculate, record, version, max_sites, engine))
                done.add(record.get("material_id"))
                if len(pending) >= workers * 2:
                    accept(pending.pop(0))
            for future in pending:
                accept(future)
        manifest["building"] = False
        manifest["exhaustive"] = exhausted
    finally:
        checkpoint()
        connection.close()
    from threading import Event

    from materials_data_studio.modules.xrd.mp_database import file_hash
    output.with_suffix(output.suffix + ".sha256").write_text(file_hash(output, Event()), encoding="ascii")
    print(json.dumps(manifest, default=str, indent=2), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inspect", "build", "verify", "register"])
    parser.add_argument("--uri", help="Explicit Delta table URI from the observed public listing")
    parser.add_argument("--delta-version", type=int, help="Required, fixed Delta transaction version")
    parser.add_argument("--database-version", default="2026-09-28")
    parser.add_argument("--jsonl", type=Path, help="Versioned local MP JSONL export with builder_meta licensing")
    parser.add_argument("--output", type=Path, default=Path("materials-project.sqlite"))
    parser.add_argument("--store", type=Path)
    parser.add_argument("--limit", type=int, default=0, help="Maximum additional records; 0 scans entire selected snapshot")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-sites", type=int, default=0)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--engine", choices=["pymatgen", "batch"], default="pymatgen", help="Batch evaluates the same scattering model in bounded vectorized blocks")
    parser.add_argument("--cache-dir", type=Path, help="Optional bounded cache of only selected committed Parquet objects, for high-latency S3 links")
    parser.add_argument("--formulas", nargs="+", help="Optional explicit formula subset, pushed down to the source")
    parser.add_argument("--prioritize-formulas", nargs="+", help="Process these formulas first, then the same complete source; compatible with resume")
    args = parser.parse_args()
    if args.action in {"verify", "register"}:
        import tempfile
        if args.action == "register" and not args.store:
            parser.error("--store is required for registration")
        with tempfile.TemporaryDirectory() as folder:
            info = MPDatabaseManager(PhaseStore(args.store or Path(folder))).register(args.output)
            print(json.dumps(info, indent=2, default=str))
        return
    if args.workers < 1 or args.workers > 16 or args.limit < 0 or args.max_sites < 0:
        parser.error("Use 1–16 workers and nonnegative limits")
    if args.jsonl:
        source, records = json_source(args.jsonl)
    else:
        if not args.uri or args.delta_version is None:
            parser.error("--uri and --delta-version are required; inspect/pin the source first")
        source, records = delta_source(args.uri, args.delta_version, args.database_version, args.formulas, args.prioritize_formulas, args.cache_dir)
    if args.action == "inspect":
        print(json.dumps(source, indent=2))
    else:
        build(args.output, source, records, args.database_version, limit=args.limit,
              workers=args.workers, max_sites=args.max_sites, retry=args.retry_failed, engine=args.engine)


if __name__ == "__main__":
    main()
