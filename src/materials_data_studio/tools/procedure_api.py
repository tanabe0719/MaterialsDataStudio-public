"""Public Python façade over canonical graph document services; no widget state."""
from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import replace
from types import ModuleType
from typing import Any

import numpy as np

from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_waves import delete_wave, make_wave, rename_wave


class WaveHandle:
    def __init__(self, services: DocumentServices, name: str) -> None:
        self.services, self.path = services, services.wave(name).igor_path

    @property
    def values(self) -> np.ndarray:
        # Immutable view, not a writable alias to source or Undo history.
        view = self.services.wave(self.path).data.view()
        view.setflags(write=False)
        return view

    @values.setter
    def values(self, values: Any) -> None:
        old = self.services.wave(self.path)
        self.services.write([make_wave(self.path, values, old)], overwrite=True)

    def assign(self, values: Any) -> None:
        """Broadcast into the existing shape/type using the Command range guard."""
        from materials_data_studio.tools.igor_context import assignment_values
        old = self.services.wave(self.path)
        values = np.broadcast_to(values, old.shape)
        if old.data.dtype.kind not in "US":
            values = assignment_values(values, old.data.dtype)
        self.values = values

    @property
    def name(self) -> str:
        return self.services.wave(self.path).name

    @property
    def coordinates(self) -> dict[str, Any]:
        from materials_data_studio.tools.igor_context import coordinates
        return coordinates(self.services.wave(self.path))

    @name.setter
    def name(self, name: str) -> None:
        parent = self.path.rsplit(":", 1)[0]
        new = name if name.startswith("root:") else parent + ":" + name
        self.services.document.current = rename_wave(self.services.document, self.path, new)
        self.path = new

    @property
    def units(self) -> str:
        return self.services.wave(self.path).data_units

    @units.setter
    def units(self, units: str) -> None:
        self.services.write([replace(self.services.wave(self.path), data_units=str(units))], overwrite=True)

    @property
    def shape(self) -> tuple[int, ...]:
        return self.services.wave(self.path).shape

    @property
    def note(self) -> str:
        """Wave metadata text, preserved by MDSG/H5XP."""
        return self.services.wave(self.path).wave_note

    @note.setter
    def note(self, value: str) -> None:
        if not isinstance(value, str) or len(value.encode("utf-8")) > 1024**2:
            raise ValueError("Wave note must be text limited to 1 MiB")
        self.services.write([replace(self.services.wave(self.path), wave_note=value)], overwrite=True)

    def set_label(self, dimension: int, index: int, label: str) -> None:
        """Name an element, or the dimension itself with index -1."""
        from materials_data_studio.tools.igor_wave_ops import set_dimension_label
        self.services.write([set_dimension_label(self.services.wave(self.path), dimension, index, label)], overwrite=True)

    def get_label(self, dimension: int, index: int) -> str:
        return self.services.get_dimension_label(self.path, dimension, index)

    @property
    def scaling(self) -> tuple[tuple[float, float], ...]:
        return self.services.wave(self.path).dimension_scaling

    @scaling.setter
    def scaling(self, values: Any) -> None:
        values = tuple(tuple(map(float, v)) for v in values)
        if len(values) != len(self.shape) or any(len(v) != 2 or not np.isfinite(v).all() or v[1] == 0 for v in values):
            raise ValueError("Scaling needs one finite (start, nonzero delta) pair per dimension")
        self.services.write([replace(self.services.wave(self.path), dimension_scaling=values)], overwrite=True)


class WaveAPI:
    def __init__(self, services: DocumentServices) -> None:
        self.services = services

    def __call__(self, name: str) -> WaveHandle:
        return WaveHandle(self.services, name)

    def create(self, name: str, values: Any, *, x: Any = None, overwrite: bool = False, units: str = "") -> WaveHandle:
        created = self.services.create_wave(name, values, overwrite=overwrite)
        handle = self(created.igor_path)
        handle.units = units
        if x is not None:
            coordinates = np.asarray(x, dtype=float)
            if coordinates.shape != (created.shape[0],) or len(coordinates) < 2 or not np.allclose(np.diff(coordinates), coordinates[1] - coordinates[0]):
                raise ValueError("x must be uniformly spaced and match the first dimension; use a separate X wave otherwise")
            handle.scaling = ((float(coordinates[0]), float(coordinates[1] - coordinates[0])), *handle.scaling[1:])
        return handle

    def delete(self, name: str) -> None:
        self.services.document.current = delete_wave(self.services.document, self.services.wave(name).igor_path)

    def names(self) -> list[str]:
        return [w.igor_path for w in self.services.document.render_source().waves]


class AxisHandle:
    def __init__(self, graph: GraphHandle, name: str) -> None:
        self.graph, self.name = graph, name

    def set_range(self, lower: float | None, upper: float | None) -> None:
        if any(v is not None and not np.isfinite(v) for v in (lower, upper)):
            raise ValueError("Axis bounds must be finite or None")
        self.graph.activate()
        self.graph.services.set_axis(self.name, limits=(lower, upper))

    def info(self) -> dict[str, Any]:
        from materials_data_studio.tools.igor_context import axis_info
        return axis_info(self.graph.model, self.name)

    @property
    def label(self) -> str:
        return next(a.label_raw for a in self.graph.model.axes if a.name == self.name)

    @label.setter
    def label(self, value: str) -> None:
        self.graph.activate()
        self.graph.services.set_axis(self.name, label=str(value))


