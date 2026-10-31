"""Frozen-build dependency smoke test invoked only by the release pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import TextIO

_fault_stream: TextIO | None = None

_SMOKE_CIF = """data_NaCl
_symmetry_space_group_name_H-M 'F m -3 m'
_cell_length_a 5.6402
_cell_length_b 5.6402
_cell_length_c 5.6402
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
Na1 Na 0 0 0 1
Cl1 Cl 0.5 0.5 0.5 1
"""


def write_reference_smoke_database(root: Path, ds, intensities) -> Path:
    """Generate a tiny independent fixture using the inspected QualX SQL format."""
    import sqlite3

    from materials_data_studio.modules.xrd.reference_index import DatabaseConnection
    root.mkdir(parents=True, exist_ok=True)
    path = root / "generated.sq"
    with sqlite3.connect(path, factory=DatabaseConnection) as connection:
        connection.executescript("""CREATE TABLE infodb(date TEXT,ncard INTEGER,type TEXT);
          INSERT INTO infodb VALUES('2026-07-02',1,'COD');
          CREATE TABLE id(id INTEGER PRIMARY KEY,chemical_formula TEXT,spacegroup TEXT,name TEXT,mineralname TEXT,nd INTEGER,dvalue TEXT,intensita TEXT);
          CREATE TABLE chemical(id INTEGER,chemical_element TEXT,PRIMARY KEY(id,chemical_element));
          INSERT INTO chemical VALUES(1000000,'Na'),(1000000,'Cl');""")
        connection.execute("INSERT INTO id VALUES(1000000,'Na Cl','F m -3 m','Generated NaCl','',?,?,?)",
                           (len(ds), ",".join(map(str, ds)), ",".join(map(str, intensities))))
    with sqlite3.connect(str(path) + ".info", factory=DatabaseConnection) as connection:
        connection.execute("CREATE TABLE info(id TEXT PRIMARY KEY,a REAL,b REAL,c REAL,alpha REAL,beta REAL,gamma REAL,volume REAL,type TEXT,h TEXT,k TEXT,l TEXT)")
        connection.execute("INSERT INTO info VALUES('1000000',5.6402,5.6402,5.6402,90,90,90,179.4,'Cubic','','','')")
    with sqlite3.connect(str(path) + ".infostat", factory=DatabaseConnection) as connection:
        connection.execute("CREATE TABLE stat_type(val TEXT,ids TEXT,n INTEGER)")
    with sqlite3.connect(str(path) + ".search", factory=DatabaseConnection) as connection:
        connection.execute("CREATE TABLE top(id INTEGER PRIMARY KEY,n TEXT,dval TEXT)")
        strongest = sorted(zip(intensities, ds, strict=True), reverse=True)[:3]
        connection.execute("INSERT INTO top VALUES(1000000,?,?)", (str(len(strongest)), ",".join(str(d) for _i, d in strongest)))
    return path


def run_packaging_smoke_test(report_path: Path) -> bool:
    """Exercise lazy runtime dependencies and write an auditable result."""
    fault_path = os.environ.get("MDS_SMOKE_FAULT_LOG", "")
    if fault_path:
        import faulthandler
        global _fault_stream
        # Keep the diagnostic descriptor alive through interpreter shutdown.
        _fault_stream = Path(fault_path).open("w", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(file=_fault_stream, all_threads=True)
    try:
        import h5py  # noqa: F401
        import igor2.packed  # noqa: F401
        import matplotlib  # noqa: F401
        import numpy as np
        import pandas  # noqa: F401
        import scipy.optimize  # noqa: F401
        from matplotlib.colors import to_rgb
        from matplotlib.figure import Figure

        from materials_data_studio.modules.mpms.widget import MpmsWidget  # noqa: F401
        from materials_data_studio.modules.ppms.widget import PpmsWidget  # noqa: F401
        from materials_data_studio.modules.xrd.cif_backend import CifDocument
        from materials_data_studio.modules.xrd.simulation import simulate_cif
        from materials_data_studio.plotting.igor_h5xp import IgorH5xpExporter  # noqa: F401
        from materials_data_studio.tools.command_engine import CommandEngine
        from materials_data_studio.tools.command_services import DocumentServices
        from materials_data_studio.tools.graph_calculation import calculate_wave
        from materials_data_studio.tools.graph_document import (
            EditableGraphDocument,
            load_graph_document,
            save_graph_document,
        )
        from materials_data_studio.tools.graph_export import GraphicsSettings, save_graphics
        from materials_data_studio.tools.graph_h5xp import load_h5xp, save_h5xp
        from materials_data_studio.tools.graph_render import IgorMatplotlibRenderer
        from materials_data_studio.tools.graph_tables import TableModel
        from materials_data_studio.tools.graph_window import GraphEditorWindow
        from materials_data_studio.tools.igor_input_resources import write_input_resources
        from materials_data_studio.tools.igor_procedure_conversion import (
            analyze_procedures,
            build_python,
        )
        from materials_data_studio.tools.procedure_interaction import ProcedureInteractionSession
        from materials_data_studio.tools.pxp_backend import Igor2Backend  # noqa: F401
        from materials_data_studio.tools.pxp_model import PxpDocument, PxpTable, PxpWave
        from materials_data_studio.tools.pxp_recreation import parse_recreation
        from materials_data_studio.tools.pxp_window import PxpViewerWindow  # noqa: F401

        graphs, _tables = parse_recreation(
            'Window Graph0() : Graph\nDisplay y vs x\nAppendToGraph/R x vs x\n'
            'Label right "Second axis"\nTextBox/N=note "First"\nAppendText "Second"\n'
            'SetDrawEnv xcoord=prel,ycoord=prel\nDrawRect .1,.1,.3,.3\nEndMacro\n'
        )
        if len(graphs) != 1 or len(graphs[0].traces) != 2 or graphs[0].traces[1].axis != "right":
            raise RuntimeError("PXP window recreation smoke test failed.")
        source = b"Generated frozen-build smoke experiment"
        waves = []
        for name, values in (("x", np.arange(10.)), ("y", np.arange(10.)**2)):
            values.setflags(write=False)
            waves.append(PxpWave(name, f"root:{name}", name, values.dtype.str,
                                 values.shape, values, ((0., 1.),), ("",), "", ""))
        imported = PxpDocument("smoke", "smoke.pxp", hashlib.sha256(source).hexdigest(),
                               len(source), "10", "igor2", "0.5.13", waves=tuple(waves), graphs=graphs,
                               status="GRAPH_PARTIAL", source_bytes=source,
                               tables=("Table0",), table_definitions=(PxpTable("Table0", ("x", "y")),))
        with tempfile.TemporaryDirectory(prefix="mds-graph-smoke-") as temporary:
            graph_path = Path(temporary) / "smoke.mdsg"
            save_graph_document(graph_path, EditableGraphDocument.from_pxp(imported))
            restored = load_graph_document(graph_path)
            h5xp_path = Path(temporary) / "smoke.h5xp"
            save_h5xp(h5xp_path, restored)
            if load_h5xp(h5xp_path).current.graphs != restored.current.graphs:
                raise RuntimeError("Frozen H5XP round-trip failed")
            table = TableModel(restored, restored.render_source().table_definitions[0])
            if table.rowCount() != 10 or table.columnCount() != 2:
                raise RuntimeError("Frozen multi-wave table smoke test failed")
            if restored.current.graphs != graphs or restored.source.source_bytes != source:
                raise RuntimeError("MDSG storage smoke test failed")
            figure = Figure()
            IgorMatplotlibRenderer().render(figure, restored, graphs[0].name)
            if len(figure.axes) != 2 or figure.axes[1].yaxis.get_label_position() != "right":
                raise RuntimeError("Multi-axis graph smoke test failed")
            calculated = calculate_wave(restored, "difference", "y-x")
            if calculated.waves is None or not any(w.name == "difference" for w in calculated.waves):
                raise RuntimeError("Wave arithmetic smoke test failed")
            for extension in ("png", "svg", "pdf"):
                exported = Path(temporary) / f"graph.{extension}"
                figure.savefig(exported)
                if exported.stat().st_size < 500:
                    raise RuntimeError(f"Frozen {extension} graph export failed")
            for extension in ("eps", "bmp", "jpg", "tiff"):
                exported = Path(temporary) / f"graphics.{extension}"
                save_graphics(exported, restored, graphs[0].name, GraphicsSettings(extension))
                if exported.stat().st_size < 500:
                    raise RuntimeError(f"Frozen {extension} graphics export failed")
            command = CommandEngine(DocumentServices(restored))
            if not command.execute("Make/D/N=8 commandWave; commandWave=p; Display/N=CommandGraph commandWave").success:
                raise RuntimeError("Frozen Command service smoke test failed")
            from dataclasses import replace

            from PySide6.QtCore import QCoreApplication, QEvent, Qt
            from PySide6.QtWidgets import QApplication

            from materials_data_studio.tools.pxp_model import PxpDrawing
            application = QApplication.instance() or QApplication([])
            window = GraphEditorWindow()
            try:
                window.bind_graph_document(restored)
                assert window.editor is not None
                window._selection = ("graph", graphs[0].name)
                window._show_selection()
                window.show()
                application.processEvents()
                window.command_workspace.output.clear()
                window.apply_properties("trace", "y", {"line_width": 2})
                window.apply_properties("axis", "bottom", {
                    "axis_color": "#123456", "label_color": "#336699",
                    "major_tick_color": "#ff0000", "minor_tick_color": "#00ff00",
                    "tick_label_color": "#0000ff", "tick_enable_min": -np.inf,
                    "tick_enable_max": 4.,
                })
                window.canvas.draw()
                axis_document = Path(temporary) / "axis_styles.mdsg"
                save_graph_document(axis_document, window.editor.document)
                axis_restored = load_graph_document(axis_document)
                axis_style = next(a.style for a in axis_restored.graph(graphs[0].name).axes if a.name == "bottom")
                axis = window.figure.axes[0]
                if (axis_style.major_tick_color != (65535, 0, 0)
                        or axis_style.minor_tick_color != (0, 65535, 0)
                        or axis_style.tick_enable != (-np.inf, 4.)
                        or not np.allclose(to_rgb(axis.spines["bottom"].get_edgecolor()), to_rgb("#123456"))
                        or any(value > 4 for value in axis.xaxis.get_majorticklocs())
                        or not any(to_rgb(tick.tick1line.get_color()) == to_rgb("#ff0000")
                                   for tick in axis.xaxis.get_major_ticks())):
                    raise RuntimeError("Frozen axis color/tick range storage and rendering failed")
                current_graph = window.graph()
                assert current_graph is not None and window.editor is not None
                window.editor.edit_graph(replace(current_graph, drawings=(*current_graph.drawings, PxpDrawing("DrawLine", (.1, .2, .3, .4)))), "Synthetic drawing")
                output = window.command_workspace.output.toPlainText()
                if "ModifyGraph" not in output or "DrawLine/W=" not in output:
                    raise RuntimeError("Frozen GUI command display failed")
                # Actually destroy old Qt controls between selections. Without
                # DeferredDelete the dangling Properties references can look
                # valid until this smoke process exits and hide the regression.
                for selection in (("wave", waves[0].id), ("wave", waves[1].id), ("graph", graphs[0].name)):
                    window._selection = selection
                    window._show_selection()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    application.processEvents()
                    if selection[0] != "graph" and window.property_widgets:
                        raise RuntimeError("Frozen selection retained obsolete Properties controls")
                for dock in (window.command_workspace.console_dock, window.command_workspace.procedure_dock):
                    window.command_workspace.float_dock(dock)
                    dock.setWindowState(Qt.WindowState.WindowMinimized)
                    dock.hide()
                    window.command_workspace.dock_back(dock)
                    application.processEvents()
                    if (dock.isFloating() or not dock.isVisible() or dock.windowState() != Qt.WindowState.WindowNoState
                            or dock.widget().visibleRegion().isEmpty()
                            or window.dockWidgetArea(dock) == Qt.DockWidgetArea.NoDockWidgetArea):
                        raise RuntimeError("Frozen explicit docking failed")
                if any(action.icon().isNull() or action not in window.quick_toolbar.actions()
                       for action in (window.open_action, window.save_action, window.import_action)):
                    raise RuntimeError("Frozen Graph Editor toolbar failed")
                workspace = window.command_workspace
                workspace.output.clear()
                for n in range(3):
                    workspace.code.setPlainText(f"print({n})")
                    workspace.flush()
                if workspace.output.toPlainText().count("GUI Edit Python procedure") != 1 or "3 consecutive updates" not in workspace.output.toPlainText():
                    raise RuntimeError("Frozen Procedure display grouping failed")
                from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
                from materials_data_studio.tools.graph_workflows import from_plot_spec
                from materials_data_studio.ui.icons import toolbar_icon
                transfer = from_plot_spec(PlotSpec("Temperature (K)", "Signal", [CurveSpec(np.arange(3.), np.arange(3.), "Synthetic sample")]))
                if transfer.render_source().waves[0].name != "Synthetic_sample_Temperature_K_x" or toolbar_icon("edit_graph", "dark").isNull():
                    raise RuntimeError("Frozen native transfer/graph icon failed")
                for language in ("en", "ja"):
                    window.show_graph_manual(language)
                    manual = window.graph_manual_dialogs[language]
                    if "Load General Text" not in manual.browser.toPlainText() or "section-13" not in manual._section_positions:
                        raise RuntimeError("Frozen Graph Viewer manual missing or incomplete")
                    manual.close()
                window.python_help_action.trigger()
                help_dialog = window.command_workspace.procedure_help
                if (help_dialog.tabs.count() != 6 or "WaveHandle" not in help_dialog.browsers[2].toPlainText()
                        or "Convert Igor / IPF" not in help_dialog.browsers[3].toPlainText()):
                    raise RuntimeError("Frozen Procedure help failed")
                if window.send_to_igor_action not in window.quick_toolbar.actions() or window.send_to_igor_action.icon().isNull():
                    raise RuntimeError("Graph Editor Send to Igor action is missing")
            finally:
                window._discard_approved = True
                window.close()
            sandbox_root = Path(temporary) / "sandbox"
            (sandbox_root / "input").mkdir(parents=True)
            (sandbox_root / "output").mkdir()
            procedure_input = sandbox_root / "input/input.mdsg"
            procedure_source = sandbox_root / "input/procedure.py"
            procedure_output = sandbox_root / "output/output.mdsg"
            save_graph_document(procedure_input, restored)
            virtual_output = sandbox_root / "virtual-only.dat"
            write_input_resources(sandbox_root / "input/igor_inputs.json", [{
                "name": "synthetic_input.dat",
                "path": str(Path(temporary) / "not-mounted" / "synthetic_input.dat"),
                "text": "Generated instrument header\r\n[Data]\r\nPosition Signal\r\n0 10\r\n1 20\r\n2 30\r\n",
            }])
            converted = build_python(analyze_procedures(
                "Function FrozenScale(w)\nWave w\nw*=2\nEnd\n"
                "Function FrozenConverted()\n"
                "Make/D/N=5 convertedWave\nconvertedWave=p+0.5\nFrozenScale(convertedWave)\n"
                "Display/N=ConvertedGraph convertedWave\n"
                "ModifyGraph tick=2,mirror=1,lsize(convertedWave)=2\nFrozenInputs()\nEnd\n"
                "Function FrozenInputs()\n"
                "NewPath/O GlobalRoot\n"
                'LoadWave/G/D/N=prepared/P=GlobalRoot "synthetic_input.dat"\n'
                "Variable handle\n"
                f'Open handle as "{virtual_output.as_posix()}"\n'
                'fprintf handle, "Position Signal\\r\\n0 5\\r\\n1 6\\r\\n2 7\\r\\n"\n'
                "Close handle\n"
                f'LoadWave/G/D/N=virtual "{virtual_output.as_posix()}"\n'
                f'DeleteFile "{virtual_output.as_posix()}"\n'
                "Duplicate/O prepared1 preparedTwice\npreparedTwice*=2\n"
                "Display/N=PreparedGraph preparedTwice vs prepared0\nEnd\n"
            ), "FrozenConverted", [])
            procedure_source.write_text(
                'from mds import wave,graph,command\n'
                'wave.create("pythonWave",[1.,2.,3.])\n'
                'graph.new("PythonGraph").append("pythonWave")\n'
                'print("worker output OK")\n' + converted +
                'command("ModifyGraph/W=ConvertedGraph rgb(convertedWave)=(12000,24000,48000)")\n'
                'print("converted Igor and public command OK")\n', encoding="utf-8")
            from PySide6.QtCore import QProcess
            process = QProcess()
            worker_args = [] if getattr(sys, "frozen", False) else ["-m", "materials_data_studio"]
            process.start(sys.executable, [*worker_args, "--procedure-broker", str(sandbox_root)])
            if not process.waitForStarted(10000) or not process.waitForFinished(90000):
                process.kill()
                process.waitForFinished(1500)
                raise RuntimeError("Frozen Python worker did not finish")
            if process.exitCode() != 0 or not procedure_output.exists():
                diagnostics = bytes(process.readAllStandardOutput().data()).decode("utf-8", errors="replace")
                diagnostics += bytes(process.readAllStandardError().data()).decode("utf-8", errors="replace")
                log_path = sandbox_root / "output/procedure.log"
                if log_path.exists():
                    diagnostics += log_path.read_text(encoding="utf-8")
                raise RuntimeError(f"Frozen Python worker failed (exit {process.exitCode()}): {diagnostics}")
            executed = load_graph_document(procedure_output)
            if not executed.graph("PythonGraph").traces:
                raise RuntimeError("Frozen Python worker did not save canonical edits")
            converted_graph = executed.graph("ConvertedGraph")
            converted_wave = executed.render_source().wave("convertedWave")
            if (converted_wave is None or not np.array_equal(converted_wave.data, [1., 3., 5., 7., 9.])
                    or converted_graph.traces[0].line_width != 2
                    or converted_graph.style.axis_defaults.tick != 2
                    or converted_graph.style.axis_defaults.mirror != 1
                    or converted_graph.traces[0].color_igor != (12000, 24000, 48000)):
                raise RuntimeError("Frozen converted Igor/runtime/public mds.command execution failed")
            if "worker output OK" not in (sandbox_root / "output/procedure.log").read_text(encoding="utf-8"):
                raise RuntimeError("Frozen Python stdout was not captured")
            if "converted Igor and public command OK" not in (sandbox_root / "output/procedure.log").read_text(encoding="utf-8"):
                raise RuntimeError("Frozen converted Igor completion was not captured")
            prepared_wave = executed.render_source().wave("preparedTwice")
            virtual_wave = executed.render_source().wave("virtual1")
            if (prepared_wave is None or not np.array_equal(prepared_wave.data, [20., 40., 60.])
                    or virtual_wave is None or not np.array_equal(virtual_wave.data, [5., 6., 7.])
                    or not executed.graph("PreparedGraph").traces
                    or "igor_input_resources" in executed.current.environment
                    or "igor_input_resources" in load_graph_document(procedure_input).current.environment
                    or virtual_output.exists()):
                raise RuntimeError("Frozen prepared-sidecar/isolated virtual text I/O workflow failed")

            # An independent, explicitly interactive job exercises the actual
            # broker/worker IPC in the frozen executable. A scripted trusted
            # responder supplies generated values/text; no private samples or
            # host files are mounted in the isolated procedure.
            interactive_root = Path(temporary) / "interactive-sandbox"
            (interactive_root / "input").mkdir(parents=True)
            (interactive_root / "output").mkdir()
            interactive_input = interactive_root / "input/input.mdsg"
            interactive_output = interactive_root / "output/output.mdsg"
            save_graph_document(interactive_input, restored)
            (interactive_root / "input/procedure.json").write_text(
                json.dumps({"igor_interactive": True}), encoding="utf-8")
            interactive_code = build_python(analyze_procedures(
                "#pragma rtGlobals=1\nFunction FrozenInteractive()\n"
                "Variable weight=10,mode=1\nString caption=\"Generated\"\n"
                'Prompt weight,"Weight (mg)"\nPrompt caption,"Sample label"\n'
                'Prompt mode,"Mode",popup,"First;Second;"\n'
                'DoPrompt "Frozen macro inputs",weight,caption,mode\n'
                "Make/D/N=1 acceptedWeight,acceptedMode\nacceptedWeight=weight\nacceptedMode=mode\n"
                "Variable handle\n"
                'Open/D/R/F="PRF text:*.prf;"/M="Select a *.prf file" handle\n'
                "LoadWave/G/D/N=dialogData S_fileName\n"
                "Display/N=InteractiveGraph dialogData1 vs dialogData0\n"
                'Label left "Signal"\nLabel bottom "Position"\n'
                'ModifyGraph tick=2,mirror=1,fSize=12,font="DejaVu Sans"\n'
                "Make/D/N=3 legacyWave\nlegacyWave=p+1\n"
                "Variable legacyValue=legacyWave[numpnts(legacyWave)]\n"
                "Make/D/N=1 legacyLast\nlegacyLast=legacyValue\n"
                'DoAlert 0,"Frozen interactive macro finished"\n'
                'DoPrompt "Frozen cancellation",weight\n'
                "Make/D/N=1 cancelledFlag,preservedWeight\ncancelledFlag=V_flag\npreservedWeight=weight\n"
                "End\n"
            ), "FrozenInteractive", [])
            (interactive_root / "input/procedure.py").write_text(interactive_code, encoding="utf-8")
            selected_text = "Position Signal\r\n0 5\r\n1 7\r\n2 11\r\n"
            selected_path = str(Path(temporary) / "explicit-selection" / "synthetic.prf")
            session = ProcedureInteractionSession(interactive_root)
            process = QProcess()
            kinds: list[str] = []
            try:
                process.start(sys.executable, [*worker_args, "--procedure-broker", str(interactive_root)])
                if not process.waitForStarted(10000):
                    raise RuntimeError("Frozen interactive worker did not start")
                deadline = time.monotonic() + 90
                while not process.waitForFinished(50):
                    application.processEvents()
                    request = session.poll()
                    if request is not None:
                        kinds.append(request["kind"])
                        if request["kind"] == "file_open":
                            if request["payload"]["title"] != "Select a *.prf file":
                                raise RuntimeError("Frozen macro file-dialog title was not preserved")
                            payload = {"resources": [{"name": "synthetic.prf", "path": selected_path,
                                                       "text": selected_text}], "selected": selected_path}
                            session.respond(request, {"status": "ok", "payload": payload})
                        elif request["kind"] == "prompt":
                            if request["payload"]["title"] == "Frozen cancellation":
                                session.respond(request, {"status": "cancelled", "payload": {}})
                            else:
                                session.respond(request, {"status": "ok", "payload": {"values": {
                                    "weight": 12.5, "caption": "Generated accepted", "mode": 1}}})
                        elif request["kind"] == "message":
                            session.respond(request, {"status": "ok", "payload": {"button": 0}})
                        else:
                            raise RuntimeError("Unexpected frozen macro interaction")
                    if time.monotonic() > deadline:
                        raise RuntimeError("Frozen interactive worker exceeded its smoke timeout")
                if process.exitCode() != 0 or not interactive_output.exists():
                    log_path = interactive_root / "output/procedure.log"
                    diagnostics = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                    diagnostics += bytes(process.readAllStandardOutput().data()).decode("utf-8", errors="replace")
                    diagnostics += bytes(process.readAllStandardError().data()).decode("utf-8", errors="replace")
                    raise RuntimeError("Frozen interactive worker failed: " + diagnostics)
            finally:
                session.close()
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.kill()
                    process.waitForFinished(1500)
            interactive_result = load_graph_document(interactive_output)
            expected = {"acceptedWeight": [12.5], "acceptedMode": [2.],
                        "legacyLast": [3.], "cancelledFlag": [1.], "preservedWeight": [12.5]}
            for name, values in expected.items():
                wave = interactive_result.render_source().wave(name)
                if wave is None or not np.array_equal(wave.data, values):
                    raise RuntimeError("Frozen macro input/cancellation/rtGlobals result failed: " + name)
            if (kinds != ["prompt", "file_open", "message", "prompt"]
                    or not interactive_result.graph("InteractiveGraph").traces
                    or interactive_result.graph("InteractiveGraph").style.axis_defaults.font_size != 12
                    or "igor_input_resources" in interactive_result.current.environment
                    or "igor_interactive" in interactive_result.current.environment
                    or json.dumps(selected_text) in json.dumps(interactive_result.current.environment)
                    or "igor_interactive" in json.loads(interactive_output.with_suffix(".json").read_text(encoding="utf-8"))):
                raise RuntimeError("Frozen interactive macro capabilities/metadata/style checks failed")

        document = CifDocument(Path("packaging-smoke.cif"), _SMOKE_CIF, _SMOKE_CIF)
        pattern = simulate_cif(
            document,
            wavelength_a=1.540598,
            two_theta_range=(20.0, 80.0),
            fwhm_deg=0.15,
        )
        if pattern.profile_x is None or pattern.profile_y is None:
            raise RuntimeError("CIF simulation did not produce a broadened profile.")
        if pattern.occupancy_model is None:
            raise RuntimeError("CIF simulation did not create an occupancy preview cache.")
        from materials_data_studio.modules.xrd.phase_models import CalculationSettings, SearchQuery
        from materials_data_studio.modules.xrd.phase_search import PhaseSearchService
        from materials_data_studio.modules.xrd.phase_store import PhaseStore
        from materials_data_studio.modules.xrd.phase_window import PhaseSearchWindow
        with tempfile.TemporaryDirectory(prefix="mds-phase-smoke-") as temporary:
            root = Path(temporary)
            cif = root / "synthetic.cif"
            cif.write_text(_SMOKE_CIF, encoding="utf-8")
            store = PhaseStore(root / "library")
            candidates, errors = store.register([cif])
            if errors or not store.local_search(SearchQuery(required="Na Cl")):
                raise RuntimeError("Frozen local CIF registration/search failed")
            peaks = [{"position": float(x), "intensity": float(y), "included": True}
                     for x, y in zip(pattern.two_theta_deg, pattern.intensity, strict=True)]
            matches, errors = PhaseSearchService(store).calculate(candidates, peaks, CalculationSettings(lower=20, upper=80))
            if errors or not matches or matches[0]["match"]["score"] < 99.9:
                raise RuntimeError("Frozen phase search/match failed")
            from materials_data_studio.core.models import Dataset, ModuleType
            from materials_data_studio.core.project_state import ProjectState
            from materials_data_studio.core.session import load_session, save_session
            from materials_data_studio.modules.xrd.phase_search import experimental_signature
            from materials_data_studio.modules.xrd.widget import XrdWidget
            observed = Dataset("Synthetic phase-search smoke", ModuleType.XRD,
                               {"x": pattern.profile_x, "intensity": pattern.profile_y},
                               metadata={"source_axis": "two_theta", "wavelength_a": 1.540598})
            phase_state = ProjectState(datasets=[observed])
            xrd = XrdWidget(phase_state)
            search = PhaseSearchWindow(phase_state, xrd, store=store)
            search.form.radiation.setCurrentText("Cu Kα1")
            search.form.numeric["lower"].setValue(20)
            search.form.numeric["upper"].setValue(80)
            search.peaks, search.matched_peaks = peaks, peaks
            search._step1_ready = True
            search.matched_signature = experimental_signature(observed)
            search.candidates, search.results = candidates, matches
            search._candidate_rows()
            search.candidate_table.selectRow(0)
            from materials_data_studio.modules.xrd.phase_detection import (
                DetectionSettings,
                alpha2_position,
                analyze_peaks,
            )
            test_x = np.linspace(40, 90, 5001)
            test_y = 20 + .1 * test_x
            for position in (55., 82.):
                test_y += 100 * np.exp(-.5 * ((test_x - position) / .04) ** 2)
                test_y += 50 * np.exp(-.5 * ((test_x - alpha2_position(position)) / .04) ** 2)
            detection = analyze_peaks(test_x, test_y, DetectionSettings(doublet=True, peak_sigma=.04))
            if len(detection["peaks"]) != 2:
                raise RuntimeError("Frozen background-corrected Cu doublet detection failed")
            for _ in range(3):
                search.preview()
            plot_item = search.plot.getPlotItem()
            if plot_item is None or not any(item.name() == "Observed" and item.scene() is not None for item in plot_item.listDataItems()):
                raise RuntimeError("Frozen phase-search preview disappeared")
            search.add_overlay()
            deadline = time.monotonic() + 30
            while search._busy and time.monotonic() < deadline:
                application.processEvents()
                time.sleep(.01)
            if search._busy:
                search.cancel()
                raise RuntimeError("Frozen phase profile simulation timed out")
            phase_state.module_states["xrd"] = {"phase_search": search.serialize_state()}
            save_session(phase_state, root / "phase.mdss")
            retained = load_session(root / "phase.mdss")
            if len(retained.datasets) != 2 or not retained.module_states["xrd"]["phase_search"]["results"]:
                raise RuntimeError("Frozen phase-search GUI/session round-trip failed")
            from materials_data_studio.modules.xrd.phase_reference import ReferenceSearchEngine
            from materials_data_studio.modules.xrd.qualx_database import ReferenceDatabaseManager
            if pattern.d_spacing_a is None:
                raise RuntimeError("Frozen reference fixture requires d spacings")
            reference = write_reference_smoke_database(root / "reference", pattern.d_spacing_a, pattern.intensity)
            info = ReferenceDatabaseManager(store).register(reference)
            _candidates, reference_results, reference_errors = ReferenceSearchEngine(PhaseSearchService(store)).search(
                peaks, SearchQuery(), CalculationSettings(lower=20, upper=80), cod=True, user=False)
            if info["phase_count"] != 1 or reference_errors or not reference_results or reference_results[0]["match"]["score"] < 99.9:
                raise RuntimeError("Frozen QualX-format offline search failed")
            import sqlite3

            from pymatgen.core import Structure

            from materials_data_studio.modules.xrd.mp_database import MPDatabaseManager
            from materials_data_studio.modules.xrd.phase_store import cif_metadata
            from materials_data_studio.modules.xrd.reference_index import (
                DatabaseConnection,
                create_index,
            )
            mp_path = root / "generated-mp.sqlite"
            from materials_data_studio.modules.xrd.mp_codec import encode_pattern
            card = cif_metadata(_SMOKE_CIF, "mp-test-generated", "Materials Project")
            card.revision = "synthetic-test"
            with sqlite3.connect(mp_path, factory=DatabaseConnection) as connection:
                create_index(connection)
                connection.execute("CREATE TABLE cards(phase_id TEXT PRIMARY KEY,candidate TEXT,pattern TEXT,structure TEXT,details TEXT)")
                manifest = {"format": "mds-mp-reference-2", "license": "CC BY 4.0 (BY-C records only)", "included": 1, "snapshot": "synthetic-test", "engine": "mds-reference-1"}
                connection.execute("INSERT INTO metadata VALUES('manifest',?)", (json.dumps(manifest),))
                connection.execute("INSERT INTO cards VALUES(?,?,?,?,?)", (card.identity, json.dumps(card.to_dict()),
                    encode_pattern({"d": pattern.d_spacing_a.tolist(), "intensities": pattern.intensity.tolist(), "hkl": [[] for _ in pattern.intensity]}),
                    json.dumps(Structure.from_str(_SMOKE_CIF, fmt="cif").as_dict()), json.dumps({"origin": "DFT-relaxed", "license": "CC BY 4.0"})))
                top = np.argsort(pattern.intensity)[::-1][:16]
                connection.execute("INSERT INTO phases VALUES(?,?,?)", (card.identity, len(pattern.intensity), len(top)))
                connection.executemany("INSERT INTO peaks VALUES(?,?,?)", [(card.identity, rank, float(pattern.d_spacing_a[i])) for rank, i in enumerate(top)])
            # Exercise the same two-ZIP importer shipped to end users.
            import zipfile

            from materials_data_studio.modules.xrd.mp_archive import SPLIT_FORMAT
            payload = mp_path.read_bytes()
            split = (len(payload) + 1) // 2
            pieces = [payload[:split], payload[split:]]
            parts = [{"archive": f"mp-part{i}.zip", "member": f"database.part{i}", "bytes": len(piece),
                      "sha256": hashlib.sha256(piece).hexdigest()} for i, piece in enumerate(pieces, 1)]
            archive_manifest = {"distribution_format": SPLIT_FORMAT, "database_sha256": hashlib.sha256(payload).hexdigest(),
                                "database_bytes": len(payload), "parts": parts}
            for part, piece in zip(parts, pieces, strict=True):
                with zipfile.ZipFile(root / part["archive"], "w", zipfile.ZIP_DEFLATED) as zipped:
                    zipped.writestr("manifest.json", json.dumps(archive_manifest))
                    zipped.writestr(part["member"], piece)
            MPDatabaseManager(store).import_archive(root / parts[1]["archive"])
            _, mp_results, mp_errors = ReferenceSearchEngine(PhaseSearchService(store)).search(peaks, SearchQuery(), CalculationSettings(lower=20, upper=80), cod=True, user=False, mp=True, mp_scale_limit=0)
            if mp_errors or {r["candidate"]["source"] for r in mp_results} != {"COD", "Materials Project"}:
                raise RuntimeError("Frozen combined MP/COD offline search failed")
            retained.module_states["xrd"]["phase_search"]["results"] = mp_results
            save_session(retained, root / "mp.mdss")
            if not any(r["candidate"]["source"] == "Materials Project" for r in load_session(root / "mp.mdss").module_states["xrd"]["phase_search"]["results"]):
                raise RuntimeError("Frozen MP session round-trip failed")
            search.close()
            xrd.close()
        report = (
            "OK\n"
            f"reflections={len(pattern.two_theta_deg)}\n"
            f"profile_points={len(pattern.profile_x)}\n"
            f"pxp_graphs={len(graphs)}\n"
            "mdsg_roundtrip=OK\n"
            "pxp_tables=OK\n"
            "h5xp_roundtrip=OK\n"
            "graph_exports=PNG,SVG,PDF,EPS,BMP,JPEG,TIFF\n"
            "command_services=OK\n"
            "gui_command_recording=OK\n"
            "selection_properties_lifetime=OK\n"
            "explicit_docking=OK\n"
            "graph_toolbar_and_transfer=OK\n"
            "procedure_log_grouping=OK\n"
            "procedure_help=OK\n"
            "graph_manuals_and_help_menu=OK\n"
            "editor_send_to_igor=OK\n"
            "python_worker_graph_and_stdout=OK\n"
            "igor_conversion_and_isolated_runtime=OK\n"
            "isolated_public_mds_command=OK\n"
            "prepared_resource_sidecar=OK\n"
            "virtual_text_io_no_host_write=OK\n"
            "prepared_inputs_not_mdsg_metadata=OK\n"
            "interactive_macro_file_prompt_message_cancel=OK\n"
            "interactive_inputs_not_mdsg_metadata=OK\n"
            "legacy_rtglobals_and_font_dependencies=OK\n"
            "axis_colors_tick_range=OK\n"
            "python_appcontainer_no_network=OK\n"
            "local_cif_phase_search_match=OK\n"
            "phase_search_gui_session=OK\n"
            "qualx_sqlite_offline_reference_search=OK\n"
            "materials_project_offline_search_and_session=OK\n"
        )
    except Exception:
        report = "FAILED\n" + traceback.format_exc()
        success = False
    else:
        success = True
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return success
