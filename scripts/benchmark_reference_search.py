"""Offline accuracy/performance check against a separately registered QualX DB."""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
import tracemalloc
from pathlib import Path
from threading import Event

import numpy as np

from materials_data_studio.modules.xrd.phase_models import CalculationSettings, SearchQuery
from materials_data_studio.modules.xrd.phase_reference import ReferenceSearchEngine
from materials_data_studio.modules.xrd.phase_search import PhaseSearchService
from materials_data_studio.modules.xrd.phase_store import PhaseStore
from materials_data_studio.modules.xrd.qualx_database import (
    QualXCODInorganicBackend,
    ReferenceDatabaseManager,
)


def standards():
    from pymatgen.analysis.diffraction.xrd import XRDCalculator
    from pymatgen.core import Lattice, Structure
    face = np.array([[0, 0, 0], [0, .5, .5], [.5, 0, .5], [.5, .5, 0]])
    structures = {
        "Si": (Structure(Lattice.cubic(5.431), ["Si"] * 8, np.vstack((face, face + .25))), 227),
        "LaB6": (Structure.from_spacegroup(221, Lattice.cubic(4.15692), ["La", "B"], [[0, 0, 0], [.2, .5, .5]]), 221),
        "Al2O3": (Structure.from_spacegroup(167, Lattice.hexagonal(4.759, 12.991), ["Al", "O"], [[0, 0, .3522], [.306, 0, .25]]), 167),
        "ZnO": (Structure.from_spacegroup(186, Lattice.hexagonal(3.2495, 5.2069), ["Zn", "O"], [[1/3, 2/3, 0], [1/3, 2/3, .3826]]), 186),
        "CaF2": (Structure.from_spacegroup(225, Lattice.cubic(5.46258), ["Ca", "F"], [[0, 0, 0], [.25, .25, .25]]), 225),
    }
    result = {}
    for name, (structure, group) in structures.items():
        pattern = XRDCalculator(wavelength=1.540598).get_pattern(structure, two_theta_range=(10, 90))
        peaks = [{"position": float(x), "intensity": float(y), "included": True} for x, y in zip(pattern.x, pattern.y, strict=True) if y > .1]
        result[name] = (peaks, group)
    return result


def is_phase(result, name, group):
    from pymatgen.core import Composition
    candidate = result["candidate"]
    return candidate["space_group_number"] == group and Composition(candidate["formula"]).reduced_composition == Composition(name).reduced_composition


def process_peak_rss():
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    class Memory(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD), *[(name, ctypes.c_size_t) for name in
                    ("peak_working_set", "working_set", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]]
    counters = Memory()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = wintypes.HANDLE
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    if not query(process(), ctypes.byref(counters), counters.cb):
        raise OSError("Cannot measure process peak working set")
    return counters.peak_working_set


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    store = PhaseStore(arguments.store)
    service = PhaseSearchService(store)
    engine = ReferenceSearchEngine(service)
    info = ReferenceDatabaseManager(store).configuration()
    started = time.perf_counter()
    backend = QualXCODInorganicBackend(info)
    initialization = time.perf_counter() - started
    cases = standards()
    membership = {}
    for name, (peaks, group) in cases.items():
        found = backend.retrieve_candidates(peaks, SearchQuery(formula=name, space_group=str(group)), CalculationSettings(lower=10), Event(), 1)
        if not found:
            raise RuntimeError("Expected standard phase is absent from the registered database: " + name)
        membership[name] = {"example_cod_id": found[0].identity, "formula": found[0].formula, "space_group_number": group}
    backend.close()
    reports = []
    rng = np.random.default_rng(20261024)
    for name, (original, group) in cases.items():
        for mode in ("clean", "missing_noise_shift_orientation"):
            peaks = [dict(p) for p in original]
            shift = 0.
            if mode != "clean":
                peaks = sorted(peaks, key=lambda p: p["intensity"], reverse=True)[:max(3, int(len(peaks) * .75))]
                shift = .04
                for p in peaks:
                    p["position"] += shift + rng.normal(0, .008)
                    p["intensity"] *= float(rng.uniform(.6, 1.4))
                peaks.append({"position": 41.234, "intensity": 2., "included": True})
            candidates, results, errors = engine.search(peaks, SearchQuery(), CalculationSettings(lower=10, zero_shift=shift),
                                                        cod=True, user=False, candidate_limit=1500, result_limit=100)
            rank = next((i for i, result in enumerate(results, 1) if is_phase(result, name, group)), None)
            reports.append({"phase": name, "mode": mode, "rank": rank, "top1": rank == 1, "top10": rank is not None and rank <= 10,
                            "top50": rank is not None and rank <= 50, "candidates": len(candidates), "metrics": dict(engine.last_metrics), "errors": errors[:10],
                            "top_ids": [{"id": r["candidate"]["identity"], "formula": r["candidate"]["formula"], "space_group": r["candidate"]["space_group_number"], "score": r["match"]["score"]} for r in results[:3]]})
            print(name, mode, "rank", rank, "seconds", round(engine.last_metrics["total_seconds"], 3), flush=True)
    mixture = [*cases["Si"][0], *[{**p, "intensity": p["intensity"] * .6} for p in cases["CaF2"][0]]]
    _, results, errors = engine.search(mixture, SearchQuery(), CalculationSettings(lower=10), cod=True, user=False, candidate_limit=2000, result_limit=100)
    mix = {name: next((i for i, r in enumerate(results, 1) if is_phase(r, name, cases[name][1])), None) for name in ("Si", "CaF2")}
    reports.append({"mode": "Si_CaF2_mixture", "ranks": mix, "metrics": dict(engine.last_metrics), "errors": errors[:10]})
    performance = []
    for allowed in ("", "Ca F O", "Ca F O Na Cl", "Ca F O Na Cl Si Al Mg", "Ca F O Na Cl Si Al Mg Ti C"):
        started = time.perf_counter()
        _, results, errors = engine.search(cases["CaF2"][0], SearchQuery(allowed=allowed), CalculationSettings(lower=10), cod=True, user=False)
        performance.append({"element_count": len(allowed.split()), "seconds": time.perf_counter() - started,
                            "metrics": dict(engine.last_metrics), "top_formula": results[0]["candidate"]["formula"] if results else None, "errors": errors[:10]})
        print("elements", len(allowed.split()), "seconds", round(performance[-1]["seconds"], 3), flush=True)
    tracemalloc.start()
    engine.search(cases["CaF2"][0], SearchQuery(), CalculationSettings(lower=10), cod=True, user=False)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    outcome = {"database": info, "backend_open_seconds": initialization, "source": "Independent generated prototype structures, conventional cell parameters; no measured research data",
               "membership_verified_before_evaluation": membership, "phase_success_criterion": "Reduced formula and space-group number; equivalent COD models retain separate IDs",
               "accuracy": reports, "performance": performance, "incremental_traced_peak_bytes": peak, "process_peak_working_set_bytes": process_peak_rss(),
               "score": "MDS Match Score; no claim of QualX FOM/ranking equivalence"}
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(outcome, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
