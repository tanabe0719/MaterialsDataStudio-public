"""Losslessly compress an existing MP index into a new standalone SQLite file."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import sys
import zlib
from pathlib import Path
from threading import Event

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from materials_data_studio.modules.xrd.mp_codec import decode_pattern, encode_pattern
from materials_data_studio.modules.xrd.mp_database import FORMAT, decode_json, file_hash
from materials_data_studio.modules.xrd.reference_index import (
    DatabaseConnection,
    create_index,
    readonly,
)


def compress_row(row, binary=False):
    values = list(row)
    # Preserve every number, hkl and coordinate byte-for-byte after decompression.
    if binary:
        before = decode_pattern(values[2])
        values[2] = encode_pattern(before)
        after = decode_pattern(values[2])
        for name in ("d", "intensities"):
            if np.asarray(before[name], dtype="<f8").tobytes() != np.asarray(after[name], dtype="<f8").tobytes():
                raise ValueError("Lossless numeric verification failed")
        if before != after:
            raise ValueError("Lossless pattern verification failed")
        if isinstance(values[4], str):
            encoded = zlib.compress(values[4].encode(), 6)
            if decode_json(encoded) != json.loads(values[4]):
                raise ValueError("Lossless metadata verification failed")
            values[4] = encoded
    elif isinstance(values[2], str):
        values[2] = zlib.compress(values[2].encode(), level=6)
    else:
        decode_json(values[2])
    if isinstance(values[3], str):
        values[3] = zlib.compress(values[3].encode(), level=6)
    return tuple(values)


def compact(source: Path, output: Path, workers=8, *, binary=False):
    if output.exists() or output.resolve() == source.resolve():
        raise ValueError("Choose a new output file; the original index is preserved")
    output.parent.mkdir(parents=True, exist_ok=True)
    with readonly(source) as original, sqlite3.connect(output, factory=DatabaseConnection) as target:
        manifest = json.loads(original.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        if manifest.get("format") not in {FORMAT, "mds-mp-reference-2"} or manifest.get("building"):
            raise ValueError("Only a completed MP index can be compacted")
        create_index(target)
        target.executescript("CREATE TABLE cards(phase_id TEXT PRIMARY KEY,candidate TEXT,pattern BLOB,structure BLOB,details TEXT); CREATE TABLE checkpoint(phase_id TEXT PRIMARY KEY,status TEXT,reason TEXT);")
        manifest["pattern_encoding"] = "float64-shuffle-hkl-varint-zlib-v2" if binary else "zlib-json-v1"
        if binary:
            manifest["format"] = "mds-mp-reference-2"
            manifest["conversion"] = {"source_sha256": file_hash(source, Event()), "verification": "Every pattern bit, hkl group and metadata value checked"}
        manifest["building"] = True
        target.execute("INSERT INTO metadata VALUES('manifest',?)", (json.dumps(manifest),))
        target.commit()
        executor = concurrent.futures.ProcessPoolExecutor if binary else concurrent.futures.ThreadPoolExecutor
        with executor(max_workers=workers) as pool:
            rows = original.execute("SELECT phase_id,candidate,pattern,structure,details FROM cards")
            pending = []
            done = 0
            def accept(future):
                nonlocal done
                target.execute("INSERT INTO cards VALUES(?,?,?,?,?)", future.result())
                done += 1
                if done % 1000 == 0:
                    target.commit()
                    print(f"Compressed {done:,}/{manifest['included']:,}", flush=True)
            for row in rows:
                pending.append(pool.submit(compress_row, row, binary))
                if len(pending) >= workers * 2:
                    accept(pending.pop(0))
            for future in pending:
                accept(future)
        for table in ("peaks", "phases", "checkpoint"):
            cursor = original.execute("SELECT * FROM " + table)
            width = len(cursor.description)
            while batch := cursor.fetchmany(1000):
                target.executemany(f"INSERT INTO {table} VALUES({','.join('?' for _ in range(width))})", batch)
            print("Copied " + table, flush=True)
        manifest["building"] = False
        target.execute("UPDATE metadata SET value=? WHERE key='manifest'", (json.dumps(manifest),))
    checksum = file_hash(output, Event())
    output.with_suffix(output.suffix + ".sha256").write_text(checksum, encoding="ascii")
    print(json.dumps({"source_bytes": source.stat().st_size, "output_bytes": output.stat().st_size, "sha256": checksum}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--binary", action="store_true", help="Lossless float64/hkl representation with per-record verification")
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("Use 1–16 workers")
    compact(args.source, args.output, args.workers, binary=args.binary)


if __name__ == "__main__":
    main()
