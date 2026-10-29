"""Centralized Igor-to-Matplotlib style translations."""

from __future__ import annotations

import hashlib
import re
from typing import Any

import numpy as np
from matplotlib import colormaps
from matplotlib.colors import LinearSegmentedColormap, ListedColormap

from materials_data_studio.tools.pxp_recreation import formatted_text

MARKERS = {
    0: "+", 1: "x", 2: "*", 5: "s", 6: "^", 7: "D", 8: "o",
    9: "_", 10: "|", 16: "s", 17: "^", 18: "D", 19: "o",
    22: "v", 23: "v", 25: "d", 26: "d", 28: "d", 29: "d",
    45: "<", 46: "<", 48: ">", 49: ">", 51: "p", 52: "p",
    54: "h", 55: "h",
}
FILLED_MARKERS = {14, 15, 16, 17, 18, 19, 23, 26, 29, 32, 34, 36, 38, 46, 49, 52, 55, 58, 60, 62}

# An independently specified palette matched to the reference gradient, not a
# copy of WaveMetrics' proprietary LUT. Keep the approximation visible in issues.
_VIOLET_ORANGE_YELLOW = LinearSegmentedColormap.from_list(
    "mds_violet_orange_yellow",
    [(0, "#090522"), (0.17, "#3f0561"), (0.33, "#7d1a5f"),
     (0.46, "#a24139"), (0.56, "#b36313"), (0.65, "#bf8a0f"),
     (0.72, "#c8ad15"), (0.79, "#d3c52c"), (0.85, "#dcd947"),
     (0.90, "#e7e972"), (0.95, "#f4f6b3"), (1, "#ffffff")], N=256,
)
if _VIOLET_ORANGE_YELLOW.name not in colormaps:
    colormaps.register(_VIOLET_ORANGE_YELLOW)
    colormaps.register(_VIOLET_ORANGE_YELLOW.reversed())
COLORMAPS = {
    "VioletOrangeYellow": "mds_violet_orange_yellow",
    "Rainbow": "rainbow", "Grays": "gray", "Spectrum": "nipy_spectral",
    "RedWhiteBlue": "bwr", "Viridis": "viridis",
    "BlueRedGreen": "brg", "PlanetEarth": "gist_earth", "Terrain": "terrain",
    "Grays256": "gray", "Rainbow256": "rainbow", "YellowHot256": "hot",
    "BlueHot256": "magma", "BlueRedGreen256": "brg",
    "RedWhiteBlue256": "bwr", "PlanetEarth256": "gist_earth",
    "Terrain256": "terrain", "Grays16": "gray", "Rainbow16": "rainbow",
    "Red": "Reds", "Green": "Greens", "Blue": "Blues", "Cyan": "cool",
    "Magenta": "RdPu", "Yellow": "YlOrBr", "Copper": "copper",
    "Gold": "YlOrBr", "CyanMagenta": "cool", "RedWhiteGreen": "PiYG",
    "BlueBlackRed": "seismic", "Geo": "gist_earth", "Geo32": "gist_earth",
    "LandAndSea": "terrain", "LandAndSea8": "terrain", "Relief": "terrain",
    "Relief19": "terrain", "PastelsMap": "Pastel1", "PastelsMap20": "Pastel1",
    "Bathymetry9": "ocean", "BlackBody": "afmhot", "SpectrumBlack": "nipy_spectral",
    "Cycles": "hsv", "Fiddle": "gist_ncar", "Pastels": "Pastel1",
    "RainbowCycle": "hsv", "Rainbow4Cycles": "hsv",
    "BlueGreenOrange": "turbo", "BrownViolet": "PuOr", "ColdWarm": "coolwarm",
    "Mocha": "copper", "SeaLandAndFire": "gist_earth",
    "GreenMagenta16": "PiYG", "EOSOrangeBlue11": "RdBu",
    "EOSSpectral11": "Spectral", "dBZ14": "turbo", "dBZ21": "turbo",
    "Web216": "hsv", "Mud": "gist_earth", "Classification": "tab20",
    "Turbo": "turbo", "RedYellowGreen": "RdYlGn", "RedYellowGreen10": "RdYlGn",
}


def igor_rgb(color: tuple[int, int, int] | None) -> tuple[float, float, float]:
    if color is None:
        return (1.0, 0.0, 0.0)
    return tuple(max(0, min(65535, component)) / 65535 for component in color)  # type: ignore[return-value]


def marker(number: int | None) -> str:
    return MARKERS.get(number or 0, "o")


def colormap(name: str, *, reverse: bool = False) -> str:
    if name == "Rainbow":
        mapped = sampled_colormap(native_rainbow())
        return f"{mapped}_r" if reverse else mapped
    mapped = COLORMAPS.get(name, name if name in colormaps else "viridis")
    return f"{mapped}_r" if reverse else mapped


def native_rainbow() -> np.ndarray:
    """Independent piecewise RGB ramps, validated against synthetic 10.03 output.

    No WaveMetrics palette file or source code is shipped. Only arithmetic
    ramps between primary/secondary colors, including native duplicated joins.
    This contract is the 100-entry Rainbow, not every similarly named palette.
    """
    vertices = np.array([[65535, 0, 0], [65535, 65535, 0], [0, 65535, 0],
                         [0, 65535, 65535], [0, 0, 65535], [32767, 0, 65535]])
    return np.concatenate([np.floor(np.linspace(a, b, n) + 1e-8).astype(np.uint16)
                           for a, b, n in zip(vertices[:-1], vertices[1:], [22, 22, 23, 22, 11], strict=True)])


