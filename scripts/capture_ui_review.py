"""Render real MDS widgets with synthetic inputs for development review.

Use --source to compare the archived previous implementation. This is a Qt
test harness, not automation of another application's Windows controls.
"""
from __future__ import annotations

# ruff: noqa: E402 -- source and display scale must be selected before Qt imports.
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, default=ROOT / "src")
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--scale", default="1")
parser.add_argument("--hold", action="store_true")
parser.add_argument("--width", type=int, default=1440)
parser.add_argument("--height", type=int, default=900)
parser.add_argument("--language", choices=("en", "ja"), default="en")
args = parser.parse_args()
sys.path.insert(0, str(args.source.resolve()))
args.output.mkdir(parents=True, exist_ok=True)
os.environ["QT_SCALE_FACTOR"] = args.scale
os.environ["MDS_SETTINGS_DIRECTORY"] = str(args.output / "settings")
os.environ["MDS_RECOVERY_DIRECTORY"] = str(args.output / "recovery")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

from materials_data_studio.core.measurement_branches import apply_branches
from materials_data_studio.core.models import Dataset, ModuleType
from materials_data_studio.modules.mpms.parser import parse_mpms_dc
from materials_data_studio.modules.ppms.generic import parse_generic_ppms_text
from materials_data_studio.modules.ppms.heat_capacity_parser import parse_heat_capacity_text
from materials_data_studio.modules.ppms.resistivity_parser import parse_resistivity_text
from materials_data_studio.modules.xrd.parser import parse_xrd_text
from materials_data_studio.modules.xrd.peak_fitting.fitting_window import XrdPeakFittingWindow
from materials_data_studio.modules.xrd.phase_store import PhaseStore
from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
from materials_data_studio.plotting.appearance import active_plot_appearance
from materials_data_studio.tools.graph_workflows import from_plot_spec
from materials_data_studio.tools.windows import MolarMassWindow
from materials_data_studio.ui.branch_editor import BranchEditor
from materials_data_studio.ui.localization import set_language
from materials_data_studio.ui.main_window import MainWindow
from materials_data_studio.ui.preferences import PreferencesDialog

app = QApplication([])
from manual_fonts import configure_capture_fonts

configure_capture_fonts(app)
app.setStyle("Fusion")
set_language(args.language)
window = MainWindow()
window.setWindowTitle("MDS UI review - synthetic data")
window.resize(args.width, args.height)
window.show()
demo = ROOT / "docs/demo_data"
xrd = window._ensure_module_widget(0)
ppms = window._ensure_module_widget(2)
mpms = window._ensure_module_widget(1)
mpms._finish_import(demo / "mpms_mt_demo.dc.dat", parse_mpms_dc(demo / "mpms_mt_demo.dc.dat"))
parsed = parse_xrd_text(demo / "xrd_observed.xy")
observed = Dataset("Synthetic diffraction", ModuleType.XRD, {"x": parsed.x, "intensity": parsed.intensity})
window.state.add_dataset(observed)
xrd.refresh()
for widget, filename, parse in (
    (ppms.heat_capacity, "ppms_heat_capacity_demo.dat", parse_heat_capacity_text),
    (ppms.resistivity, "ppms_resistivity_demo.dat", parse_resistivity_text),
    (ppms.generic, "ppms_custom_demo.dat", parse_generic_ppms_text),
):
    path = demo / filename
    widget._add_result(path, parse(path.read_text()))

def settle():
    loop = QEventLoop()
    QTimer.singleShot(150, loop.quit)
    loop.exec()

inventory = []
def capture(widget, name):
    widget.show()
    settle()
    image = widget.grab()
    image.save(str(args.output / (name + ".png")))
    inventory.append({"name": name, "logical_size": [widget.width(), widget.height()],
                      "pixels": [image.width(), image.height()], "scale": args.scale,
                      "visible_widgets": sum(child.isVisible() for child in widget.findChildren(QWidget)),
                      "tabs": [[tabs.tabText(i) for i in range(tabs.count())] for tabs in widget.findChildren(QTabWidget)]})

phase = PhaseSearchWindow(window.state, xrd, store=PhaseStore(args.output / "phase"), selected_dataset_id=observed.dataset_id)
graph = window.open_native_graph(from_plot_spec(xrd.build_plot_spec()))
graph.hide()
fitting = XrdPeakFittingWindow(window.state, apply_callback=lambda *_: None, parent=window)
calculator = MolarMassWindow(window)
for theme in ("light", "dark"):
    window.apply_theme(theme)
    window.module_tabs.setCurrentIndex(0)
    capture(window, "main_" + theme)
    window.module_tabs.setCurrentIndex(1)
    capture(window, "mpms_" + theme)
    settings = mpms.findChild(QTabWidget, "SettingsNavigation")
    settings.setCurrentIndex(2)
    capture(window, "mpms_analysis_" + theme)
    settings.setCurrentIndex(0)
    for index, name in enumerate(("heat_capacity", "resistivity", "custom_plot")):
        window.module_tabs.setCurrentIndex(2)
        ppms.tabs.setCurrentIndex(index)
        capture(window, name + "_" + theme)
        settings = ppms.tabs.widget(index).findChild(QTabWidget, "SettingsNavigation")
        settings.setCurrentIndex(2)
        capture(window, name + "_analysis_" + theme)
        settings.setCurrentIndex(0)
    if hasattr(phase, "apply_theme"):
        phase.apply_theme(theme)
    capture(phase, "search_match_" + theme)
    phase.hide()
    for widget, name in ((graph, "graph_editor"), (fitting, "peak_fitting"), (calculator, "calculator")):
        if hasattr(widget, "apply_theme"):
            widget.apply_theme(theme)
        capture(widget, name + "_" + theme)
        widget.hide()
    preferences = PreferencesDialog(theme, active_plot_appearance(), window)
    capture(preferences, "preferences_" + theme)
    preferences.hide()
root = ppms.generic.dataset_list.datasets()[0]
if hasattr(ppms.generic.dataset_list, "edit_branches"):
    rows = len(next(iter(root.raw_data.values())))
    apply_branches(window.state, root, [{"id": "synthetic-branch", "name": "Selected rows", "start": 0, "stop": min(rows-1, 29), "mode": "generic"}])
    ppms.generic.refresh()
    editor = BranchEditor(window.state, root, window)
    capture(editor, "custom_branches_dark")
    editor.hide()
(args.output / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
if args.hold:
    window.module_tabs.setCurrentIndex(0)
    window.show()
    app.exec()
else:
    phase.hide()
    window.hide()
    print(f"Captured {len(inventory)} synthetic UI views at Qt scale {args.scale}")
