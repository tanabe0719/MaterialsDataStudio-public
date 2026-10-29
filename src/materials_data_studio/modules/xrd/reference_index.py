"""Small d-spacing indexes shared by external references and user CIFs."""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from threading import Event
from typing import Protocol

import numpy as np

from materials_data_studio.modules.xrd.phase_models import (
    CalculationSettings,
    Candidate,
    SearchQuery,
)

ENGINE_VERSION = "mds-reference-1"


class DatabaseConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def readonly(path: Path, stop: Event | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10, factory=DatabaseConnection)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA cache_size=-8192")
        if stop is not None:
            connection.set_progress_handler(lambda: int(stop.is_set()), 1000)
    except BaseException:
        connection.close()
        raise
    return connection


def check_stop(stop: Event) -> None:
    if stop.is_set():
        raise InterruptedError("Reference database operation stopped")


def numbers(value, *, limit=20000) -> np.ndarray:
    """QualX stores comma-delimited decimal text, including trailing commas."""
    text = value.decode("utf-8") if isinstance(value, bytes) else str(value or "")
    if len(text) > 2_000_000:
        raise ValueError("Reference peak field exceeds its size limit")
    tokens = [t.strip() for t in text.split(",") if t.strip()]
    if len(tokens) > limit:
        raise ValueError("Reference peak field has too many values")
    result = np.array([float(t) for t in tokens], dtype=float)
    if not np.all(np.isfinite(result)):
        raise ValueError("Reference peaks contain nonfinite values")
    return result


def create_index(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE peaks (phase_id TEXT NOT NULL, rank INTEGER NOT NULL, d REAL NOT NULL,
                            PRIMARY KEY (phase_id,rank)) WITHOUT ROWID;
        CREATE INDEX peaks_d ON peaks(d,phase_id,rank);
        CREATE TABLE phases (phase_id TEXT PRIMARY KEY, peak_count INTEGER NOT NULL,
                             top_count INTEGER NOT NULL) WITHOUT ROWID;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
    """)


def retrieve(index: sqlite3.Connection, peaks: list[dict], settings: CalculationSettings, stop: Event,
             *, allowed_ids: set[str] | None = None, scale_limit: float = 0) -> list[tuple[str, float]]:
    """Vote through indexed d ranges; never read all detailed reference arrays."""
    selected = sorted((p for p in peaks if p.get("included", True) and settings.lower <= p["position"] <= settings.upper
                       and p["intensity"] > 0), key=lambda p: p["intensity"], reverse=True)[:32]
    if not selected:
        raise ValueError("Select observed peaks within the search range")
    maximum = max(p["intensity"] for p in selected)
    votes: dict[str, float] = {}
    hits: dict[str, int] = {}
    for peak in selected:
        check_stop(stop)
        # A fixed zero shift changes reference 2theta; invert it for the d lookup.
        position = float(peak["position"]) - settings.zero_shift
        uncertainty = min(settings.tolerance, max(0., float(peak.get("position_sigma", 0))))
        tolerance = settings.tolerance + uncertainty
        lower, upper = max(.001, position - tolerance), min(179.999, position + tolerance)
        d_low = settings.wavelength / (2 * math.sin(math.radians(upper / 2)))
        d_high = settings.wavelength / (2 * math.sin(math.radians(lower / 2)))
        rows = index.execute("SELECT phase_id,rank FROM peaks WHERE d BETWEEN ? AND ?", (d_low / (1 + scale_limit), d_high / (1 - scale_limit))).fetchall()
        per_phase: dict[str, float] = {}
        for phase_id, rank in rows:
            if allowed_ids is None or phase_id in allowed_ids:
                per_phase[phase_id] = max(per_phase.get(phase_id, 0), 1 / math.sqrt(rank + 1))
        # Common d values carry less identifying information than rare ones.
        identifying = 1 / math.log2(2 + len(per_phase))
        weight = math.sqrt(float(peak["intensity"]) / maximum) * identifying
        for phase_id, reference_weight in per_phase.items():
            votes[phase_id] = votes.get(phase_id, 0) + weight * reference_weight
            hits[phase_id] = hits.get(phase_id, 0) + 1
    counts = {phase_id: (int(n), int(top)) for phase_id, n, top in index.execute("SELECT phase_id,peak_count,top_count FROM phases")}
    ranked = []
    for identity, vote in votes.items():
        n, top = counts[identity]
        # Mild reflection-density correction avoids preferring very large cells.
        score = vote * (1 + math.log1p(hits[identity])) / (math.sqrt(max(1, top)) * (1 + .025 * math.log1p(n)))
        ranked.append((identity, score))
    return sorted(ranked, key=lambda row: (-row[1], row[0]))


class ReferenceDatabaseBackend(Protocol):
    def get_metadata(self, phase_id: str) -> Candidate: ...
    def get_reference_peaks(self, phase_id: str) -> dict: ...
    def get_database_info(self) -> dict: ...
    def retrieve_candidates(self, peaks: list[dict], constraints: SearchQuery, settings: CalculationSettings,
                            stop: Event, limit: int) -> list[Candidate]: ...