class TraceHandle:
    def __init__(self, graph: GraphHandle, name: str) -> None:
        self.graph, self.name = graph, name

    def _set(self, field: str, value: Any) -> None:
        graph = self.graph.model
        self.graph.services.put_graph(replace(graph, traces=tuple(replace(t, **{field: value}) if t.name == self.name else t for t in graph.traces)))

    def info(self) -> dict[str, Any]:
        from materials_data_studio.tools.igor_context import trace_info
        return trace_info(self.graph.model, self.name)

    @property
    def line_width(self) -> float | None:
        return next(t.line_width for t in self.graph.model.traces if t.name == self.name)

    @line_width.setter
    def line_width(self, value: float) -> None:
        if not np.isfinite(value) or value < 0:
            raise ValueError("Line width must be finite and nonnegative")
        self._set("line_width", float(value))

    @property
    def color(self) -> Any:
        return next(t.color_igor for t in self.graph.model.traces if t.name == self.name)

    @color.setter
    def color(self, value: Any) -> None:
        from matplotlib.colors import to_rgb
        rgb = to_rgb(value) if isinstance(value, str) else tuple(value)
        if len(rgb) != 3 or any(not np.isfinite(v) or v < 0 or v > 1 for v in rgb):
            raise ValueError("Use a color name/hex or three RGB components in [0,1]")
        self._set("color_igor", tuple(round(v * 65535) for v in rgb))


class GraphHandle:
    def __init__(self, services: DocumentServices, name: str) -> None:
        self.services, self.name = services, name

    @property
    def model(self):
        return self.services.graph(self.name)

    def activate(self) -> None:
        self.services.context["graph"] = self.name

    def append(self, y: str | WaveHandle, x: str | WaveHandle | None = None, *, axis: str = "left", x_axis: str = "bottom", x_side: str = "") -> TraceHandle:
        self.activate()
        self.services.append(y.path if isinstance(y, WaveHandle) else y, x.path if isinstance(x, WaveHandle) else x, axis, x_axis=x_axis, x_side=x_side)
        return TraceHandle(self, self.model.traces[-1].name)

    def axis(self, name: str) -> AxisHandle:
        if not any(a.name == name for a in self.model.axes):
            raise ValueError("Unknown axis")
        return AxisHandle(self, name)

    def trace(self, name: str) -> TraceHandle:
        if not any(t.name == name for t in self.model.traces):
            raise ValueError("Unknown trace")
        return TraceHandle(self, name)


class GraphAPI:
    def __init__(self, services: DocumentServices) -> None:
        self.services = services

    def new(self, name: str = "Graph0") -> GraphHandle:
        return GraphHandle(self.services, self.services.new_graph(name).name)

    def active(self) -> GraphHandle:
        return GraphHandle(self.services, self.services.graph().name)

    def __call__(self, name: str) -> GraphHandle:
        return GraphHandle(self.services, self.services.graph(name).name)


class FolderAPI:
    def __init__(self, services: DocumentServices) -> None:
        self.services = services

    @property
    def current(self) -> str:
        return self.services.current_folder

    def set(self, name: str) -> None:
        path = self.services.path(name)
        if path not in self.services.document.render_source().folders:
            raise ValueError("Unknown data folder")
        self.services.environment["current_folder"] = path

    def create(self, name: str) -> str:
        path = self.services.path(name)
        folders = list(self.services.document.render_source().folders)
        if path in folders or path.rsplit(":", 1)[0] not in folders:
            raise ValueError("Folder exists or its parent is missing")
        self.services.environment["folders"] = [*folders, path]
        return path

    def rename(self, name: str, new_name: str) -> None:
        if ":" in new_name:
            raise ValueError("Use a leaf name for rename")
        path = self.services.path(name)
        self.services.move_folder(path, path.rsplit(":", 1)[0] + ":" + new_name)

    def move(self, name: str, parent: str) -> None:
        path = self.services.path(name)
        self.services.move_folder(path, self.services.path(parent) + ":" + path.rsplit(":", 1)[-1])


def install_api(services: DocumentServices) -> ModuleType:
    """Called only in the explicitly launched Procedure worker (or tests)."""
    api = ModuleType("mds", "Materials Data Studio canonical document API")
    # Canonical CurveFit lazily imports this trusted implementation. Preload it
    # before the worker enables the file-I/O guard; do not expand user imports.
    from materials_data_studio.tools import graph_fit, igor_context, igor_transforms  # noqa: F401
    from materials_data_studio.tools.igor_procedure_runtime import IgorAPI
    igor = IgorAPI(services)

    @contextmanager
    def transaction(_title: str = "Python transaction"):
        before, context = services.document.current.clone(), services.context.copy()
        try:
            yield
        except BaseException:
            services.document.current, services.context = before, context
            raise

    api.wave = WaveAPI(services)  # type: ignore[attr-defined]
    api.graph = GraphAPI(services)  # type: ignore[attr-defined]
    api.document = services.document  # type: ignore[attr-defined]
    api.folder = FolderAPI(services)  # type: ignore[attr-defined]
    api.selection = services.context  # type: ignore[attr-defined]
    api.transaction = transaction  # type: ignore[attr-defined]
    api.igor = igor  # type: ignore[attr-defined]
    api.command = igor.command  # type: ignore[attr-defined]
    sys.modules["mds"] = api
    return api
