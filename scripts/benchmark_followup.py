"""Synthetic cold/warm transfer, lazy-wave, history and numerical-pool probes."""
from __future__ import annotations

import argparse
import gc
import io
import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def measured(work, repeat=5):
    from materials_data_studio.core.startup_metrics import process_memory
    samples = []
    before = process_memory()
    for _ in range(repeat):
        start = time.perf_counter()
        work()
        samples.append(time.perf_counter() - start)
    return {"p50_seconds": statistics.median(samples), "p95_seconds": sorted(samples)[-1],
            "samples_seconds": samples, "memory_before": before, "memory_after": process_memory()}


def numerical(jobs):
    import numpy as np

    from materials_data_studio.modules.ppms.heat_capacity import _debye_integrals
    x = np.column_stack([np.ones(20000), np.linspace(0, 1, 20000)])
    y = x @ np.array([1., 2.])
    def work(_):
        for _repeat in range(20):
            result = np.linalg.lstsq(x, y, rcond=None)[0]
            np.testing.assert_allclose(result, [1., 2.], rtol=1e-12)
            assert np.isfinite(_debye_integrals(np.geomspace(.01, 100, 2000))).all()
    def run():
        with ThreadPoolExecutor(jobs) as pool:
            list(pool.map(work, range(4)))
    return measured(run, repeat=3)


def probes():
    import numpy as np

    from materials_data_studio.core.immutable_json import HistoryLog, prepare_revision
    from materials_data_studio.core.models import Dataset, ModuleType
    from materials_data_studio.core.wave_store import WaveStore
    from materials_data_studio.plotting.igor_export import (
        GraphExportState,
        add_checked_dataset_waves,
    )
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **{f"w{i}": np.arange(64.) for i in range(600)})
    payload = buffer.getvalue()
    def lazy():
        store = WaveStore(payload)
        for i in range(600):
            np.testing.assert_array_equal(store.load(f"w{i}"), np.arange(64.))
    x = np.arange(100000.)
    datasets = [Dataset("Synthetic", ModuleType.MPMS, {"temperature_k": x, "field_oe": np.full(len(x), 100.), "moment_emu": x},
                        metadata={"sample_weight_mg": 10., "molar_mass_g_mol": 20.}) for _ in range(10)]
    def transfer():
        state = GraphExportState("MPMS", "Synthetic", "T", "M", [])
        add_checked_dataset_waves(state, datasets)
        assert len(state.waves) == 50
    history = HistoryLog([], {"title": "start"})
    def histories():
        current = history
        for i in range(10000):
            current = HistoryLog(current, {"title": str(i)})
        assert len(json.loads(json.dumps(current))) == 10001
    revision = prepare_revision({"results": [{"positions": list(range(1000))} for _ in range(100)]})
    results = {"lazy_600_waves": measured(lazy), "transfer_10x100000": measured(transfer),
               "history_10000": measured(histories), "warm_revision": measured(lambda: prepare_revision(revision))}
    gc.collect()
    from materials_data_studio.core.startup_metrics import process_memory
    results["after_collect"] = process_memory()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--thread-matrix", action="store_true")
    parser.add_argument("--numeric-child", type=int)
    args = parser.parse_args()
    if args.numeric_child:
        print(json.dumps(numerical(args.numeric_child)))
        return
    results = {"scope": "Synthetic source probes; no EXE, Explorer, real DB or user documents. P95 is the observed maximum of five samples; numerical pools use three. No thread setting is changed in the installed app.",
               "python": sys.version, "probes": probes()}
    if args.thread_matrix:
        matrix = []
        for threads in (1, 2, 4):
            for jobs in (1, 2):
                env = {**os.environ, **{name: str(threads) for name in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS")}}
                result = subprocess.run([sys.executable, __file__, "--numeric-child", str(jobs)], env=env,
                                        check=True, capture_output=True, text=True, timeout=120)
                matrix.append({"threads": threads, "jobs": jobs, **json.loads(result.stdout)})
        results["numerical_pool_matrix"] = matrix
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
