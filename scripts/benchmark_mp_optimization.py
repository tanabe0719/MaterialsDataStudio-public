"""Compare exact retained MP results with a local baseline Git ref and benchmark shared lists."""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import numpy as np
from benchmark_reference_search import standards

from materials_data_studio.modules.xrd.mp_database import MPDatabaseManager
from materials_data_studio.modules.xrd.phase_models import CalculationSettings, SearchQuery
from materials_data_studio.modules.xrd.phase_reference import ReferenceSearchEngine
from materials_data_studio.modules.xrd.phase_search import PhaseSearchService
from materials_data_studio.modules.xrd.phase_store import PhaseStore


def baseline_source(ref, file):
    return subprocess.check_output(["git", "show", ref + ":" + file], encoding="utf-8")


def baseline_module(ref, filename):
    module = types.ModuleType("baseline_" + Path(filename).stem)
    sys.modules[module.__name__] = module
    exec(compile(baseline_source(ref, filename), filename, "exec"), module.__dict__)
    return module


def list_benchmark(ref):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from materials_data_studio.core.models import Dataset, ModuleType
    from materials_data_studio.core.project_state import ProjectState
    from materials_data_studio.ui.dataset_list import DatasetList
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    state = ProjectState([Dataset(f"Generated profile {i}", ModuleType.XRD, {"x": np.arange(4.), "intensity": np.ones(4)}, metadata={"tags": ["generated"]}) for i in range(2000)])
    widget = DatasetList(state, ModuleType.XRD)
    source = ast.parse(baseline_source(ref, "src/materials_data_studio/ui/dataset_list.py"))
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "DatasetList")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in {"_filter", "refresh_diagnostics"}]
    scope = {"Qt": Qt}
    exec(compile(ast.Module(body=methods, type_ignores=[]), "baseline_dataset_list", "exec"), scope)
    result = {}
    for method in ("_filter", "refresh_diagnostics"):
        measurements = {}
        for mode in ("baseline", "current"):
            values = []
            for _ in range(3):
                started = time.perf_counter()
                scope[method](widget) if mode == "baseline" else getattr(widget, method)()
                values.append(time.perf_counter() - started)
            measurements[mode] = values
        result[method] = measurements
    widget.close()
    app.processEvents()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True, help="Existing scratch PhaseStore referencing the original verified MP database")
    parser.add_argument("--new-index", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", default="v0.14.0")
    args = parser.parse_args()
    old_database = baseline_module(args.baseline, "src/materials_data_studio/modules/xrd/mp_database.py")
    old_reference = baseline_module(args.baseline, "src/materials_data_studio/modules/xrd/phase_reference.py")
    old_reference.MaterialsProjectBackend = old_database.MaterialsProjectBackend
    old_reference.MPDatabaseManager = old_database.MPDatabaseManager
    service = PhaseSearchService(PhaseStore(args.store))
    old_engine, new_engine = old_reference.ReferenceSearchEngine(service), ReferenceSearchEngine(service)
    cases = standards()
    results = []
    for name, query in (("Si", SearchQuery()), ("ZnO", SearchQuery(formula="ZnO"))):
        reference = None
        for mode in ("baseline", "current_first", "current_warm"):
            engine = old_engine if mode == "baseline" else new_engine
            started = time.perf_counter()
            candidates, rows, errors = engine.search(cases[name][0], query, CalculationSettings(lower=10), cod=False, user=False, mp=True)
            elapsed = time.perf_counter() - started
            current = ([c.to_dict() for c in candidates], rows, errors)
            if reference is None:
                reference = current
            if current != reference:
                raise AssertionError(f"Retained results changed for {name}/{mode}")
            result = {"case": name, "mode": mode, "seconds": elapsed, "metrics": engine.last_metrics, "exact_result_equal": True}
            results.append(result)
            print(result, flush=True)
    # Register/validate the entire converted DB in its own store, leaving user registration untouched.
    converted_store = PhaseStore(args.store / "converted")
    converted_info = MPDatabaseManager(converted_store).register(args.new_index, report=lambda s: print(s, flush=True))
    converted = ReferenceSearchEngine(PhaseSearchService(converted_store))
    for name, query in (("Si", SearchQuery()), ("ZnO", SearchQuery(formula="ZnO"))):
        started = time.perf_counter()
        _, rows, errors = converted.search(cases[name][0], query, CalculationSettings(lower=10), cod=False, user=False, mp=True)
        results.append({"case": name, "mode": "converted", "seconds": time.perf_counter() - started, "metrics": converted.last_metrics,
                        "errors": errors, "top5": [{"id": r["candidate"]["identity"], "score": r["match"]["score"], "scale": r["lattice_scale"]} for r in rows[:5]]})
    report = {"kind": "Independent synthetic crystallographic standards; local sequential timing, not experimental accuracy",
              "baseline_ref": args.baseline, "cases": results, "converted_count": converted_info["phase_count"],
              "converted_hash": converted_info["content_hash"], "dataset_lists_2000": list_benchmark(args.baseline)}
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
