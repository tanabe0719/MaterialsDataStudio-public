"""Widget/artist-free semantic operations shared by GUI, commands and Python."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from materials_data_studio.tools.graph_document import EditableGraphDocument, GraphState
from materials_data_studio.tools.graph_waves import make_wave, put_waves, validate_references
from materials_data_studio.tools.pxp_model import PxpAxis, PxpGraph, PxpTrace, PxpWave


@dataclass
class CommandResult:
    success: bool
    value: Any = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    affected_objects: list[str] = field(default_factory=list)
    status: str = "Supported"


def assign_trace_axes(graph: PxpGraph, trace_name: str, *, axis: str | None = None,
                      x_axis: str | None = None) -> PxpGraph:
    trace = next(t for t in graph.traces if t.name == trace_name)
    y_name, x_name = axis or trace.axis, x_axis or trace.x_axis
    if not y_name or not x_name or y_name == x_name:
        raise ValueError("X/Y axis names must be nonempty and distinct")
    axes = list(graph.axes)
    for name, sides, default in ((y_name,{"left","right"},"left"),(x_name,{"bottom","top"},"bottom")):
        model = next((a for a in axes if a.name == name),None)
        if model is None:
            if name not in sides:
                raise ValueError(f"Create the free axis before assigning it: {name}")
            axes.append(PxpAxis(name,side=name))
        elif (model.side or (name if name in {"left","right","bottom","top"} else default)) not in sides:
            raise ValueError(f"Incompatible axis orientation: {name}")
    return replace(graph,axes=tuple(axes),traces=tuple(
        replace(t,axis=y_name,x_axis=x_name) if t.name == trace_name else t for t in graph.traces))


def replace_graph_state(document: EditableGraphDocument, graph: PxpGraph,
                        layout: dict[str, Any] | None = None) -> GraphState:
    after = document.current.clone()
    after.graphs = tuple(graph if item.name == graph.name else item for item in after.graphs)
    if not any(g.name == graph.name for g in after.graphs):
        after.graphs += (graph,)
    if layout is not None:
        import copy
        after.layout[graph.name] = copy.deepcopy(layout)
    return after


def append_trace_model(graph: PxpGraph, y: str, x: str | None = None,
                       axis: str = "left", side: str = "", x_axis: str = "bottom",
                       x_side: str = "") -> PxpGraph:
    base, index = y.rsplit(":", 1)[-1], 1
    name = base
    while name in {t.name for t in graph.traces}:
        index += 1
        name = f"{base}#{index - 1}"
    palette = ((0, 0, 0), (10000, 25000, 50000), (50000, 10000, 10000), (10000, 40000, 25000))
    trace = PxpTrace(name, y, x, axis, mode=0, line_width=1.2,
                     color_igor=palette[len(graph.traces) % len(palette)], x_axis=x_axis)
    if axis == x_axis or axis in {"bottom", "top"} or x_axis in {"left", "right"}:
        raise ValueError("X and Y axes must have distinct, compatible names")
    axes = graph.axes if any(a.name == axis for a in graph.axes) else (*graph.axes, PxpAxis(axis, side=side))
    if not any(a.name == x_axis for a in axes):
        axes = (*axes, PxpAxis(x_axis, side=x_side or ("top" if x_axis == "top" else "bottom")))
    for name, expected in ((axis, {"left", "right"}), (x_axis, {"bottom", "top"})):
        model = next(a for a in axes if a.name == name)
        if model.side and model.side not in expected:
            raise ValueError(f"Incompatible axis orientation: {name}")
    return replace(graph, traces=(*graph.traces, trace), axes=axes)


class DocumentServices:
    def __init__(self, document: EditableGraphDocument, context: dict[str, str] | None = None) -> None:
        self.document = document
        self.context = dict(context or {})
        self.warnings: list[str] = []
        self.expression_functions = {
            "pcsr": lambda cursor, window="": self.cursor_value("p", cursor, window),
            "hcsr": lambda cursor, window="": self.cursor_value("x", cursor, window),
            "vcsr": lambda cursor, window="": self.cursor_value("y", cursor, window),
            "note": lambda wave: self.wave(wave).wave_note,
            "getdimlabel": lambda wave, dim, index: self.get_dimension_label(wave, dim, index),
            "finddimlabel": lambda wave, dim, label: self.find_dimension_label(wave, dim, label),
            "axisinfo": lambda window, axis: self.readback("axis", window, axis),
            "traceinfo": self.trace_readback,
            "numpnts": lambda data: self.wave(data).data.size if isinstance(data, str) else np.asarray(data).size,
            "dimsize": lambda data, dim: self.dimension_size(data, dim),
            "dimoffset": lambda wave, dim: self.wave(wave).dimension_scaling[int(dim)][0],
            "dimdelta": lambda wave, dim: self.wave(wave).dimension_scaling[int(dim)][1],
        }
        self.expression_wave_parameters = {"dimoffset": (0,), "dimdelta": (0,), "dimsize": (0,), "numpnts": (0,), "note": (0,), "getdimlabel": (0,), "finddimlabel": (0,)}
        self.expression_wave_reference = lambda name: self.wave(name).igor_path

    def cursor_value(self, kind: str, cursor: str, window: str = "") -> float:
        from materials_data_studio.tools.igor_graph_extensions import cursor_value
        return cursor_value(self, kind, cursor, window)

    def trace_readback(self, window: str, trace: str, mode: int = 0) -> str:
        if mode != 0:
            raise ValueError("TraceInfo supports mode 0 semantic JSON only; native output formats are not emulated")
        return self.readback("trace", window, trace)

    def dimension_size(self, data: Any, dimension: Any) -> int:
        if int(dimension) != dimension or not 0 <= dimension < 4:
            raise ValueError("Dimension must be an integer in 0..3")
        shape = self.wave(data).shape if isinstance(data, str) else np.shape(data)
        return shape[int(dimension)] if dimension < len(shape) else 0

    def get_dimension_label(self, wave: str, dimension: int, index: int) -> str:
        from materials_data_studio.tools.igor_wave_ops import dimension_label
        if int(dimension) != dimension or int(index) != index:
            raise ValueError("Dimension/label index must be integers")
        return dimension_label(self.wave(wave), int(dimension), int(index))

    def find_dimension_label(self, wave: str, dimension: int, label: str) -> int:
        model = self.wave(wave)
        if int(dimension) != dimension or not 0 <= dimension < model.data.ndim:
            raise ValueError("Dimension is outside the wave")
        labels = model.dimension_labels[int(dimension)] if dimension < len(model.dimension_labels) else ()
        if dimension < len(model.dimension_names) and label == model.dimension_names[int(dimension)] and label:
            return -1
        return labels.index(label) if label in labels else -2

    def readback(self, kind: str, window: str, name: str) -> str:
        import json

        from materials_data_studio.tools.igor_context import axis_info, trace_info
        value = (axis_info if kind == "axis" else trace_info)(self.graph(window or None), name)
        # A documented MDS semantic JSON subset, not native binary/readback state.
        def finite(item):
            if isinstance(item, float) and not np.isfinite(item):
                return str(item)
            if isinstance(item, dict):
                return {k: finite(v) for k,v in item.items()}
            if isinstance(item, (list,tuple)):
                return [finite(v) for v in item]
            return item
        return json.dumps(finite(value), ensure_ascii=False, sort_keys=True, allow_nan=False)

    def move_folder(self, old: str, new: str) -> None:
        from materials_data_studio.tools.graph_waves import rename_wave
        old, new = self.path(old), self.path(new)
        folders = set(self.document.render_source().folders)
        if old == "root" or old not in folders or new == old or new.startswith(old + ":"):
            raise ValueError("Invalid/cyclic folder move")
        if new in folders or new.rsplit(":", 1)[0] not in folders:
            raise ValueError("Destination exists or its parent is missing")
        def mapped(path: str) -> str:
            return new + path[len(old):] if path == old or path.startswith(old + ":") else path
        waves = self.document.render_source().waves
        targets = [mapped(w.igor_path) for w in waves]
        if len(set(targets)) != len(targets):
            raise ValueError("Folder move would overwrite waves")
        for wave in waves:
            target = mapped(wave.igor_path)
            if target != wave.igor_path:
                self.document.current = rename_wave(self.document, wave.igor_path, target)
        for section in ("variables", "strings", "operation_results"):
            values = self.environment.get(section, {})
            self.environment[section] = {mapped(k): v for k, v in values.items()}
        self.environment["folders"] = sorted(mapped(p) for p in folders)
        self.environment["current_folder"] = mapped(self.current_folder)

    @property
    def environment(self) -> dict[str, Any]:
        return self.document.current.environment

    @property
    def current_folder(self) -> str:
        return self.environment.get("current_folder", "root")

    def path(self, name: str) -> str:
        import re
        # Parser validation happens before service dispatch. Canonical imported
        # names (including unit parentheses) are opaque names, not expressions.
        name = re.sub(r"'([^']*)'", lambda match: match[1], name).rstrip(":")
        if not name or any(c in name for c in "\0\r\n"):
            raise ValueError("Invalid object name")
        return name if name == "root" or name.startswith("root:") else self.current_folder + ":" + name

    def wave(self, name: str) -> PxpWave:
        import re
        name = re.sub(r"'([^']*)'", lambda match: match[1], name)
        source = self.document.render_source()
        paths = [name] if name.startswith("root:") else [self.path(name), "root:" + name]
        for path in paths:
            found = next((w for w in source.waves if w.igor_path == path or w.id == name), None)
            if found is not None:
                return found
        raise ValueError(f"Unknown or ambiguous wave: {name}; specify its full root:path")

    def resolve(self, name: str) -> Any:
        results = self.environment.get("operation_results", {})
        for path in (self.path(name), "root:" + name):
            key = next((key for key in results if key.casefold() == path.casefold()), None)
            if key is not None:
                value = results[key]
                return float(value) if isinstance(value, str) else value
        for section in ("variables", "strings"):
            values = self.environment.get(section, {})
            for path in (self.path(name), "root:" + name):
                if path in values:
                    return values[path]
        return self.wave(name).data

    def set_results(self, values: dict[str, float]) -> None:
        """Operation variables are transactional, strict-JSON and Undo/save aware."""
        results = self.environment.setdefault("operation_results", {})
        for name, value in values.items():
            path = self.path(name)
            key = next((key for key in results if key.casefold() == path.casefold()), path)
            results[key] = float(value) if np.isfinite(value) else str(float(value))

    def write(self, waves: list[PxpWave], *, overwrite: bool = False) -> None:
        self.document.current = put_waves(self.document, waves, overwrite=overwrite)

    def create_wave(self, name: str, values: Any, *, overwrite: bool = False,
                    template: PxpWave | None = None) -> PxpWave:
        data = np.asarray(values)
        if any(self.path(name) in self.environment.get(section, {}) for section in ("variables", "strings")):
            raise ValueError("A wave cannot shadow a scalar variable/string")
        if data.size > 10_000_000 or data.nbytes > 256 * 1024**2:
            raise ValueError("A command wave is limited to 10 million elements / 256 MiB")
        wave = make_wave(self.path(name), data, template)
        if template is not None:
            import uuid
            wave = replace(wave, id=uuid.uuid4().hex)
        self.write([wave], overwrite=overwrite)
        return self.wave(wave.igor_path)

    def graph(self, name: str | None = None) -> PxpGraph:
        name = name or self.context.get("graph")
        if not name:
            raise ValueError("No active graph. Use Display or DoWindow/F first.")
        try:
            return self.document.graph(name)
        except StopIteration as exc:
            raise ValueError(f"Unknown graph: {name}") from exc

    def put_graph(self, graph: PxpGraph) -> None:
        after = replace_graph_state(self.document, graph)
        validate_references(self.document, after)
        self.document.current = after
        self.context["graph"] = graph.name
        self.context["selected_kind"] = "graph"

    def new_graph(self, name: str = "Graph0") -> PxpGraph:
        names = {g.name for g in self.document.current.graphs}
        base, index = name, 0
        while name in names:
            index += 1
            name = f"{base}_{index}"
        graph = PxpGraph(name, axes=(PxpAxis("left"), PxpAxis("bottom")))
        self.put_graph(graph)
        return graph

    def append(self, y: str, x: str | None = None, axis: str = "left", side: str = "",
               x_axis: str = "bottom", x_side: str = "") -> None:
        graph = append_trace_model(self.graph(), self.wave(y).igor_path,
                                   self.wave(x).igor_path if x else None, axis, side, x_axis, x_side)
        self.put_graph(graph)

    def set_axis(self, name: str, *, limits: Any = ..., label: str | None = None) -> None:
        graph = self.graph()
        axes = list(graph.axes)
        index = next((i for i, a in enumerate(axes) if a.name == name), None)
        if index is None:
            raise ValueError(f"Unknown axis: {name}")
        fields = {}
        if limits is not ...:
            fields["limits"] = limits
        if label is not None:
            fields["label_raw"] = label
        axes[index] = replace(axes[index], **fields)
        override = self.document.current.layout.get(graph.name, {}).get("view", {}).get("axes", {}).get(name, {})
        if limits is not ...:
            override.pop("limits", None)
        if label is not None:
            override.pop("label", None)
        self.put_graph(replace(graph, axes=tuple(axes)))


def semantic_events(document: EditableGraphDocument, before: GraphState, after: GraphState,
                    title: str) -> list[str]:
    from materials_data_studio.tools.command_recording import semantic_events as record
    return record(document, before, after, title)
