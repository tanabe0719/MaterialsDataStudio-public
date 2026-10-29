"""Synthetic, platform-tolerant visual snapshots and bounded performance gates."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PIL import Image

from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.core.project_state import ProjectState
from materials_data_studio.core.session import load_session, save_session
from materials_data_studio.tools.graph_document import EditableGraphDocument
from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer
from materials_data_studio.tools.graph_waves import make_wave
from materials_data_studio.tools.pxp_model import PxpDocument
from materials_data_studio.tools.pxp_recreation import parse_recreation

ROOT = Path(__file__).resolve().parents[1]


def render_case(case: str, dpi: int = 100):
    x = np.linspace(0, 10, 101)
    waves = tuple(make_wave(name, values) for name, values in (("x", x), ("y", np.sin(x)), ("z", np.cos(x)*100), ("err", np.full(101, .1)), ("image", np.outer(np.linspace(0, 1, 31), np.linspace(1, 0, 21)))))
    commands = {
        "axes_annotation": 'Display y vs x\nModifyGraph rgb(y)=(16000,24000,52000),tick=2,mirror=1,minor=1,axThick=1.5\nErrorBars y Y,wave=(err,err)\nTextBox/N=note/F2/A=LT/X=5/Y=5 "First line\\rSecond line"',
        "multiple_axes": 'Display y vs x\nAppendToGraph/R z vs x\nLabel right "Secondary"\nModifyGraph tick=2,mirror=1,axThick=1.5',
        "image": 'Display\nAppendImage image\nModifyImage image ctab={0,1,Viridis,0}\nColorScale/C/N=scale image=image',
        "marker_fill": 'Display y vs x\nModifyGraph mode(y)=4,marker(y)=8,opaque(y)=1,msize(y)=4,rgb(y)=(50000,10000,10000),mrkFillRGB(y)=(0,50000,0),useMrkStrokeRGB(y)=1,mrkStrokeRGB(y)=(0,0,0),mrkThick(y)=1.5',
    }
    source_text = "Window Synthetic() : Graph\n" + commands[case] + '\nModifyGraph font="DejaVu Sans",fSize=12,margin(left)=85,margin(bottom)=52,margin(top)=15,margin(right)=85\nLabel bottom "Position"\nLabel left "Signal"\nEndMacro'
    graphs, _issues = parse_recreation(source_text)
    document = PxpDocument("visual", "Synthetic.pxp", "", 0, "10", "synthetic", "1", waves, graphs=graphs, status="FULL")
    figure = Figure(figsize=(6, 4), dpi=dpi)
    canvas = FigureCanvasAgg(figure)
    IgorMatplotlibRenderer().render(figure, EditableGraphDocument.from_pxp(document), "Synthetic")
    canvas.draw()
    return Image.fromarray(np.asarray(canvas.buffer_rgba()).copy()).convert("RGB")


def visual_checks(update: bool = False):
    directory = ROOT / "tests" / "visual_baselines"
    if update:
        directory.mkdir(parents=True, exist_ok=True)
    report = {}
    for case in ("axes_annotation", "multiple_axes", "image", "marker_fill"):
        current = render_case(case)
        path = directory / f"{case}.png"
        if update:
            current.save(path)
        if not path.is_file():
            raise AssertionError("Missing reviewed synthetic baseline: " + path.name)
        reference = Image.open(path).convert("RGB")
        delta = np.abs(np.asarray(current, dtype=float)-np.asarray(reference, dtype=float))/255
        mean_error, changed = float(delta.mean()), float(np.any(delta > .15, axis=2).mean())
        if mean_error > .02 or changed > .06:
            raise AssertionError(f"Visual regression {case}: mean={mean_error:.4g}, changed={changed:.4g}")
        for dpi in (125, 150, 200):
            scaled = render_case(case, dpi)
            if scaled.size != (6*dpi, 4*dpi):
                raise AssertionError("DPI scaling lost physical figure dimensions")
        report[case] = {"mean_pixel_error": mean_error, "changed_fraction": changed}
    return report


def performance_checks(points: int = 1_000_000):
    tracemalloc.start()
    x = np.linspace(1, 100, points)
    state = ProjectState([Dataset("Synthetic million-point profile", ModuleType.XRD, {"x": x, "intensity": np.sin(x)+2})])
    with tempfile.TemporaryDirectory(prefix="mds-performance-") as work:
        path = Path(work) / "synthetic.mdss"
        start = time.perf_counter()
        save_session(state, path)
        save_seconds = time.perf_counter()-start
        start = time.perf_counter()
        restored = load_session(path)
        open_seconds = time.perf_counter()-start
        if len(restored.datasets[0].raw_data["x"]) != points:
            raise AssertionError("Large profile round trip lost samples")
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if max(save_seconds, open_seconds) > 120 or peak > 1024**3:
        raise AssertionError("Large-profile time/allocation safety regression")
    return {"points": points, "save_seconds": save_seconds, "open_seconds": open_seconds, "traced_peak_bytes": peak, "measurement": "tracemalloc allocations, not OS RSS"}


def startup_check():
    """Fresh-process source startup, not filesystem-cache-cold or frozen startup."""
    code = 'from PySide6.QtWidgets import QApplication; from materials_data_studio.ui.main_window import MainWindow; a=QApplication([]); w=MainWindow(); w.show(); a.processEvents(); w.undo_history.reset(clean=True); w.close()'
    with tempfile.TemporaryDirectory(prefix="mds-startup-probe-") as work:
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "MDS_SETTINGS_DIRECTORY": work, "MDS_RECOVERY_DIRECTORY": str(Path(work)/"recovery")}
        started = time.perf_counter()
        subprocess.run([sys.executable, "-c", code], check=True, timeout=60, env=env, capture_output=True)
        elapsed = time.perf_counter()-started
    return {"fresh_process_source_seconds": elapsed, "includes": "imports, initial XRD window and first event pass; not cold filesystem cache"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-baselines", action="store_true")
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    report = {"visual": visual_checks(arguments.update_baselines), "performance": performance_checks(), "startup": startup_check(), "python": sys.version.split()[0], "platform": sys.platform}
    payload = json.dumps(report, indent=2)
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    main()