def freeze_palette(image: Any) -> Any:
    from dataclasses import replace
    if image.color_table == "Rainbow":
        return replace(image, color_lut=tuple(tuple(map(int, row)) for row in native_rainbow()), lut_name=image.color_table,
                       lut_provenance="independent RGB ramps; Igor 10.03 Rainbow100 numerical reference")
    resolved = colormaps[colormap(image.color_table)]
    colors = np.rint(resolved(np.linspace(0, 1, resolved.N))[:, :3] * 65535).astype(np.uint16)
    approximate = image.color_table not in colormaps and image.color_table != "Viridis"
    provenance = f"Matplotlib {resolved.name}; " + ("native palette approximation" if approximate else "explicit RGB16 samples")
    return replace(image, color_lut=tuple(tuple(map(int, row)) for row in colors), lut_name=image.color_table, lut_provenance=provenance)


def has_palette(image: Any) -> bool:
    return bool(image.color_lut) and image.lut_name in {"", image.color_table}


def bind_palette_waves(waves: Any, graphs: Any) -> Any:
    """Resolve native RGB-table waves to an immutable content-addressed snapshot."""
    from dataclasses import replace

    from materials_data_studio.tools.pxp_model import PxpIssue
    paths = {w.igor_path: w for w in waves}
    names: dict[str, Any] = {}
    for wave in waves:
        names[wave.name] = None if wave.name in names else wave
    result = []
    for graph in graphs:
        images, resolved, issues = [], [], list(graph.issues)
        for image in graph.images:
            table = paths.get(image.color_table) or names.get(image.color_table.strip("'"))
            if table is not None:
                data = table.data
                if (data.ndim != 2 or data.shape[1] not in {3,4} or len(data) > 65536
                        or data.dtype.kind not in "iuf" or not np.isfinite(data).all()
                        or np.any((data < 0) | (data > 65535))):
                    issues.append(PxpIssue("ModifyImage", "Invalid RGB16 palette wave", code="igor.image.invalid_lut",
                                           layer="renderer", property="ctab", severity="error"))
                else:
                    name = sampled_colormap(data)
                    resolved.append(image.color_table)
                    image = replace(image, color_table=name, lut_name=name,
                                    color_lut=tuple(tuple(int(v) for v in row) for row in data),
                                    lut_provenance="RGB16 wave from PXP; immutable snapshot (fractional channels quantized within 1 LSB)")
            images.append(image)
        issues = [i for i in issues if not (i.code == "igor.image.palette_fallback" and any(ref in i.message for ref in resolved))]
        result.append(replace(graph, images=tuple(images), issues=tuple(issues)))
    return tuple(result)


def image_colormap(image: Any, *, name: str | None = None, reverse: bool | None = None) -> Any:
    use_name = image.color_table if name is None else name
    flipped = image.reverse if reverse is None else reverse
    if has_palette(image) and use_name == image.color_table:
        colors = np.asarray(image.color_lut)
        if colors.ndim != 2 or colors.shape[1] not in {3,4} or len(colors) > 65536 or not np.isfinite(colors).all() or np.any((colors < 0) | (colors > 65535)):
            raise ValueError("Invalid persisted RGB16 color LUT")
        return ListedColormap(colors[::-1] / 65535 if flipped else colors / 65535)
    return colormaps[colormap(use_name, reverse=flipped)].copy()


def sampled_colormap(data: Any) -> str:
    colors = np.clip(np.asarray(data, dtype=float) / 65535., 0, 1)
    name = "mds_h5xp_" + hashlib.sha256(colors.tobytes()).hexdigest()[:24]
    if name not in colormaps:
        palette = ListedColormap(colors, name=name)
        colormaps.register(palette)
        colormaps.register(palette.reversed())
    return name


def restore_sampled_colormaps(waves: Any, graphs: Any) -> None:
    required = {image.color_table for graph in graphs for image in graph.images
                if image.color_table.startswith("mds_h5xp_") and image.color_table not in colormaps}
    if not required:
        return
    for wave in waves:
        if wave.data.ndim == 2 and wave.shape[1] in {3, 4} and wave.data.dtype.kind in "fiu":
            colors = np.clip(wave.data.astype(float) / 65535., 0, 1)
            name = "mds_h5xp_" + hashlib.sha256(colors.tobytes()).hexdigest()[:24]
            if name in required:
                sampled_colormap(wave.data)
                required.remove(name)
                if not required:
                    break


def legend_labels(raw: str, trace_names: list[str]) -> list[str]:
    """Use Igor's visible legend lines, falling back to trace names."""
    lines = raw.replace("\\\\", "\\").replace("\\r", "\n").splitlines()
    labels = [formatted_text(re.sub(r"\\s\([^)]*\)", "", line)).strip() for line in lines]
    return [labels[index] if index < len(labels) and labels[index] else name
            for index, name in enumerate(trace_names)]
