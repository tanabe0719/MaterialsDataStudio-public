"""Fresh-process synthetic startup/document/style probes; no user files required."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path


def fixtures(root: Path) -> None:
    import numpy as np

    from materials_data_studio.core.models import Dataset, ModuleType
    from materials_data_studio.core.project_state import ProjectState
    from materials_data_studio.core.session import save_session
    from materials_data_studio.tools.graph_document import (
        EditableGraphDocument,
        save_graph_document,
    )
    from materials_data_studio.tools.graph_waves import make_wave
    from materials_data_studio.tools.pxp_model import PxpDocument, PxpGraph, PxpTrace
    x = np.linspace(0, 100, 100000)
    datasets = [Dataset(f"Synthetic {i}", ModuleType.XRD, {"x": x, "intensity": np.sin(x+i)+2}) for i in range(6)]
    save_session(ProjectState(datasets), root / "synthetic.mdss")
    waves = (make_wave("x", x), *(make_wave(f"y{i}", np.sin(x+i)) for i in range(6)))
    graph = PxpGraph("Synthetic", traces=tuple(PxpTrace(f"y{i}", f"root:y{i}", "root:x") for i in range(6)))
    source = PxpDocument("synthetic", "Synthetic", "", 0, "", "MDS", "", waves=waves, graphs=(graph,), status="FULL")
    save_graph_document(root / "synthetic.mdsg", EditableGraphDocument.from_pxp(source))


def child(mode: str, root: Path) -> dict:
    started = time.perf_counter()
    from PySide6.QtWidgets import QApplication

    from materials_data_studio.core.startup_metrics import snapshot
    from materials_data_studio.ui.main_window import MainWindow
    app = QApplication([])
    app.setStyle("Fusion")
    window = MainWindow(defer_initial_module=mode != "empty")
    window.show()
    app.processEvents()
    first = time.perf_counter()-started
    samples = []
    if mode == "mdss":
        window.open_session(root / "synthetic.mdss")
    elif mode == "mdsg":
        window.tool_manager.open_graph(root / "synthetic.mdsg")
    app.processEvents()
    ready = time.perf_counter()-started
    from PySide6.QtCore import QTimer
    heartbeat_gaps = []
    last_heartbeat = [time.perf_counter()]
    def heartbeat():
        now = time.perf_counter()
        heartbeat_gaps.append(now - last_heartbeat[0])
        last_heartbeat[0] = now
    heartbeat_timer = QTimer()
    heartbeat_timer.setInterval(10)
    heartbeat_timer.timeout.connect(heartbeat)
    heartbeat_timer.start()
    if mode == "mdsg":
        from dataclasses import replace
        editor = window.tool_manager._last_graph
        for i in range(8):
            graph = editor.editor.document.current.graphs[0]
            now = time.perf_counter()
            editor.editor.edit_graph(replace(graph, traces=(replace(graph.traces[0], line_width=1.6+i*.1), *graph.traces[1:])), "Synthetic style probe")
            app.processEvents()
            samples.append(time.perf_counter()-now)
    heartbeat_timer.stop()
    data = {"mode": mode, "first_events_seconds": first, "document_ready_seconds": ready,
            "max_style_event_loop_gap_seconds": max(heartbeat_gaps, default=0.),
            "style_samples_seconds": samples, "style_p95_seconds": sorted(samples)[-1] if samples else None,
            "peak_detection_imported": "materials_data_studio.modules.xrd.phase_detection" in sys.modules,
            **snapshot()}
    window._recovery_timer.stop()
    for viewer in tuple(window.tool_manager._graph_windows):
        viewer._discard_approved = True
    window.state.modified = False
    window.undo_history.reset(clean=True)
    window.close()
    app.processEvents()
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", choices=("empty", "mdss", "mdsg"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    if arguments.child:
        print(json.dumps(child(arguments.child, arguments.root)), flush=True)
        return
    if not 1 <= arguments.repeat <= 20:
        raise ValueError("Use 1–20 repetitions")
    results = []
    work = Path.cwd().resolve() / ".test-tmp" / "startup-probes"
    work.mkdir(parents=True, exist_ok=True)
    root = work / uuid.uuid4().hex
    root.mkdir()
    try:
        fixtures(root)
        for iteration in range(arguments.repeat):
            for mode in ("empty", "mdss", "mdsg"):
                settings = root / f"settings-{iteration}-{mode}"
                env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "MDS_SETTINGS_DIRECTORY": str(settings),
                       "MDS_RECOVERY_DIRECTORY": str(settings / "recovery")}
                completed = subprocess.run([sys.executable, __file__, "--child", mode, "--root", str(root)], check=True,
                                           capture_output=True, text=True, encoding="utf-8", timeout=60, env=env)
                result = json.loads(completed.stdout.splitlines()[-1])
                results.append(result)
                # Structural gates catch the observed regression independently
                # of CPU speed. Time budgets are deliberately runner tolerant.
                if result["peak_detection_imported"]:
                    raise AssertionError("Document startup unexpectedly imported peak detection")
                if result["document_ready_seconds"] > {"empty": 15, "mdss": 20, "mdsg": 30}[mode]:
                    raise AssertionError(f"{mode} exceeded its startup budget")
                if result["style_p95_seconds"] is not None and result["style_p95_seconds"] > 2:
                    raise AssertionError("Graph property latency exceeded its budget")
    finally:
        if root.resolve().parent != work.resolve():
            raise RuntimeError("Unexpected benchmark cleanup path")
        shutil.rmtree(root)
    report = {"samples": results, "python": sys.version.split()[0], "platform": sys.platform,
              "medians_seconds": {mode: statistics.median(r["document_ready_seconds"] for r in results if r["mode"] == mode) for mode in ("empty", "mdss", "mdsg")},
              "scope": "Fresh Python processes and synthetic local files; OS cache is uncontrolled. Does not measure Explorer, frozen loader or external sync/antivirus delays."}
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
