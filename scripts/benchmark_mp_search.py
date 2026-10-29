"""Independent lattice prototypes vs real MP (not measured-data accuracy)."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from benchmark_reference_search import is_phase, process_peak_rss, standards
from pymatgen.analysis.diffraction.xrd import XRDCalculator
from pymatgen.core import Lattice, Structure

from materials_data_studio.modules.xrd.mp_database import MPDatabaseManager
from materials_data_studio.modules.xrd.phase_models import CalculationSettings, SearchQuery
from materials_data_studio.modules.xrd.phase_reference import ReferenceSearchEngine
from materials_data_studio.modules.xrd.phase_search import PhaseSearchService
from materials_data_studio.modules.xrd.phase_store import PhaseStore
from materials_data_studio.modules.xrd.qualx_database import ReferenceDatabaseManager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mp-index", type=Path, required=True)
    parser.add_argument("--cod-db", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    store = PhaseStore(args.store)
    mp_info = MPDatabaseManager(store).register(args.mp_index)
    ReferenceDatabaseManager(store).register(args.cod_db)
    engine = ReferenceSearchEngine(PhaseSearchService(store))
    cases = {k: v for k, v in standards().items() if k in {"Si", "ZnO"}}
    more = {
        "TiO2": (Structure.from_spacegroup(136, Lattice.tetragonal(4.5937, 2.9587), ["Ti", "O"], [[0, 0, 0], [.305, .305, 0]]), 136),
        "Fe2O3": (Structure.from_spacegroup(167, Lattice.hexagonal(5.0356, 13.7489), ["Fe", "O"], [[0, 0, .3553], [.3059, 0, .25]]), 167),
        "BaTiO3": (Structure.from_spacegroup(99, Lattice.tetragonal(3.994, 4.038), ["Ba", "Ti", "O", "O"], [[0, 0, 0], [.5, .5, .522], [.5, .5, .013], [.5, 0, .488]]), 99),
    }
    for name, (structure, group) in more.items():
        pattern = XRDCalculator(wavelength=1.540598).get_pattern(structure, two_theta_range=(10, 90))
        cases[name] = ([{"position": float(x), "intensity": float(y), "included": True} for x, y in zip(pattern.x, pattern.y, strict=True) if y > .1], group)
    records = []
    for mode in ("mp_only", "combined_cod_mp"):
        for name, (peaks, group) in cases.items():
            started = time.perf_counter()
            _, results, errors = engine.search(peaks, SearchQuery(), CalculationSettings(lower=10), cod=mode != "mp_only", user=False, mp=True, candidate_limit=1000, result_limit=100)
            rank = next((i for i, r in enumerate(results, 1) if is_phase(r, name, group)), None)
            source_ranks = {source: next((r.get("source_rank") for r in results if r["candidate"]["source"] == source and is_phase(r, name, group)), None) for source in ("COD", "Materials Project")}
            row = {"mode": mode, "phase": name, "rank": rank, "source_ranks": source_ranks, "seconds": time.perf_counter() - started,
                   "metrics": dict(engine.last_metrics), "errors": errors[:5], "top5": [{"id": r["candidate"]["identity"], "source": r["candidate"]["source"], "formula": r["candidate"]["formula"], "sg": r["candidate"]["space_group_number"], "score": r["match"]["score"], "scale": r.get("lattice_scale", 1)} for r in results[:5]]}
            records.append(row)
            print(mode, name, rank, round(row["seconds"], 3), flush=True)
    mix = cases["Si"][0] + [{**p, "intensity": p["intensity"] * .6} for p in cases["ZnO"][0]]
    _, results, errors = engine.search(mix, SearchQuery(), CalculationSettings(lower=10), cod=True, user=False, mp=True, result_limit=100)
    mixture = {name: next((i for i, r in enumerate(results, 1) if is_phase(r, name, cases[name][1])), None) for name in ("Si", "ZnO")}
    summary = {}
    for mode in ("mp_only", "combined_cod_mp"):
        rows = [r for r in records if r["mode"] == mode]
        summary[mode] = {"top1": sum(r["rank"] == 1 for r in rows) / len(rows),
                         "top10": sum(r["rank"] is not None and r["rank"] <= 10 for r in rows) / len(rows),
                         "top50": sum(r["rank"] is not None and r["rank"] <= 50 for r in rows) / len(rows),
                         "median_seconds": statistics.median(r["seconds"] for r in rows)}
    args.report.write_text(json.dumps({"kind": "Independent crystallographic lattice prototypes, not measured experimental profiles", "mp_snapshot": mp_info["snapshot"], "mp_count": mp_info["phase_count"], "mp_hash": mp_info["content_hash"], "cases": records, "summary": summary,
                                      "mixture": mixture, "mixture_errors": errors[:5], "process_peak_rss_bytes": process_peak_rss()}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
