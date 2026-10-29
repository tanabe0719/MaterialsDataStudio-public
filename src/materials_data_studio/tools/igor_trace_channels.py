"""Wave-driven trace appearance: canonical references and vector drawing."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from materials_data_studio.tools.command_language import reference, split
from materials_data_studio.tools.pxp_model import PxpTrace


def apply_channel(traces: list[PxpTrace], target: str | None, key: str, value: str, resolve: Any) -> None:
    """Parse a wave appearance property without interpreting arbitrary code."""
    key = key.lower()
    field = {"zcolor": "z_color", "logzcolor": "log_z_color", "zmrksize": "z_size", "zmrknum": "z_marker"}[key]
    for i, trace in enumerate(traces):
        if target and trace.name != target:
            continue
        result: Any = None
        if key == "logzcolor":
            if value not in {"0", "1"}:
                raise ValueError("logZColor must be 0 or 1")
            result = value == "1"
        elif value != "0":
            parts = split(value.strip("{}"))
            ref = resolve(reference(parts[0]).path)
            def bound(s: str) -> float | None:
                v = None if s == "*" else float(s)
                if v is not None and not np.isfinite(v):
                    raise ValueError("Channel bounds must be finite or *")
                return v
            if key == "zmrknum":
                if len(parts) != 1:
                    raise ValueError("zmrkNum requires {wave} or 0")
                result = ref
            elif key == "zcolor":
                if len(parts) not in {4, 5, 6}:
                    raise ValueError("zColor requires {wave,min,max,table[,reverse[,colorWave]]}")
                palette = parts[3]
                reverse = int(parts[4]) if len(parts) >= 5 else (trace.z_color[4] if trace.z_color else 0)
                if reverse == -1:
                    reverse = trace.z_color[4] if trace.z_color else 0
                if reverse not in {0, 1}:
                    raise ValueError("zColor reverse must be 0 or 1")
                if palette.casefold() in {"ctablergb", "cindexrgb"} and len(parts) != 6:
                    raise ValueError("Color table/index mode requires a color wave")
                result = (ref, bound(parts[1]), bound(parts[2]), palette, reverse, resolve(reference(parts[5]).path) if len(parts) == 6 else "")
            else:
                if len(parts) not in {5, 6}:
                    raise ValueError("zmrkSize requires {wave,min,max,sizeMin,sizeMax[,axis]}")
                result = (ref, bound(parts[1]), bound(parts[2]), float(parts[3]), float(parts[4]), parts[5] if len(parts) == 6 else "")
                if not np.isfinite(result[3:5]).all():
                    raise ValueError("Marker sizes must be finite")
        traces[i] = replace(trace, **{field: result})


def channel_range(values: Any, low: float | None, high: float | None, log: bool = False) -> tuple[float, float]:
    """Resolve an auto range from eligible finite points only."""
    values = np.asarray(values)
    finite = values[np.isfinite(values) & ((values > 0) if log else True)]
    if not len(finite):
        finite = np.array([1., 10.]) if log else np.array([0., 1.])
    low = float(np.min(finite)) if low is None else low
    high = float(np.max(finite)) if high is None else high
    if low > high or (log and low <= 0):
        raise ValueError("Color/size range must be ordered; logarithmic colors need positive bounds")
    return low, high if high != low else low + max(abs(low) * .01, 1.)


def point_colors(document: Any, trace: PxpTrace) -> Any:
    """Return RGBA values retaining masks and direct/table/index semantics."""
    from matplotlib import colormaps
    from matplotlib.colors import LogNorm, Normalize

    from materials_data_studio.tools.pxp_style import colormap

    spec = trace.z_color
    assert spec is not None
    ref, low, high, palette, reverse, table = spec
    wave = document.wave(ref)
    if wave is None:
        raise ValueError(f"Missing color wave: {ref}")
    values = np.asarray(wave.data, dtype=float)
    mode = palette.casefold()
    if mode == "directrgb":
        rgba = values / (255 if wave.data.dtype == np.uint8 else 65535)
        if values.shape[1] == 3:
            rgba = np.column_stack((rgba, np.ones(len(values))))
        return np.nan_to_num(np.clip(rgba, 0, 1))
    lo, hi = channel_range(values, low, high, trace.log_z_color)
    norm = LogNorm(lo, hi, clip=True) if trace.log_z_color else Normalize(lo, hi, clip=True)
    if mode in {"ctablergb", "cindexrgb"}:
        color_wave = document.wave(table)
        colors = np.asarray(color_wave.data, dtype=float) / 65535
        if reverse:
            colors = colors[::-1]
        if mode == "cindexrgb":
            offset, delta = color_wave.dimension_scaling[0]
            indices = np.clip(np.rint((np.nan_to_num(values, nan=offset) - offset) / delta), 0, len(colors) - 1).astype(int)
        else:
            indices = np.rint(norm(np.nan_to_num(values, nan=lo)) * (len(colors) - 1)).astype(int)
        rgba = colors[indices]
        if rgba.shape[1] == 3:
            rgba = np.column_stack((rgba, np.ones(len(values))))
    else:
        rgba = colormaps[colormap(palette, reverse=bool(reverse))](norm(values))
    rgba[~np.isfinite(values) | ((values <= 0) if trace.log_z_color else False), 3] = 0
    return rgba


def render_channels(ax: Any, document: Any, trace: PxpTrace, x: Any, y: Any, *, mode: int, scale: float) -> Any:
    """Draw point colors/sizes/markers and colored line segments as vector Artists."""
    from matplotlib.collections import LineCollection

    from materials_data_studio.tools.pxp_markers import marker_path
    from materials_data_studio.tools.pxp_style import igor_rgb

    selection = slice(trace.point_range[0], trace.point_range[1]+1) if trace.point_range else slice(None)
    colors = point_colors(document, trace)[selection] if trace.z_color else igor_rgb(trace.color_igor)
    last = None
    if mode not in {1, 2, 3, 5, 8}:
        points = np.column_stack((x, y))
        segments = np.stack((points[:-1], points[1:]), axis=1)
        lc = LineCollection(list(segments), colors=colors[:-1] if trace.z_color else colors,
                            linewidths=(trace.line_width or 1) * scale)
        lc.set_gid(f"trace:{trace.name}")
        ax.add_collection(lc)
        ax.update_datalim(points)
        ax.autoscale_view()
        last = lc
    sizes = np.full(len(x), trace.marker_size or 3., dtype=float)
    if trace.z_size:
        ref, low, high, a, b, axis = trace.z_size
        values = np.asarray(document.wave(ref).data, dtype=float)[selection]
        if axis:
            horizontal = axis in {"bottom", "top"} or axis == trace.x_axis
            transform = ax.get_xaxis_transform() if horizontal else ax.get_yaxis_transform()
            zero = transform.transform(np.zeros((len(x), 2)))
            points = np.zeros((len(x), 2))
            points[:, 0 if horizontal else 1] = values
            pixels = np.abs(transform.transform(points) - zero)[:, 0 if horizontal else 1]
            sizes = pixels * 72 / ax.figure.dpi / scale
        else:
            lo, hi = channel_range(values, low, high)
            sizes = np.clip(a + (values - lo) * (b - a) / (hi - lo), 1, 400)
    numbers = np.asarray(document.wave(trace.z_marker).data)[selection] if trace.z_marker else np.full(len(x), trace.marker or 0)
    eligible = np.isfinite(numbers) & np.isfinite(sizes)
    if mode in {2, 3, 4, 8}:
        for number in np.unique(np.clip(numbers[eligible], 0, 62).astype(int)):
            mask = eligible & (np.clip(np.nan_to_num(numbers), 0, 62).astype(int) == number)
            color = colors[mask] if trace.z_color else colors
            last = ax.scatter(np.asarray(x)[mask], np.asarray(y)[mask], s=((2 * sizes[mask] + 1) * scale)**2,
                              marker="." if mode == 2 else marker_path(int(number)), c=color,
                              linewidths=(trace.marker_thickness or .5) * scale)
            last.set_gid(f"trace:{trace.name}")
    if trace.color_scale and trace.z_color:
        from matplotlib import colormaps
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import ListedColormap, LogNorm, Normalize

        from materials_data_studio.tools.pxp_style import colormap
        ref, low, high, palette, reverse, _ = trace.z_color
        if palette.casefold() != "directrgb":
            lo, hi = channel_range(document.wave(ref).data, low, high, trace.log_z_color)
            if palette.casefold() in {"ctablergb", "cindexrgb"}:
                table = document.wave(trace.z_color[5])
                cmap = ListedColormap(np.clip(table.data / 65535, 0, 1))
                if reverse:
                    cmap = cmap.reversed()
                if palette.casefold() == "cindexrgb":
                    lo, hi = sorted((table.coordinate(0)[0], table.coordinate(0)[-1]))
            else:
                cmap = colormaps[colormap(palette, reverse=bool(reverse))]
            norm = LogNorm(lo, hi) if trace.log_z_color else Normalize(lo, hi)
            bar = ax.figure.colorbar(ScalarMappable(norm, cmap), ax=ax)
            bar.ax.set_gid("colorbar:trace:" + trace.name)
    return last
