"""Igor-style range/trace menus operating only on the viewer's local history."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, replace
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QColorDialog, QInputDialog, QMenu, QMessageBox

from materials_data_studio.tools.graph_actions import graph_action, wave_action
from materials_data_studio.tools.graph_export import data_columns
from materials_data_studio.tools.graph_fit import MODELS
from materials_data_studio.tools.graph_symbols import style_icon, visual_choices
from materials_data_studio.tools.pxp_model import PxpAxis

if TYPE_CHECKING:
    from materials_data_studio.tools.graph_window import GraphEditorWindow


def zoom_region(window: GraphEditorWindow, direction: str, dimensions: str) -> None:
    """Expand is zoom-in; Shrink applies its inverse, including logarithmic axes."""
    graph, editor, region = window.graph(), window.editor, window.region
    if graph is None or editor is None:
        return
    if direction == "all":
        graph = replace(graph, axes=tuple(replace(axis, limits=None) for axis in graph.axes))
        layout = window.graph_layout()
        layout.get("view", {}).pop("axes", None)
        editor.edit_graph(graph, "View all", layout)
        return
    if region is None:
        return
    ax = region["ax"]
    corners = np.array([region["start"], region["end"]], dtype=float)
    # Restrict the selected box to its plot; tiny boxes never generate huge ranges.
    corners[:, 0] = np.clip(corners[:, 0], ax.bbox.x0, ax.bbox.x1)
    corners[:, 1] = np.clip(corners[:, 1], ax.bbox.y0, ax.bbox.y1)
    xlo, xhi = sorted(corners[:, 0])
    ylo, yhi = sorted(corners[:, 1])
    plot_axes = [a for a in window.figure.axes if not str(a.get_gid() or "").startswith("colorbar:")]
    names = list(dict.fromkeys(t.axis for t in graph.traces)) or ["left"]
    yname = str(ax.get_gid()).split(":", 1)[1] if str(ax.get_gid()).startswith("plotaxis:") else names[min(plot_axes.index(ax), len(names) - 1)]
    xname,yname = getattr(ax,"_mds_axis_names",("bottom",yname))
    identifiers = (yname, xname) if graph.swap_xy else (xname, yname)
    updates = {}
    for index, (identifier, bounds, limits, logarithmic) in enumerate(zip(
        identifiers, ((xlo, xhi, ax.bbox.x0, ax.bbox.width), (ylo, yhi, ax.bbox.y0, ax.bbox.height)),
        (ax.get_xlim(), ax.get_ylim()), (ax.get_xscale() == "log", ax.get_yscale() == "log"), strict=True,
    )):
        if (dimensions == "horizontal" and index == 1) or (dimensions == "vertical" and index == 0):
            continue
        lower, upper, origin, extent = bounds
        p, q = (lower - origin) / extent, (upper - origin) / extent
        if q - p < 0.01:
            continue
        a, b = np.log10(limits) if logarithmic else limits
        if direction == "expand":
            limits_new = a + p * (b - a), a + q * (b - a)
        else:
            span = (b - a) / (q - p)
            limits_new = a - p * span, a + (1 - p) * span
        values = tuple(float(10**v if logarithmic else v) for v in limits_new)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Selected zoom range is outside numerical limits.")
        updates[identifier] = values
    axes = list(graph.axes)
    for identifier, limits in updates.items():
        if any(axis.name == identifier for axis in axes):
            axes = [replace(axis, limits=limits) if axis.name == identifier else axis for axis in axes]
        else:
            axes.append(PxpAxis(identifier, limits=limits))
    layout = window.graph_layout()
    for identifier in updates:
        layout.get("view", {}).get("axes", {}).pop(identifier, None)
    editor.edit_graph(replace(graph, axes=tuple(axes)), f"{direction.title()} {dimensions}", layout)


def graph_context_menu(window: GraphEditorWindow, event: Any) -> QMenu:
    menu = QMenu(window)
    menu.setObjectName("GraphContextMenu")
    if window.region:
        for title, direction, dimensions in (("Expand", "expand", "both"),
                                            ("Horiz Expand", "expand", "horizontal"),
                                            ("Vert Expand", "expand", "vertical"),
                                            ("Shrink", "shrink", "both"),
                                            ("Horiz Shrink", "shrink", "horizontal"),
                                            ("Vert Shrink", "shrink", "vertical")):
            zoom_action = menu.addAction(title)
            zoom_action.triggered.connect(lambda _checked=False, d=direction, dim=dimensions: window.zoom_region(d, dim))
        return menu
    graph, editor = window.graph(), window.editor
    if graph is None or editor is None:
        return menu
    target: tuple[str, str] | None = None
    renderer = window.canvas.get_renderer()
    priority = {"drawing": 0, "annotation": 0, "legend": 0, "label": 1, "trace": 2, "axis": 3, "colorbar": 4, "image": 5}
    for artist, candidate in sorted(window.artist_targets.items(), key=lambda item: priority.get(item[1][0], 9)):
        if not artist.get_visible():
            continue
        try:
            hit = artist.get_window_extent(renderer).contains(event.x, event.y) if candidate[0] in {"annotation", "legend", "label", "colorbar"} else artist.contains(event)[0]
        except (TypeError, AttributeError):
            hit = False
        if hit:
            target = candidate
            break

    def action(title: str, callback: Any, parent: QMenu = menu) -> QAction:
        result = parent.addAction(title)
        def invoke(_checked: bool = False) -> None:
            try:
                callback()
            except Exception as exc:
                QMessageBox.warning(window, "Graph Action", str(exc))
        result.triggered.connect(invoke)
        return result

    def create_menu(title: str, parent: QMenu = menu) -> QMenu:
        child = QMenu(title, parent)
        parent.addMenu(child)
        return child

    if target and target[0] == "drawing":
        from materials_data_studio.tools.graph_drawing import drawing_operation
        index = int(target[1])
        window.drawing_selected = index
        window.select_target("drawing", str(index))
        for title, operation in (("Modify Drawing…", "edit"), ("Cut", "cut"), ("Copy", "copy"), ("Clear", "clear"), ("Duplicate", "duplicate"),
                                 ("Bring to Front", "front"), ("Send to Back", "back")):
            action(title, lambda op=operation: drawing_operation(window, op, index=index))
    elif target and target[0] == "trace":
        identifier = target[1]
        trace = next(t for t in graph.traces if t.name == identifier)
        def browse() -> None:
            wave = editor.document.render_source().wave(trace.y_wave)
            if wave is None:
                return
            # Inspect source data in a separate, read-only window; don't leave the graph.
            dialog = QInputDialog(window)
            dialog.setWindowTitle(f"Browse {identifier} — read-only source wave")
            dialog.setOption(QInputDialog.InputDialogOption.UsePlainTextEditForTextInput)
            dialog.setLabelText(f"{wave.igor_path} | {wave.dtype} | {wave.shape}\nFirst 1000 values (use Export Data for all points)")
            dialog.setTextValue("\n".join(str(v) for v in wave.data[:1000]))
            from PySide6.QtWidgets import QPlainTextEdit
            for widget in dialog.findChildren(QPlainTextEdit):
                widget.setReadOnly(True)
            dialog.resize(540, 600)
            dialog.exec()

        def modify(**changes: Any) -> None:
            window.apply_properties("trace", identifier, changes)

        def duplicate() -> None:
            count = 2
            while any(t.name == f"{identifier}_{count}" for t in graph.traces):
                count += 1
            duplicate_name = f"{identifier}_{count}"
            layout = window.graph_layout()
            overrides = layout.get("view", {}).get("traces", {}).get(identifier)
            if overrides is not None:
                layout.setdefault("view", {}).setdefault("traces", {})[duplicate_name] = copy.deepcopy(overrides)
            editor.edit_graph(replace(graph, traces=(*graph.traces, replace(trace, name=duplicate_name))), "Duplicate trace", layout)

        def replace_wave() -> None:
            graph_action(window, "replace", identifier)

        def reorder(offset: int) -> None:
            traces = list(graph.traces)
            index = traces.index(trace)
            traces.pop(index)
            traces.insert(max(0, min(len(traces), index + offset)), trace)
            editor.edit_graph(replace(graph, traces=tuple(traces)), "Reorder trace", window.graph_layout())

        action(f"Browse {identifier}…", browse)
        action(f"Edit {identifier}", lambda: wave_action(window, "edit", trace.y_wave))
        action(f"Remove {identifier}", lambda: editor.edit_graph(replace(graph, traces=tuple(t for t in graph.traces if t.name != identifier)), "Remove trace", window.graph_layout()))
        action(f"Hide {identifier}", lambda: modify(visible=False))
        action(f"Duplicate {identifier}", duplicate)
        action("Replace Wave…", replace_wave)
        copy_menu = create_menu("Copy")
        def copy_data(index: int) -> None:
            columns = data_columns(editor.document, graph.name, ["trace:" + identifier])
            QApplication.clipboard().setText("\n".join(format(float(value), ".17g") for value in columns[index][1]))
        action("X values", lambda: copy_data(0), copy_menu)
        action("Y values", lambda: copy_data(1), copy_menu)
        action("Trace settings", lambda: QApplication.clipboard().setText(json.dumps({"trace": asdict(trace), "display": window.graph_layout().get("view", {}).get("traces", {}).get(identifier, {})}, indent=2)), copy_menu)
        menu.addSeparator()
        action(f"Modify {identifier}…", lambda: window.select_target("trace", identifier))
        unsupported = menu.addAction("Customize at Point")
        unsupported.setEnabled(False)
        unsupported.setToolTip("Per-point style overrides are not supported by this read-only-wave editor.")
        for title, key, choices in (("Mode", "mode", [("Lines", 0), ("Dots", 2), ("Markers", 3), ("Lines + markers", 4), ("Steps", 6)]),
                                    ("Line Style", "line_style", visual_choices("line")),
                                    ("Line Size", "line_width", [(str(v), v) for v in (0.5, 1, 1.5, 2, 3, 4)]),
                                    ("Markers", "marker", visual_choices("marker")),
                                    ("Marker Size", "marker_size", [(str(v), v) for v in (2, 3, 4, 5, 6, 8, 10)])):
            submenu = create_menu(title)
            if key in {"line_style", "line_width"}:
                submenu.setEnabled(trace.mode not in {2, 3, 5, 8})
            if key == "marker":
                submenu.setEnabled(trace.mode in {3, 4, 8})
            if key == "marker_size":
                submenu.setEnabled(trace.mode in {2, 3, 4, 8})
            for label, value in choices:
                item = action(label, lambda k=key, v=value: modify(**{k: v}), submenu)
                if key in {"line_style", "marker"}:
                    item.setIcon(style_icon("line" if key == "line_style" else "marker", int(value),
                        window.palette().window().color().lightness() < 128, dict(graph.style.dash_patterns).get(int(value)) if key == "line_style" else None))
        color_menu = create_menu("Color")
        for label, color in (("Black", "#000000"), ("Blue", "#2869ad"), ("Red", "#c73737"), ("Green", "#22865c")):
            action(label, lambda c=color: modify(color=c), color_menu)
        def choose_color() -> None:
            color = QColorDialog.getColor(parent=window, title="Trace Color")
            if color.isValid():
                modify(color=color.name())
        action("Custom…", choose_color, color_menu)
        menu.addSeparator()
        action("Bring to Front", lambda: reorder(len(graph.traces)))
        action("Send to Back", lambda: reorder(-len(graph.traces)))
        action("Forward", lambda: reorder(1))
        action("Backward", lambda: reorder(-1))
        axis_menu = create_menu("Move to Other Y Axis")
        for name in dict.fromkeys(["left", "right", *(a.name for a in graph.axes if (a.side or a.name) not in {"bottom", "top"})]):
            def move_axis(identifier_axis: str = name) -> None:
                axes = graph.axes if any(a.name == identifier_axis for a in graph.axes) else (*graph.axes, PxpAxis(identifier_axis))
                traces = tuple(replace(t, axis=identifier_axis) if t.name == identifier else t for t in graph.traces)
                editor.edit_graph(replace(graph, axes=axes, traces=traces), "Move trace axis", window.graph_layout())
            action(name, move_axis, axis_menu)
        menu.addSeparator()
        add_quick_fit(menu, window, identifier)
    elif target:
        kind, identifier = target
        action(f"Modify {identifier}…", lambda: window.edit_annotation(kind, identifier) if kind in {"annotation", "legend", "colorbar"} else window.select_target(kind, identifier))
        if kind in {"annotation", "legend", "label"}:
            action("Edit Text…", lambda: window.edit_text(kind, identifier))
        if kind in {"annotation", "legend"}:
            action("Hide", lambda: window.apply_properties("annotation", identifier, {"visible": False}))
    else:
        action("Modify Axes…", lambda: window.select_target("axis", graph.axes[0].name if graph.axes else "bottom"))
        action("Graph Options…", lambda: window.select_target("geometry", graph.name))
        action("Add Annotation…", window.add_text)
        action("Append Traces…", lambda: graph_action(window, "append"))
        action("Reorder Traces…", lambda: graph_action(window, "reorder"))
        if graph.traces:
            add_quick_fit(menu, window)
        hidden = [t for t in graph.traces if not window.graph_layout().get("view", {}).get("traces", {}).get(t.name, {}).get("visible", True)]
        if hidden:
            show = create_menu("Show Hidden Trace")
            for trace in hidden:
                action(trace.name, lambda name=trace.name: window.apply_properties("trace", name, {"visible": True}), show)
    menu.addSeparator()
    action("View All", lambda: zoom_region(window, "all", "both"))
    action("Export Figure…", window.export_graph)
    action("Export Data…", window.export_data)
    menu.addSeparator()
    menu.addAction(window.undo_action)
    menu.addAction(window.redo_action)
    return menu


def add_quick_fit(menu: QMenu, window: GraphEditorWindow, trace_name: str = "") -> None:
    quick = QMenu("Quick Fit", menu)
    menu.addMenu(quick)
    for model in MODELS:
        quick.addAction(model, lambda _checked=False, value=model: window.quick_fit(trace_name, value))
    for name in ("poly", "poly_XOffset"):
        submenu = QMenu(name, quick)
        quick.addMenu(submenu)
        for degree in range(2, 11):
            submenu.addAction(f"Order {degree}", lambda _checked=False, value=f"{name}:{degree}": window.quick_fit(trace_name, value))
    quick.addSeparator()
    for key in ("poly2d", "gauss2d"):
        title = "poly2D…" if key == "poly2d" else "Gauss2D…"
        from materials_data_studio.tools.graph_analysis_forms import show_analysis
        quick.addAction(title, lambda _checked=False, kind=key: show_analysis(window, kind, statistical=False))
    quick.addSeparator()
    for title, attribute, default in (("Fit Between Cursors / Selected Region", "fit_between_cursors", True),
                                      ("Weight From Error Bar Wave", "fit_weighted", True),
                                      ("Add Coefficient Textbox", "fit_textbox", False)):
        item = quick.addAction(title)
        item.setCheckable(True)
        item.setChecked(getattr(window, attribute, default))
        item.toggled.connect(lambda value, key=attribute: setattr(window, key, value))
    def refresh() -> None:
        graph = window.graph()
        for action in quick.actions():
            if action.text() in {"poly2D…", "Gauss2D…"}:
                action.setEnabled(graph is not None and bool(graph.images or graph.contours))
            key = {"Fit Between Cursors / Selected Region": "fit_between_cursors", "Weight From Error Bar Wave": "fit_weighted", "Add Coefficient Textbox": "fit_textbox"}.get(action.text())
            if key:
                action.blockSignals(True)
                action.setChecked(getattr(window, key, key != "fit_textbox"))
                action.blockSignals(False)
    quick.aboutToShow.connect(refresh)
