"""Disposable metadata indexes keep chemical filters away from large peak blobs."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from threading import Lock

from materials_data_studio.modules.xrd.reference_index import check_stop, readonly

_BUILD_LOCK = Lock()


def metadata_index(source, info: dict, stop) -> sqlite3.Connection:
    path = Path(info["metadata_path"])
    identity = json.dumps([info["content_hash"], info["size"], info["mtime_ns"], 1])
    with _BUILD_LOCK:
        check_stop(stop)
        if path.is_file():
            cached = None
            try:
                cached = readonly(path, stop)
                if cached.execute("SELECT value FROM identity").fetchone() == (identity,):
                    return cached
            except sqlite3.DatabaseError:
                pass  # A disposable cache is rebuilt, never substituted for the source.
            if cached is not None:
                cached.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="mp-metadata-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        try:
            connection = sqlite3.connect(temporary)
            try:
                connection.executescript("""
                    CREATE TABLE identity(value TEXT NOT NULL);
                    CREATE TABLE meta(id TEXT PRIMARY KEY, candidate TEXT NOT NULL, element_key TEXT NOT NULL,
                                      symbols TEXT NOT NULL, space_group TEXT NOT NULL, space_group_number TEXT NOT NULL,
                                      crystal_system TEXT NOT NULL, volume REAL NOT NULL) WITHOUT ROWID;
                """)
                cursor = source.execute("SELECT phase_id,candidate FROM cards")
                while batch := cursor.fetchmany(512):
                    check_stop(stop)
                    rows = []
                    for key, text in batch:
                        card = json.loads(text)
                        symbols = sorted(set(card.get("elements", [])))
                        rows.append((key, text, ",".join(symbols), "|" + "|".join(symbols) + "|",
                                     card.get("space_group", "").replace(" ", "").lower(), str(card.get("space_group_number", 0)),
                                     card.get("crystal_system", "").lower(), float(card.get("volume", 0))))
                    connection.executemany("INSERT INTO meta VALUES(?,?,?,?,?,?,?,?)", rows)
                connection.execute("CREATE INDEX meta_elements ON meta(element_key,id)")
                connection.execute("INSERT INTO identity VALUES(?)", (identity,))
                connection.commit()
            finally:
                connection.close()
            check_stop(stop)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return readonly(path, stop)
