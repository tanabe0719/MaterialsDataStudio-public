"""MDS-specific replay commands for labelled statistical/signal reports."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import numpy as np

from materials_data_studio.tools.graph_signal_analysis import process
from materials_data_studio.tools.graph_statistics import real_array, statistics
from materials_data_studio.tools.graph_waves import make_wave
from materials_data_studio.tools.pxp_model import PxpTable


def execute_analysis(engine: Any, command: Any, flag: Any, number: Any, whole: Any) -> None:
    s = engine.services
    waves = [s.wave(engine.text(a)) for a in command.arguments]
    kind, method = flag("KIND", ""), whole("METH", 0)
    if kind == "average" and method != 0:
        raise ValueError("Waves Average supports only pointwise mean / SD / SEM")
    destination = s.path(flag("DEST", "W_"+kind))
    sources = {w.igor_path for w in waves}
    targets = [destination+suffix for suffix in ("", "_sd", "_sem")] if kind == "average" else [destination]
    if sources & set(targets):
        raise ValueError("Analysis output must not overwrite an input wave")
    if command.name == "mdsprocess":
        if any(f.name == "NODC" for f in command.flags) and kind != "correlate":
            raise ValueError("Remove-DC option applies only to correlation")
        if kind in {"poly2d", "gauss2d"}:
            if not 0 <= method < (1 if kind == "gauss2d" else 4):
                raise ValueError("Unsupported surface fit method")
            if len(waves) != 1:
                raise ValueError("Select exactly one image matrix")
            destinations={destination,destination+"_residual",destination+"_coef"}
            if destinations & sources:
                raise ValueError("Surface output cannot overwrite input")
            from materials_data_studio.tools.graph_surface_fit import surface_fit
            s.write(surface_fit(waves[0],destination,gaussian=kind=="gauss2d",degree=method+1),overwrite=True)
            return
        result = process(kind, waves, destination, method=method, count=whole("N", 201), remove_dc=any(f.name == "NODC" for f in command.flags))
        s.write([result], overwrite=True)
        engine.outputs.append(f"{result.igor_path}: {result.shape}")
        return
    if kind == "average":
        if len(waves) < 2 or any(w.shape != waves[0].shape or w.dimension_scaling != waves[0].dimension_scaling for w in waves) or sum(w.data.size for w in waves) > 10_000_000:
            raise ValueError("Average requires >=2 equal-shaped, equally scaled waves (<=10 million total values)")
        arrays = np.array([real_array(w.data) for w in waves])
        finite = np.isfinite(arrays)
        count = finite.sum(axis=0)
        total = np.where(finite, arrays, 0).sum(axis=0)
        mean = np.divide(total, count, out=np.full_like(total, np.nan), where=count > 0)
        variance = np.divide(np.where(finite, (arrays-mean)**2, 0).sum(axis=0), count-1,
                             out=np.full_like(total, np.nan), where=count > 1)
        sd = np.sqrt(variance)
        sem = np.divide(sd, np.sqrt(count), out=np.full_like(total, np.nan), where=count > 1)
        results = [make_wave(name, data, waves[0]) for name, data in zip(targets, (mean, sd, sem), strict=True)]
    else:
        report = statistics(kind, [w.data for w in waves], method=method, alpha=number("ALPH", .05), tail=whole("TAIL", 4), repeats=whole("N", 2000), seed=whole("SEED", 0))
        from materials_data_studio.tools.igor_wave_ops import set_dimension_label
        result = make_wave(destination, list(report.values()))
        for index, label in enumerate(report):
            result = set_dimension_label(result, 0, index, label)
        note = json.dumps({"analysis": kind, "method": method, "inputs": [w.igor_path for w in waves], "alpha": number("ALPH", .05), "tail": whole("TAIL", 4)}, sort_keys=True)
        results = [replace(result, wave_note=note)]
        engine.outputs.append(str(report))
    s.write(results, overwrite=True)
    s.set_results({"V_flag": 0})
    if any(f.name == "T" for f in command.flags):
        table = destination.rsplit(":", 1)[-1]+"Table"
        state = s.document.current
        state.tables = (*(t for t in s.document.render_source().table_definitions if t.name != table), PxpTable(table, tuple(targets)))
