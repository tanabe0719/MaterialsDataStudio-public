"""Reusable offline reference retrieval and MDS detailed ranking engine."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict
from threading import Event
from typing import Any, cast

import numpy as np

from materials_data_studio.modules.xrd.mp_database import MaterialsProjectBackend, MPDatabaseManager
from materials_data_studio.modules.xrd.phase_models import (
    CalculationSettings,
    Candidate,
    SearchQuery,
    match_peaks,
)
from materials_data_studio.modules.xrd.phase_score import PreparedPeakScore
from materials_data_studio.modules.xrd.phase_search import CALCULATOR, PhaseSearchService
from materials_data_studio.modules.xrd.qualx_database import (
    QualXCODInorganicBackend,
    ReferenceDatabaseManager,
)
from materials_data_studio.modules.xrd.reference_index import (
    ENGINE_VERSION,
    DatabaseConnection,
    check_stop,
    create_index,
    retrieve,
)


class UserCIFBackend:
    """Index only changed user CIFs, reusing the existing simulation/cache service."""
    def __init__(self, service: PhaseSearchService, settings: CalculationSettings, peaks: list[dict], stop: Event, report=lambda message: None):
        self.service, self.stop = service, stop
        folder = service.store.root / "reference_indexes"
        folder.mkdir(exist_ok=True)
        self.connection = sqlite3.connect(folder / "user.sqlite", timeout=10, factory=DatabaseConnection)
        if not self.connection.execute("SELECT 1 FROM sqlite_master WHERE name='peaks'").fetchone():
            create_index(self.connection)
            self.connection.execute("CREATE TABLE cards (phase_id TEXT PRIMARY KEY, conditions TEXT, candidate TEXT, pattern TEXT, cif TEXT)")
            self.connection.commit()
        library = service.store.library()
        active = {c.identity for c in library}
        conditions = json.dumps({"wavelength": settings.wavelength, "lower": settings.lower, "upper": settings.upper,
                                 "radiation": settings.radiation, "calculator": CALCULATOR}, sort_keys=True)
        self.errors = []
        try:
            for identity, in self.connection.execute("SELECT phase_id FROM cards").fetchall():
                if identity not in active:
                    for table in ("cards", "peaks", "phases"):
                        self.connection.execute(f"DELETE FROM {table} WHERE phase_id=?", (identity,))
            for index, candidate in enumerate(library):
                check_stop(stop)
                cached = self.connection.execute("SELECT conditions FROM cards WHERE phase_id=?", (candidate.identity,)).fetchone()
                if cached and cached[0] == conditions:
                    continue
                report(f"Indexing user CIF {index + 1}/{len(library)}")
                rows, errors = service.calculate([candidate], peaks, settings, stop, report)
                self.errors.extend(errors)
                if not rows:
                    continue
                result = rows[0]
                pattern = result["pattern"]
                ds, intensities = np.array(pattern["d"]), np.array(pattern["intensities"])
                if not len(ds) or len(ds) != len(intensities):
                    self.errors.append(candidate.identity + ": reference d values unavailable")
                    continue
                for table in ("cards", "peaks", "phases"):
                    self.connection.execute(f"DELETE FROM {table} WHERE phase_id=?", (candidate.identity,))
                top = [int(i) for i in np.argsort(intensities)[::-1] if intensities[i] > 0][:16]
                self.connection.execute("INSERT INTO cards VALUES(?,?,?,?,?)", (candidate.identity, conditions,
                                        json.dumps(candidate.to_dict()), json.dumps(pattern), result["cif"]))
                self.connection.execute("INSERT INTO phases VALUES(?,?,?)", (candidate.identity, len(ds), len(top)))
                self.connection.executemany("INSERT INTO peaks VALUES(?,?,?)", [(candidate.identity, rank, float(ds[i])) for rank, i in enumerate(top)])
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            self.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def get_metadata(self, phase_id: str) -> Candidate:
        row = self.connection.execute("SELECT candidate FROM cards WHERE phase_id=?", (phase_id,)).fetchone()
        if row is None:
            raise ValueError("User CIF reference not found")
        return Candidate(**json.loads(row[0]))

    def get_reference_peaks(self, phase_id: str) -> dict:
        row = self.connection.execute("SELECT pattern FROM cards WHERE phase_id=?", (phase_id,)).fetchone()
        if row is None:
            raise ValueError("User CIF reference peaks not found")
        pattern = json.loads(row[0])
        return {"d": np.array(pattern["d"]), "intensities": np.array(pattern["intensities"])}

    def cif(self, phase_id: str) -> str:
        row = self.connection.execute("SELECT cif FROM cards WHERE phase_id=?", (phase_id,)).fetchone()
        return row[0] if row else ""

    def get_database_info(self) -> dict:
        identities = [row[0] for row in self.connection.execute("SELECT phase_id FROM cards ORDER BY phase_id")]
        return {"source": "User CIF Library", "phase_count": len(identities), "engine": ENGINE_VERSION,
                "content_hash": hashlib.sha256(json.dumps(identities).encode()).hexdigest(), "snapshot": CALCULATOR}

    def retrieve_candidates(self, peaks: list[dict], constraints: SearchQuery, settings: CalculationSettings,
                            stop: Event, limit: int) -> list[Candidate]:
        ranked = retrieve(self.connection, peaks, settings, stop)
        candidates = []
        for identity, _vote in ranked:
            candidate = self.get_metadata(identity)
            if constraints.accepts(candidate):
                candidates.append(candidate)
                if len(candidates) == limit:
                    break
        return candidates


class ReferenceSearchEngine:
    def __init__(self, service: PhaseSearchService):
        self.service = service
        self.last_metrics: dict = {}

    def search(self, peaks: list[dict], query: SearchQuery, settings: CalculationSettings, *, cod=True, user=True,
               mp=False, mp_scale_limit=.01, candidate_limit=1000, result_limit=100, stop: Event | None = None, report=lambda message: None) -> tuple[list[Candidate], list[dict], list[str]]:
        query.validate()
        settings.validate()
        if not 50 <= candidate_limit <= 5000 or not 1 <= result_limit <= 500:
            raise ValueError("Use 50–5,000 retrieval candidates and 1–500 saved results")
        if not np.isfinite(mp_scale_limit) or not 0 <= mp_scale_limit <= .03:
            raise ValueError("MP lattice scale range must be between 0 and 3%")
        stop = stop or Event()
        started = time.perf_counter()
        backends = []
        results, candidates, errors = [], [], []
        metrics = {"engine": ENGINE_VERSION, "retrieval_seconds": 0., "ranking_seconds": 0., "scale_seconds": 0., "peak_read_seconds": 0., "scored": 0}
        scorer = PreparedPeakScore(peaks, settings) if mp else None
        def configured(manager, backend_class):
            info = manager.configuration()
            if not info:
                raise ValueError("not installed")
            return backend_class(info, stop)
        try:
            for enabled, name, factory in (
                (cod, "COD", lambda: configured(ReferenceDatabaseManager(self.service.store), QualXCODInorganicBackend)),
                (user, "User CIF", lambda: UserCIFBackend(self.service, settings, peaks, stop, report)),
                (mp, "Materials Project", lambda: configured(MPDatabaseManager(self.service.store), MaterialsProjectBackend)),
            ):
                if enabled:
                    try:
                        backend = factory()
                        backends.append(backend)
                        if isinstance(backend, UserCIFBackend):
                            errors.extend(backend.errors)
                    except InterruptedError:
                        raise
                    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
                        errors.append(name + " unavailable; import or verify it in Reference Databases: " + str(exc))
            if not backends:
                raise ValueError("\n".join(errors) or "Select a reference database")
            for backend in backends:
                info = backend.get_database_info()
                report("Retrieving candidates from " + info["source"])
                before = time.perf_counter()
                selected = (backend.retrieve_candidates(peaks, query, settings, stop, candidate_limit, scale_limit=mp_scale_limit)
                            if isinstance(backend, MaterialsProjectBackend) else backend.retrieve_candidates(peaks, query, settings, stop, candidate_limit))
                metrics["retrieval_seconds"] += time.perf_counter() - before
                candidates.extend(selected)
                for index, candidate in enumerate(selected):
                    check_stop(stop)
                    try:
                        before = time.perf_counter()
                        reference = (backend.get_search_peaks(candidate.identity) if isinstance(backend, MaterialsProjectBackend)
                                     else backend.get_reference_peaks(candidate.identity))
                        metrics["peak_read_seconds"] += time.perf_counter() - before
                        ds, intensity = np.asarray(reference["d"]), np.asarray(reference["intensities"])
                        scale, scale_penalty = 1., 0.
                        if isinstance(backend, MaterialsProjectBackend):
                            before = time.perf_counter()
                            scale, scale_penalty = select_mp_scale(peaks, ds, intensity, settings, mp_scale_limit, stop, scorer=scorer)
                            metrics["scale_seconds"] += time.perf_counter() - before
                            ds = ds * scale
                        argument = settings.wavelength / (2 * ds)
                        valid = (argument > 0) & (argument < 1) & (intensity > 0)
                        positions = np.rad2deg(2 * np.arcsin(argument[valid]))
                        # Keep neighbors that a bounded zero shift may bring inside range.
                        within = (positions + settings.zero_shift >= settings.lower) & (positions + settings.zero_shift <= settings.upper)
                        positions, intensity, ds = positions[within], intensity[valid][within], ds[valid][within]
                        order = np.argsort(positions)
                        pattern = {"positions": positions[order].tolist(), "intensities": intensity[order].tolist(), "d": ds[order].tolist(), "hkl": []}
                        before = time.perf_counter()
                        matched = match_peaks(peaks, pattern["positions"], pattern["intensities"], settings)
                        matched["raw_score"] = matched["score"]
                        matched["score"] = max(0., matched["score"] - scale_penalty)
                        metrics["ranking_seconds"] += time.perf_counter() - before
                        metrics["scored"] += 1
                        provenance = {k: info[k] for k in ("source", "phase_count", "engine", "snapshot", "content_hash", "record_url",
                                                         "format", "calculator", "wavelength", "range", "license", "snapshot_identity") if k in info}
                        results.append({"candidate": candidate.to_dict(), "pattern": pattern, "match": matched, "settings": asdict(settings),
                                        "cif": backend.cif(candidate.identity) if isinstance(backend, UserCIFBackend) else "",
                                        "database": provenance, "calculator": "MDS Match Score / " + ENGINE_VERSION, "adopted": False,
                                        **({"mp_details": backend.details(candidate.identity), "lattice_scale": scale, "scale_limit": mp_scale_limit, "scale_penalty": scale_penalty} if isinstance(backend, MaterialsProjectBackend) else {})})
                        if len(results) > result_limit * 2:
                            results.sort(key=lambda result: (-result["match"]["score"], result["candidate"]["identity"]))
                            del results[result_limit:]
                        if index % 20 == 0:
                            report(f"MDS Match Score: {index + 1}/{len(selected)} in {info['source']}")
                    except InterruptedError:
                        raise
                    except (ValueError, KeyError, sqlite3.Error) as exc:
                        errors.append(candidate.identity + ": " + str(exc))
            check_stop(stop)
            results.sort(key=lambda result: (-result["match"]["score"], result["candidate"]["identity"]))
            annotate_results(results)
            retained, used_bytes = [], 0
            for result in results[:result_limit]:
                size = len(json.dumps(result).encode())
                if used_bytes + size > 12 * 1024**2:
                    errors.append("Saved-result size limit reached; fewer results retained. Narrow the search or result count.")
                    break
                retained.append(result)
                used_bytes += size
            metrics["total_seconds"] = time.perf_counter() - started
            metrics["retrieved"] = len(candidates)
            self.last_metrics = metrics
            return candidates, retained, errors
        except sqlite3.OperationalError as exc:
            check_stop(stop)
            raise ValueError("Reference database could not be read: " + str(exc)) from exc
        finally:
            for backend in backends:
                backend.close()


def select_mp_scale(peaks, ds, intensity, settings, limit, stop, *, scorer=None):
    """A bounded isotropic cell correction with an explicit complexity penalty.

    The penalty is a transparent ranking heuristic (2 points per 1% strain),
    not a calibrated physical probability. Fixed instrumental zero shift stays fixed.
    """
    if not limit:
        return 1., 0.
    from scipy.optimize import minimize_scalar
    if not np.all(np.isfinite(ds)) or not np.all(ds > 0) or not np.all(np.isfinite(intensity)):
        raise ValueError("Calculated reflections must be finite with positive d values.")
    scorer = scorer or PreparedPeakScore(peaks, settings)
    # Reciprocal d order is invariant under positive isotropic scaling. Equal
    # transformed positions are handled by the prepared scorer's tie sort.
    order = np.argsort(-ds, kind="stable")
    ds, intensity = ds[order], intensity[order]
    def objective(scale):
        check_stop(stop)
        argument = settings.wavelength / (2 * ds * scale)
        valid = (argument > 0) & (argument < 1) & (intensity > 0)
        positions = np.rad2deg(2 * np.arcsin(argument[valid]))
        score = scorer.score(positions, intensity[valid])
        return -(score - 200 * abs(scale - 1))
    grid = np.linspace(1 - limit, 1 + limit, 21)
    values = [objective(float(scale)) for scale in grid]
    best = int(np.argmin(values))
    scale, value = float(grid[best]), values[best]
    lower, upper = float(grid[max(0, best - 1)]), float(grid[min(20, best + 1)])
    fitted = cast(Any, minimize_scalar(objective, bounds=(lower, upper), method="bounded", options={"xatol": 1e-6, "maxiter": 25}))
    if fitted.fun < value:
        scale = float(fitted.x)
    return scale, 200 * abs(scale - 1)


def annotate_results(results):
    """Keep every identity; mark only conservative possible-equivalence groups."""
    from pymatgen.core import Composition
    ranks, groups = {}, []
    for result in results:
        card = result["candidate"]
        source = card["source"]
        ranks[source] = ranks.get(source, 0) + 1
        result["source_rank"] = ranks[source]
        result["possible_group"] = ""
        if not card.get("space_group_number") or len(card.get("lattice", [])) != 6:
            continue
        try:
            formula = Composition(card["formula"]).reduced_formula
        except ValueError:
            continue
        cell = np.array(card["lattice"])
        for group in groups:
            if formula == group[0] and card["space_group_number"] == group[1] and np.allclose(cell[:3], group[2][:3], rtol=.02) and np.allclose(cell[3:], group[2][3:], atol=.5):
                result["possible_group"] = group[3]
                group[4]["possible_group"] = group[3]
                break
        else:
            groups.append((formula, card["space_group_number"], cell, "G" + str(len(groups) + 1), result))
