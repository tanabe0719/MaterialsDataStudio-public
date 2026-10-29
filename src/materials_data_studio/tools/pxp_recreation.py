"""Whitelist parser for Igor window recreation commands; never executes text."""

from __future__ import annotations

import re
from dataclasses import replace
from math import isfinite, isnan
from typing import Any

from materials_data_studio.tools.pxp_model import (
    PxpAnnotation,
    PxpAnnotationStyle,
    PxpAxis,
    PxpAxisStyle,
    PxpContour,
    PxpDrawing,
    PxpGraph,
    PxpGraphStyle,
    PxpImage,
    PxpIssue,
    PxpLayoutItem,
    PxpTrace,
)

_WINDOW = re.compile(r"^Window\s+([^()\s]+)\(\)\s*:\s*(Graph|Table|Layout)")
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_RGB = re.compile(r"^\((\d+),(\d+),(\d+)\)$")
_STYLE = re.compile(r"^([A-Za-z][\w]*)(?:\(([^()]*)\))?=(.*)$")
_FORMAT = re.compile(r"\\(?:F'[^']*'|Zr\d{3}|\]0|Z\d{1,2}|f\d{1,2}|[sK]\([^)]*\)|s[ab][+-]\d{2}|[MBu])")
_SUPERSCRIPT = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")
_SUBSCRIPT = str.maketrans("0123456789+-", "₀₁₂₃₄₅₆₇₈₉₊₋")


def formatted_text(raw: str) -> str:
    """Keep visible Igor text while removing safe, known formatting escapes."""
    text = (
        raw.replace("\\\\", "\\").replace("\\r\\n", "\n").replace("\\r", "\n").replace("\\n", "\n")
    )
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\\S([0-9+-]+)(?:\\M|$)", lambda match: match[1].translate(_SUPERSCRIPT), text)
    text = re.sub(r"\\B([0-9+-]+)(?:\\M|$)", lambda match: match[1].translate(_SUBSCRIPT), text)
    text = _FORMAT.sub("", text)
    text = re.sub(r"\\[A-Za-z]\d*", "", text)
    return text


def _parts(value: str) -> list[str]:
    """Split comma-separated assignments outside Igor tuples and braces."""
    result: list[str] = []
    level = 0
    quoted = False
    start = 0
    for index, char in enumerate(value):
        if char == '"':
            quoted = not quoted
        if not quoted:
            level += (char in "({") - (char in ")}")
            if char == "," and level == 0:
                result.append(value[start:index].strip())
                start = index + 1
    result.append(value[start:].strip())
    return result


def _value_pair(text: str) -> tuple[float, float] | None:
    try:
        first, second = text.strip("{} ").split(",", 1)
        return float(first), float(second)
    except ValueError:
        return None


def _quote(text: str) -> str:
    match = _QUOTED.search(text)
    return decode_literal(match[1]) if match else ""


def decode_literal(text: str) -> str:
    """Decode a SINGLE Igor string-literal layer, never Python unicode escapes."""
    return re.sub(
        r'\\([\\"rnt])', lambda m: {"r": "\r", "n": "\n", "t": "\t"}.get(m[1], m[1]), text
    )


def _replace_axis(axes: list[PxpAxis], name: str, **changes: object) -> None:
    for index, axis in enumerate(axes):
        if axis.name == name:
            axes[index] = replace(axis, **changes)
            return
    axes.append(replace(PxpAxis(name), **changes))


def _replace_trace(traces: list[PxpTrace], name: str | None, **changes: object) -> None:
    name = name.strip("'") if name is not None else None
    for index, trace in enumerate(traces):
        if name is None or trace.name == name:
            traces[index] = replace(trace, **changes)


def _axis_style(
    axes: list[PxpAxis], settings: dict[str, object], target: str | None, **changes: object
) -> None:
    if "label_lateral_position" in changes:
        changes["label_lateral_fraction"] = None
    if target:
        for index, axis in enumerate(axes):
            if axis.name == target:
                axes[index] = replace(axis, style=replace(axis.style, **changes))
                return
        axes.append(PxpAxis(target, style=replace(PxpAxisStyle(), **changes)))
    else:
        graph_style = settings.get("style", PxpGraphStyle())
        assert isinstance(graph_style, PxpGraphStyle)
        settings["style"] = replace(
            graph_style,
            axis_defaults=replace(graph_style.axis_defaults, **changes),
        )
        for index, axis in enumerate(axes):
            axes[index] = replace(axis, style=replace(axis.style, **changes))


def _rgb(value: str) -> tuple[int, int, int]:
    match = _RGB.match(re.sub(r"\s+", "", value))
    if not match:
        raise ValueError(value)
    components = tuple(int(component) for component in match.groups())
    if any(component > 65535 for component in components):
        raise ValueError("RGB components must be in 0..65535")
    return components  # type: ignore[return-value]


def _graph_dimension(value: str) -> tuple[float | None, float | None]:
    value = value.strip()
    if value.startswith("{Aspect,") and value.endswith("}"):
        return None, float(value[8:-1])
    points = float(value)
    return (points if points > 0 else None), None


def _wave_reference(reference: str, folder: str | None) -> str:
    if not reference.strip():
        return ""
    if (
        folder == "root"
        and reference.startswith("'")
        and reference.endswith("'")
        and reference.count("'") == 2
    ):
        return reference
    reference = re.sub(r"'([^']*)'", r"\1", reference.strip())
    if reference.startswith(":"):
        depth = len(reference) - len(reference.lstrip(":"))
        parts = (folder or "__mds_unresolved_folder__").split(":")
        base = parts[:max(1, len(parts)-depth+1)]
        return ":".join([*base, reference.lstrip(":")])
    if reference.startswith("root:") or folder == "root":
        return reference
    return (folder or "__mds_unresolved_folder__") + ":" + reference


def _trace(
    command: str,
    args: str,
    traces: list[PxpTrace],
    issues: list[PxpIssue],
    folder: str | None = "root",
) -> None:
    flags = re.match(r"^(?:/\w+(?:=\([^)]*\)|=[^/\s]+)?\s*)*", args)
    remainder = args[flags.end() :].strip() if flags else args
    if not remainder:
        return
    pair = re.split(r"\s+vs\s+", remainder, maxsplit=1)
    x_wave = _wave_reference(pair[1], folder) if len(pair) == 2 else None
    axis_match = re.search(r"/([LR])(?:=([\w]+))?(?=/|\s|$)", args)
    axis = (
        (axis_match[2] or ("right" if axis_match[1] == "R" else "left")) if axis_match else "left"
    )
    x_match = re.search(r"/([BT])(?:=([\w]+))?(?=/|\s|$)", args)
    x_axis = (x_match[2] or ("top" if x_match[1] == "T" else "bottom")) if x_match else "bottom"
    for y_wave in _parts(pair[0]):
        bounds = re.search(r"\[(\d+),(\d+)\]$", y_wave)
        point_range = (int(bounds[1]), int(bounds[2])) if bounds else None
        if bounds:
            y_wave = y_wave[:bounds.start()]
        name = y_wave.strip("'").rsplit(":", 1)[-1]
        instance = sum(t.name.split("#", 1)[0] == name for t in traces)
        traces.append(
            PxpTrace(
                name + (f"#{instance}" if instance else ""),
                _wave_reference(y_wave, folder),
                x_wave,
                axis,
                x_axis=x_axis, point_range=point_range,
            )
        )


def _modify_graph(
    args: str | tuple[tuple[str, str | None, str], ...],
    traces: list[PxpTrace],
    axes: list[PxpAxis],
    settings: dict[str, object],
    issues: list[PxpIssue],
    folder: str | None = "root",
) -> None:
    accepted = {
        "mode": "mode",
        "marker": "marker",
        "msize": "marker_size",
        "lsize": "line_width",
        "rgb": "color_igor",
        "mrkfillrgb": "marker_fill_igor",
        "mrkstrokergb": "marker_stroke_igor",
        "mrkthick": "marker_thickness",
        "opaque": "opaque",
        "usemrkstrokergb": "use_marker_stroke",
        "lstyle": "line_style",
        "loptions": "line_cap",
    }
    axis_numbers = {
        "tick": ("tick", int),
        "mirror": ("mirror", int),
        "axthick": ("thickness", float),
        "btlen": ("major_length", float),
        "stlen": ("minor_length", float),
        "btthick": ("major_thickness", float),
        "stthick": ("minor_thickness", float),
        "minor": ("minor_enabled", bool),
        "ftlen": ("medium_length", float),
        "ftthick": ("medium_thickness", float),
        "log": ("log", int),
        "grid": ("grid", int),
        "fsize": ("font_size", float),
        "lblmargin": ("label_margin", float),
        "fstyle": ("font_style", int),
        "axoffset": ("axis_offset", float),
        "lblpos": ("label_position", float),
        "lblposmode": ("label_position_mode", int),
        "lbllatpos": ("label_lateral_position", float),
        "lblrot": ("label_rotation", float),
        "tklblrot": ("tick_rotation", float),
        "nolabel": ("no_label", int),
        "tickunit": ("tick_unit", int),
        "zaptz": ("trim_trailing_zero", bool),
        "reverse": ("reverse", bool),
        "nticks": ("tick_count", int),
        "sep": ("tick_separation", float),
        "standoff": ("standoff", bool),
        "zero": ("zero_line", int),
        "gridhair": ("grid_hair", int),
        "lowtrip": ("low_trip", float),
        "hightrip": ("high_trip", float),
        "notation": ("notation", int),
        "zisz": ("zero_is_zero", bool),
        "ttlen": ("subminor_length", float),
        "ttthick": ("subminor_thickness", float),
        "gridstyle": ("grid_style", int),
        "lbllinespacing": ("label_line_spacing", float),
    }
    axis_colors = {
        "axrgb": "axis_color",
        "alblrgb": "label_color",
        "tickrgb": "tick_color",
        "tlblrgb": "tick_label_color",
        "gridrgb": "grid_color",
    }
    ignored = {
        "lblpos",
        "lbllatpos",
        "freepos",
        "axoffset",
        "gridstyle",
        "zeroline",
        "usecomma",
        "ttlen",
        "ttthick",
        "axisontop",
        "useTSep".lower(),
    }
    for part in _parts(args) if isinstance(args, str) else args:
        if isinstance(part, str):
            match = _STYLE.match(part)
            if not match:
                issues.append(PxpIssue("ModifyGraph", f"Unsupported option: {part[:80]}"))
                continue
            key, target, value = match.groups()
        else:
            key, target, value = part
        lower = key.lower()
        if lower == "dateinfo":
            try:
                values = tuple(int(v) for v in _parts(value.strip("{}")))
                if len(values) != 3 or values[0] not in range(3) or values[1] not in range(3) or values[2] not in range(3):
                    raise ValueError("dateInfo supports {0..2,0..2,0..2}")
                if target:
                    _axis_style(axes, settings, target, date_info=values)
                else:
                    for axis in list(axes):
                        _axis_style(axes, settings, axis.name, date_info=values)
            except ValueError as exc:
                issues.append(PxpIssue("ModifyGraph", str(exc)))
        elif lower == "swapxy":
            settings["swap_xy"] = value.strip() == "1"
        elif lower in {"gfsize", "gfmult", "gfrelsize", "gmsize"}:
            try:
                size = float(value)
                if target or not isfinite(size) or size < -1 or size > 400:
                    raise ValueError("Invalid graph typography value")
                if lower == "gfsize" and size not in {-1, 0} and not 3 <= size <= 99:
                    raise ValueError("gfSize is -1, 0 (auto), or 3..99 pt")
                if lower == "gfrelsize" and not 0 <= size <= 100:
                    raise ValueError("gfRelSize is a percentage in 0..100")
                if lower == "gfmult":
                    size = min(400, max(25, size))
                style = settings.get("style", PxpGraphStyle())
                assert isinstance(style, PxpGraphStyle)
                field_name = {"gfsize": "font_size", "gfmult": "font_multiplier", "gfrelsize": "relative_font_size", "gmsize": "marker_size"}[lower]
                changes: dict[str, object] = {field_name: size}
                if lower == "gfsize":
                    changes["relative_font_size"] = None
                elif lower == "gfrelsize":
                    changes["font_size"] = None
                settings["style"] = replace(style, **changes)
            except ValueError:
                issues.append(PxpIssue("ModifyGraph", "Unsupported gfSize: use a graph default of 3..99 pt"))
        elif lower == "tickzap":
            try:
                values = tuple(float(part) for part in _parts(value.strip("{}")))
                if len(values) > 3 or not all(isfinite(p) for p in values):
                    raise ValueError("Use at most three finite values")
                _axis_style(axes, settings, target, tick_zap=values)
            except ValueError:
                issues.append(PxpIssue("ModifyGraph", "Invalid tickZap"))
        elif lower == "tickenab":
            pair = _value_pair(value)
            if pair is None or any(isnan(item) for item in pair) or pair[0] > pair[1]:
                issues.append(PxpIssue("ModifyGraph", "Invalid tickEnab: use ordered numerical bounds (infinite bounds allowed)"))
            else:
                _axis_style(axes, settings, target, tick_enable=pair)
        elif lower == "axisenab" and target:
            enable = _value_pair(value)
            if enable:
                _replace_axis(axes, target, enable=enable)
        elif lower == "freepos" and target:
            try:
                if value.startswith("{"):
                    location, cross = _parts(value.strip("{} "))
                    if not re.fullmatch(r"[A-Za-z]\w*", cross):
                        raise ValueError("Invalid crossing axis")
                    position: float | tuple[float, str] = (float(location), cross)
                else:
                    position = float(value)
                if not isfinite(position[0] if isinstance(position, tuple) else position):
                    raise ValueError("Nonfinite position")
                _replace_axis(axes, target, free_position=position)
            except ValueError:
                issues.append(PxpIssue("ModifyGraph", f"Invalid {key} position"))
        elif lower in {"width", "height"}:
            try:
                points, aspect = _graph_dimension(value)
                style = settings.get("style", PxpGraphStyle())
                assert isinstance(style, PxpGraphStyle)
                settings["style"] = replace(
                    style,
                    **{
                        f"{lower}_points": points,
                        f"{lower}_aspect": aspect,
                    },
                )
            except ValueError:
                issues.append(PxpIssue("ModifyGraph", f"Unsupported {key} size mode"))
        elif lower == "margin":
            try:
                style = settings.get("style", PxpGraphStyle())
                assert isinstance(style, PxpGraphStyle)
                margins = dict(style.margins)
                margins[target or "all"] = float(value)
                settings["style"] = replace(style, margins=tuple(margins.items()))
            except ValueError:
                issues.append(PxpIssue("ModifyGraph", "Invalid margin"))
        elif lower == "gbrgb":
            try:
                style = settings.get("style", PxpGraphStyle())
                assert isinstance(style, PxpGraphStyle)
                settings["style"] = replace(style, plot_background=_rgb(value))
            except ValueError:
                issues.append(PxpIssue("ModifyGraph", "Invalid graph background color"))
        elif lower in {"zcolor", "logzcolor", "zmrksize", "zmrknum"}:
            try:
                from materials_data_studio.tools.igor_trace_channels import apply_channel
                apply_channel(traces, target, lower, value,
                              lambda ref: _wave_reference(ref, folder) if ref.startswith("root:") or folder != "root" else "root:" + re.sub(r"'([^']*)'", r"\1", ref))
            except (ValueError, IndexError) as exc:
                issues.append(PxpIssue("ModifyGraph", str(exc), property=key))
        elif lower in accepted:
            try:
                converted: object
                if lower in {"rgb", "mrkfillrgb", "mrkstrokergb"}:
                    converted = _rgb(value)
                elif lower in {"opaque", "usemrkstrokergb"}:
                    converted = bool(int(value))
                elif lower in {"mode", "marker", "lstyle", "loptions"}:
                    converted = int(value)
                else:
                    converted = float(value)
                    if not isfinite(converted):
                        raise ValueError("Nonfinite trace style")
                    if converted < 0:
                        raise ValueError("Trace size/width cannot be negative")
                _replace_trace(traces, target, **{accepted[lower]: converted})
                # Global formatting before AppendToGraph is harmless; this parser
                # primarily supports Igor's usual post-trace ModifyGraph order.
            except ValueError:
                issues.append(PxpIssue("ModifyGraph", f"Invalid {key} value"))
        elif lower in {"offset", "muloffset"}:
            pair = _value_pair(value)
            if pair is None:
                issues.append(PxpIssue("ModifyGraph", f"Invalid {key} value"))
            else:
                _replace_trace(
                    traces, target, **{"offset" if lower == "offset" else "multiplier": pair}
                )
        elif lower == "linejoin":
            pair = _value_pair(value)
            if pair:
                _replace_trace(traces, target, line_join=int(pair[0]))
        elif lower == "gfont":
            style = settings.get("style", PxpGraphStyle())
            assert isinstance(style, PxpGraphStyle)
            settings["style"] = replace(style, font_family=value.strip().strip('"'))
        elif lower == "font":
            _axis_style(axes, settings, target, font_family=value.strip().strip('"'))
        elif lower in axis_numbers:
            try:
                field, convert = axis_numbers[lower]
                if lower in {"zero", "gridhair", "notation"}:
                    numeric = float(value)
                    if not isfinite(numeric) or not numeric.is_integer():
                        raise ValueError("An integer setting is required")
                    converted = int(numeric)
                else:
                    converted = (bool(float(value)) if lower in {"standoff", "zisz"} else bool(int(value))) if convert is bool else convert(value)
                if isinstance(converted, float) and not isfinite(converted):
                    raise ValueError("Nonfinite axis style")
                if lower == "standoff" and float(value) not in {0, 1}:
                    raise ValueError("standoff must be 0 or 1")
                if lower == "zisz" and float(value) not in {0, 1}:
                    raise ValueError("ZisZ must be 0 or 1")
                if lower == "zero" and not 0 <= converted <= 18:
                    raise ValueError("Supported zero-line styles are 0..18")
                if lower == "gridhair" and converted not in {0, 1, 2, 3}:
                    raise ValueError("gridHair must be 0..3")
                if lower == "notation" and converted not in {0, 1}:
                    raise ValueError("notation must be 0 or 1")
                if lower == "gridstyle" and converted not in {0, 1, 2, 3, 4, 5}:
                    raise ValueError("gridStyle must be 0..5")
                if lower == "fsize" and converted != 0 and not 3 <= converted <= 99:
                    raise ValueError("fSize must be auto (0) or 3..99 pt")
                if lower in {"lowtrip", "hightrip"} and converted < 0:
                    raise ValueError("Trip thresholds cannot be negative")
                if (
                    lower
                    in {
                        "axthick",
                        "btlen",
                        "stlen",
                        "btthick",
                        "stthick",
                        "ftlen",
                        "ftthick",
                        "ttlen",
                        "ttthick",
                        "fsize",
                        "nticks",
                    }
                    and converted < 0
                ):
                    raise ValueError("Axis size/width/count cannot be negative")
                _axis_style(axes, settings, target, **{field: converted})
            except ValueError as exc:
                detail = f": {exc}" if lower in {"standoff", "zero", "gridhair", "lowtrip", "hightrip", "notation", "zisz"} else ""
                issues.append(PxpIssue("ModifyGraph", f"Invalid {key} value{detail}"))
        elif lower in axis_colors:
            try:
                colors: dict[str, object] = {axis_colors[lower]: _rgb(value)}
                if lower == "tickrgb":
                    # Native tickRGB explicitly sets both tick classes; do not
                    # retain stale MDS-only per-class overrides over that edit.
                    colors.update(major_tick_color=None, minor_tick_color=None)
                _axis_style(axes, settings, target, **colors)
            except ValueError:
                issues.append(PxpIssue("ModifyGraph", f"Invalid {key} color"))
        elif lower in {"mantick", "manminor"}:
            try:
                values = value.strip(" {}")
                if lower == "mantick" and value.strip() == "0":
                    _axis_style(axes, settings, target, manual_ticks=None)
                elif lower == "mantick":
                    canon, increment, exponent, digits = [
                        float(item) for item in values.split(",")[:4]
                    ]
                    _axis_style(
                        axes,
                        settings,
                        target,
                        manual_ticks=(canon, increment, int(exponent), int(digits)),
                    )
                else:
                    count, emphasize = [int(item) for item in values.split(",")[:2]]
                    _axis_style(axes, settings, target, manual_minor=(count, emphasize))
            except ValueError:
                issues.append(PxpIssue("ModifyGraph", f"Invalid {key} ticks"))
        elif lower == "userticks":
            values = _parts(value.strip(" {}"))
            if value.strip() == "0":
                _axis_style(axes, settings, target, user_ticks=None)
            elif len(values) == 2:
                _axis_style(
                    axes,
                    settings,
                    target,
                    user_ticks=tuple(_wave_reference(v, folder) for v in values),
                )
            else:
                issues.append(PxpIssue("ModifyGraph", "Invalid user tick wave pair"))
        elif lower in ignored:
            issues.append(PxpIssue("ModifyGraph", f"Not reproduced: {key}",property=key,
                                   raw=f"ModifyGraph {key}" + (f"({target})" if target else "") + f"={value}"))
        else:
            issues.append(PxpIssue("ModifyGraph", f"Unsupported option: {key}",property=key,
                                   raw=f"ModifyGraph {key}" + (f"({target})" if target else "") + f"={value}"))


def _modify_image(
    args: str | tuple[str, tuple[tuple[str, str], ...]],
    images: list[PxpImage],
    issues: list[PxpIssue],
) -> None:
    if isinstance(args, str):
        name, _, setting = args.partition(" ")
        assignments = tuple(
            (part.partition("=")[0], part.partition("=")[2])
            for part in _parts(setting.strip().lstrip(","))
        )
    else:
        name, assignments = args
    for index, image in enumerate(images):
        if image.wave != name and image.wave.rsplit(":", 1)[-1] != name:
            continue
        for key, value in assignments:
            key = key.strip().lower()
            value = value.strip()
            if key == "log":
                image = replace(image, log_color=value == "1")
            elif key == "ctab":
                parts = _parts(value.strip(" {}"))
                if len(parts) == 4:
                    try:
                        old_range = image.color_range or (None, None)
                        low = (
                            old_range[0]
                            if parts[0] == ""
                            else None
                            if parts[0] == "*"
                            else float(parts[0])
                        )
                        high = (
                            old_range[1]
                            if parts[1] == ""
                            else None
                            if parts[1] == "*"
                            else float(parts[1])
                        )
                        image = replace(
                            image,
                            color_range=(low, high),
                            color_table=parts[2].strip("'\"") or image.color_table,
                            reverse=image.reverse if parts[3] in {"", "-1"} else parts[3] == "1",
                        )
                    except ValueError:
                        issues.append(PxpIssue("ModifyImage", "Invalid color table range"))
            elif key in {"minrgb", "maxrgb", "nanrgb"}:
                try:
                    color: tuple[int, int, int] | str | None
                    color = (
                        _rgb(value)
                        if value.startswith("(")
                        else (
                            "transparent"
                            if value.lower() == "nan"
                            else "black"
                            if value == "1"
                            else None
                        )
                    )
                    image = replace(
                        image, **{{"minrgb": "min_color", "maxrgb": "max_color", "nanrgb": "nan_color"}[key]: color}
                    )
                except ValueError:
                    issues.append(PxpIssue("ModifyImage", f"Invalid {key} color"))
            elif key == "interpolate":
                image = replace(image, interpolation=value == "1")
            elif key == "hide":
                image = replace(image, hidden=value == "1")
            else:
                issues.append(PxpIssue("ModifyImage", f"Unsupported option: {key}"))
        if any(key.strip().lower() == "ctab" for key, _ in assignments):
            from materials_data_studio.tools.pxp_style import freeze_palette
            image = freeze_palette(image)
        images[index] = image


def _annotation(args: str, style: PxpAnnotationStyle) -> PxpAnnotationStyle:
    """Read annotation flags/options without interpreting their text as code."""
    changes: dict[str, object] = {}
    flags = {
        "A": "anchor",
        "X": "x_percent",
        "Y": "y_percent",
        "F": "frame",
        "G": "color",
        "LS": "line_spacing",
        "H": "symbol_width",
        "S": "frame_style",
        "O": "rotation",
    }
    for flag, field in flags.items():
        match = re.search(rf"/{flag}=?([A-Z]+|\([^)]*\)|[+-]?[\d.]+)(?=/|\s|$)", args)
        if not match:
            continue
        value = match[1]
        try:
            changes[field] = value if flag == "A" else _rgb(value) if flag == "G" else float(value)
        except ValueError:
            continue
    background = re.search(r"/B=?([0-3]|\([^)]*\))(?=/|\s|$)", args)
    if background:
        value = background[1]
        if value.startswith("("):
            changes.update(background_color=_rgb(value), background_mode=0, transparent=False)
        else:
            changes.update(background_mode=int(value), transparent=int(value) == 1)
    exterior = re.search(r"/E(?:=([0-2]))?(?=/|\s|$)", args)
    if exterior:
        changes["exterior"] = int(exterior[1] or 1)
    dimensions = re.search(r"/D=(\{[^}]*\}|[\d.]+)(?=/|\s|$)", args)
    if dimensions:
        for index, value in enumerate(dimensions[1].strip("{}").split(",")):
            if value.strip() and index < 3:
                changes[("frame_width", "shadow", "padding")[index]] = float(value) * (
                    0.5 if index == 0 else 1
                )
                if index == 2:
                    changes["padding_explicit"] = True
    numbers = {
        "width": "width",
        "height": "height",
        "heightpct": "height_percent",
        "widthpct": "width_percent",
        "fsize": "font_size",
        "vert": "vertical",
        "log": "log_axis",
    }
    for part in _parts(args):
        match = _STYLE.match(part)
        if match and match[1].lower() in numbers:
            key = match[1].lower()
            try:
                changes[numbers[key]] = (
                    bool(int(match[3])) if key in {"vert", "log"} else float(match[3])
                )
                if key == "fsize":
                    changes["inherit_graph_font"] = False
            except ValueError:
                continue
    return replace(style, **changes)


def parse_recreation(text: str) -> tuple[tuple[PxpGraph, ...], tuple[str, ...]]:
    """Read only Window Graph/Table blocks from a recreation record."""
    graphs: list[PxpGraph] = []
    tables: list[str] = []
    name = ""
    kind = ""
    traces: list[PxpTrace] = []
    axes: list[PxpAxis] = []
    images: list[PxpImage] = []
    cursors: list = []
    contours: list = []
    layout_items: list = []
    issues: list[PxpIssue] = []
    settings: dict[str, object] = {}
    legend = ""
    legend_style = PxpAnnotationStyle()
    default_font = "Arial"
    dash_patterns: dict[int, tuple[float, ...]] = {}
    annotations: list[PxpAnnotation] = []
    drawings: list[PxpDrawing] = []
    annotation_target = ""
    colorbar_target = ""
    draw_settings: dict[str, Any] = {}
    draw_layer = "UserFront"
    data_folder: str | None = "root"
    window_folder: str | None = "root"
    saved_folders: dict[str, str | None] = {}
    raw_commands: list[str] = []

    def finish() -> None:
        if kind == "Table":
            tables.append(name)
        elif kind in {"Graph", "Layout"}:
            from materials_data_studio.tools.pxp_style import COLORMAPS

            for image in images:
                if image.color_table == "VioletOrangeYellow":
                    issues.append(
                        PxpIssue(
                            "ModifyImage",
                            "VioletOrangeYellow uses an approximate reference palette",
                            code="igor.image.palette_approximation", layer="renderer", property="ctab",
                        )
                    )
                elif image.color_table and image.color_table not in COLORMAPS:
                    issues.append(
                        PxpIssue(
                            "ModifyImage", f"Unknown colormap {image.color_table}; using viridis",
                            code="igor.image.palette_fallback", layer="renderer", property="ctab",
                        )
                    )
                elif image.color_table and image.color_table not in {"Viridis", "Rainbow"} and not image.color_lut:
                    issues.append(
                        PxpIssue(
                            "ModifyImage",
                            f"{image.color_table} is approximated with {COLORMAPS[image.color_table]}",
                            code="igor.image.palette_approximation", layer="renderer", property="ctab",
                        )
                    )
            if not any(axis.name == "left" for axis in axes):
                axes.insert(0, PxpAxis("left"))
            if not any(axis.name == "bottom" for axis in axes):
                axes.append(PxpAxis("bottom"))
            status = "Partially supported" if issues else "Fully supported"
            if not traces and not images:
                status = "Data only"
            graph_style = settings.get("style", PxpGraphStyle())
            assert isinstance(graph_style, PxpGraphStyle)
            graph_style = replace(graph_style, dash_patterns=tuple(dash_patterns.items()))
            if any(t.line_style and t.line_style not in dash_patterns for t in traces):
                issues.append(
                    PxpIssue(
                        "ModifyGraph",
                        "Default dashed line patterns use reference-matched approximations",
                        code="igor.trace.dash_approximation", layer="renderer", property="lStyle",
                    )
                )
                status = "Partially supported"
            graphs.append(
                PxpGraph(
                    name,
                    tuple(traces),
                    tuple(axes),
                    tuple(images),
                    legend,
                    bool(settings.get("swap_xy")),
                    tuple(issues),
                    status,
                    graph_style,
                    legend_style,
                    tuple(annotations),
                    tuple(drawings),
                    "\n".join(raw_commands),
                    cursors=tuple(cursors), contours=tuple(contours), layout_items=tuple(layout_items), window_kind=kind,
                )
            )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if kind and not _WINDOW.match(line):
            raw_commands.append(raw_line)
        if line.startswith("DefaultFont "):
            default_font = _quote(line) or default_font
            continue
        if line.startswith("SetDashPattern "):
            try:
                number, values = line[15:].split(",", 1)
                pattern = tuple(float(v) for v in values.strip("{} ").split(","))
                if (
                    not 1 <= int(number) <= 17
                    or not 2 <= len(pattern) <= 16
                    or len(pattern) % 2
                    or any(not 1 <= value <= 127 for value in pattern)
                ):
                    raise ValueError("Invalid dash pattern")
                dash_patterns[int(number)] = pattern
            except ValueError:
                if kind:
                    issues.append(PxpIssue("SetDashPattern", "Invalid dash pattern"))
            continue
        header = _WINDOW.match(line)
        if header:
            if kind:
                finish()
                data_folder = window_folder
            window_folder = data_folder
            saved_folders = {}
            name, kind = header.groups()
            raw_commands = [raw_line]
            traces, axes, images, issues, settings, legend = [], [], [], [], {}, ""
            # Imported automatic fonts have no stored point size. Use a modest
            # 10-point fallback; explicit ModifyGraph/inline sizes always win.
            settings["style"] = PxpGraphStyle(
                font_family=default_font, automatic_font_size=10,
                font_provenance="Igor 10.03 automatic-font subset; unmeasured sizes estimated"
            )
            legend_style = PxpAnnotationStyle()
            annotations, drawings, cursors, contours, layout_items = [], [], [], [], []
            annotation_target, colorbar_target = "", ""
            draw_settings, draw_layer = {}, "UserFront"
            continue
        if not kind:
            if line.startswith("SetDataFolder root:"):
                data_folder = re.sub(r"'([^']*)'", r"\1", line[14:].strip()).rstrip(":")
            continue
        if line == "EndMacro":
            finish()
            kind = ""
            data_folder = window_folder
            continue
        if not line or line.startswith(("//", "PauseUpdate;", "Silent ")):
            continue
        command, _, args = line.partition(" ")
        args = args.strip()
        # Igor attaches /L=axis and /C/N=... directly to command names.
        if "/" in command:
            command, suffix = command.split("/", 1)
            args = f"/{suffix} {args}"
        if command == "String" and (
            saved := re.fullmatch(r"(\w+)\s*=\s*GetDataFolder\(\s*1\s*\)", args)
        ):
            saved_folders[saved[1]] = data_folder
            continue
        if command == "SetDataFolder":
            target = re.sub(r"'([^']*)'", r"\1", args).strip()
            if target in saved_folders:
                data_folder = saved_folders[target]
            elif re.fullmatch(r"root:(?:[^:;()\"\\]+:)*[^:;()\"\\]*:?", target):
                data_folder = target.rstrip(":")
            else:
                data_folder = None
                issues.append(
                    PxpIssue(
                        command, "Dynamic or unsupported data folder; relative waves are unresolved"
                    )
                )
            continue
        if kind == "Table":
            continue
        if command in {"Display", "AppendToGraph"}:
            if command == "Display":
                rect = re.search(r"/W=\(([^)]*)\)", args)
                if rect:
                    try:
                        x0, y0, x1, y1 = (float(value) for value in rect[1].split(","))
                        style = settings["style"]
                        assert isinstance(style, PxpGraphStyle)
                        if x1 > x0 and y1 > y0:
                            settings["style"] = replace(style, window_size=(x1 - x0, y1 - y0))
                    except ValueError:
                        issues.append(PxpIssue("Display", "Invalid window dimensions"))
            _trace(command, args, traces, issues, data_folder)
            for axis_flag in re.finditer(r"/([LRBT])(?:=([\w]+))?(?=/|\s|$)", args):
                side = {"L": "left", "R": "right", "B": "bottom", "T": "top"}[axis_flag[1]]
                _replace_axis(
                    axes,
                    axis_flag[2] or side,
                    side=side,
                )
        elif command in {"AppendBoxPlot", "AppendViolinPlot"}:
            try:
                from materials_data_studio.tools.command_language import Operation, parse
                from materials_data_studio.tools.igor_box import append_box
                from materials_data_studio.tools.igor_violin import append_violin
                op = parse(command + " " + args)[0]
                assert isinstance(op, Operation)
                refs, xref = op.arguments
                literal_flags: dict[str, Any] = {f.name: f.value for f in op.flags}
                graph = (append_box if command == "AppendBoxPlot" else append_violin)(PxpGraph(name, traces=tuple(traces), axes=tuple(axes)),
                                   tuple(_wave_reference(r.path, data_folder) for r in refs),
                                   _wave_reference(xref.path, data_folder) if xref else None,
                                   name=literal_flags.get("TN") or "", axis=literal_flags.get("R") or ("right" if "R" in literal_flags else literal_flags.get("L") or "left"),
                                   x_axis=literal_flags.get("T") or ("top" if "T" in literal_flags else literal_flags.get("B") or "bottom"),
                                   vertical="VERT" in literal_flags and literal_flags["VERT"] != "0", categories="CATL" in literal_flags and literal_flags["CATL"] != "0")
                traces, axes = list(graph.traces), list(graph.axes)
            except (ValueError, IndexError, AttributeError) as exc:
                issues.append(PxpIssue(command, str(exc)))
        elif command in {"ModifyBoxPlot", "ModifyViolinPlot"}:
            try:
                from materials_data_studio.tools.igor_box import modify_box
                from materials_data_studio.tools.igor_violin import modify_violin
                (modify_box if command == "ModifyBoxPlot" else modify_violin)(traces, args)
            except (ValueError, IndexError) as exc:
                issues.append(PxpIssue(command, str(exc)))
        elif command == "NewLayout":
            page_style = settings["style"]
            assert isinstance(page_style, PxpGraphStyle)
            settings["style"] = replace(page_style, window_size=(612,792))
        elif command == "AppendLayoutObject":
            try:
                from materials_data_studio.tools.command_language import operation_head
                _operation, flags, operands = operation_head(command + " " + args)
                rect = next((f.value for f in flags if f.name == "R"), "(36,36,576,396)")
                object_type, target = operands.split(None, 1)
                if object_type != "graph":
                    raise ValueError("Only graph layout objects are supported")
                bounds = tuple(float(v) for v in (rect or "").strip("()").split(","))
                if len(bounds) != 4:
                    raise ValueError("Layout rectangle needs four coordinates")
                layout_items.append(PxpLayoutItem(target, (bounds[0],bounds[1],bounds[2],bounds[3])))
            except (ValueError, AttributeError) as exc:
                issues.append(PxpIssue(command, str(exc)))
        elif command == "AppendMatrixContour":
            contours.append(PxpContour(_wave_reference(args.strip(), data_folder)))
        elif command == "ModifyContour":
            try:
                from materials_data_studio.tools.command_language import Operation, evaluate, parse
                from materials_data_studio.tools.igor_canvas import modify_contour
                op = parse(command + " " + args)[0]
                assert isinstance(op, Operation)
                target, properties = op.arguments
                index = next(i for i,c in enumerate(contours) if c.wave == target.path or c.wave.rsplit(":",1)[-1] == target.path)
                # Native palette and level settings share the command service.
                contours[index] = modify_contour(contours[index], properties, lambda value: evaluate(value, lambda name: (_ for _ in ()).throw(ValueError("Literal contour setting required"))))
            except (ValueError, AttributeError, StopIteration) as exc:
                issues.append(PxpIssue(command, str(exc)))
        elif command == "AppendImage":
            parts = re.split(r"\s+vs\s+", args, maxsplit=1)
            refs = _parts(parts[1].strip("{} ")) if len(parts) > 1 else []
            images.append(PxpImage(_wave_reference(parts[0].strip(), data_folder), x_wave=_wave_reference(refs[0], data_folder) if refs and refs[0] != "*" else None, y_wave=_wave_reference(refs[1], data_folder) if refs and refs[1] != "*" else None))
        elif command == "ModifyGraph":
            _modify_graph(args, traces, axes, settings, issues, data_folder)
        elif command == "ModifyImage":
            _modify_image(args, images, issues)
        elif command == "SetAxis":
            match = re.fullmatch(r"([A-Za-z]\w*)\s*([*+\-.\d].*)", args)
            if match:
                axis_name, values = match.groups()
                limits = _value_pair(values)
                if limits:
                    _replace_axis(axes, axis_name, limits=limits)
                elif "*" in values:
                    parts = _parts(values)
                    if len(parts) == 2:
                        try:
                            partial = tuple(None if p == "*" else float(p) for p in parts)
                            _replace_axis(axes, axis_name, limits=partial)
                        except ValueError:
                            issues.append(PxpIssue("SetAxis", "Invalid automatic axis endpoint"))
        elif command == "Label":
            axis_name, _, label_text = args.partition(" ")
            _replace_axis(axes, axis_name, label_raw=_quote(label_text))
        elif command in {"Legend", "TextBox"}:
            target = re.search(r"/N=([^/\s]+)", args)
            annotation_target = target[1] if target else "text0"
            if re.search(r"/K(?=/|\s|$)", args):
                annotations[:] = [a for a in annotations if a.name != annotation_target]
                if command == "Legend":
                    legend = ""
                continue
            for flag in re.findall(r"/([A-Za-z]+)", args.split('"', 1)[0]):
                if flag not in {
                    "A",
                    "B",
                    "C",
                    "D",
                    "E",
                    "F",
                    "G",
                    "H",
                    "LS",
                    "N",
                    "O",
                    "S",
                    "W",
                    "X",
                    "Y",
                }:
                    issues.append(PxpIssue(command, f"Not reproduced annotation flag: /{flag}"))
            if re.search(r"/E(?:=1)?(?=/|\s|$)", args):
                issues.append(
                    PxpIssue(
                        command,
                        "Exterior annotation retained; automatic margin expansion is approximate",
                    )
                )
            colorbar_target = ""
            previous = next((a for a in annotations if a.name == annotation_target), None)
            graph_style = settings["style"]
            assert isinstance(graph_style, PxpGraphStyle)
            default_style = PxpAnnotationStyle(
                font_size=0,
                offset_mode="native", inherit_graph_font=True,
            )
            if command == "Legend":
                default_style = replace(default_style, anchor="RT", x_percent=5, y_percent=5)
            if re.search(r"/A=?[A-Z]+", args):
                default_style = replace(default_style, x_percent=0, y_percent=0)
            annotation = PxpAnnotation(
                annotation_target,
                _quote(args) or (previous.raw if previous else ""),
                _annotation(args, previous.style if previous else default_style),
            )
            annotations[:] = [a for a in annotations if a.name != annotation_target]
            annotations.append(annotation)
            if command == "Legend":
                legend, legend_style = annotation.raw, annotation.style
        elif command == "ColorScale":
            trace_match = re.search(r"trace=([^,\s]+)", args)
            if trace_match:
                _replace_trace(traces, trace_match[1], color_scale=True)
                continue
            image_match = re.search(r"image=([^,\s]+)", args)
            target = image_match[1] if image_match else (images[-1].wave if images else "")
            target = next(
                (image.wave for image in images if image.wave.rsplit(":", 1)[-1] == target), target
            )
            from materials_data_studio.tools.command_language import (
                evaluate,
                expression,
                operation_head,
                split,
            )
            from materials_data_studio.tools.igor_graph_extensions import colorscale_options
            _op, _flags, operands = operation_head("ColorScale " + args)
            color_options = []
            for entry in split(operands):
                key, equal, value = entry.partition("=")
                if equal and key.lower() != "image":
                    try:
                        color_options.append((key, evaluate(expression(value), lambda name: (_ for _ in ()).throw(ValueError("Literal ColorScale option required")))))
                    except (ValueError, SyntaxError) as exc:
                        issues.append(PxpIssue(command, str(exc)))
            colorbar_target, annotation_target = target, ""
            for index, image in enumerate(images):
                if image.wave == target:
                    images[index] = replace(
                        image,
                        colorbar_visible=True,
                        colorbar_style=_annotation(
                            args, replace(image.colorbar_style, offset_mode="native", inherit_graph_font=True)
                        ),
                    )
                    try:
                        images[index] = colorscale_options(images[index], color_options)
                    except ValueError as exc:
                        issues.append(PxpIssue(command, str(exc)))
        elif command == "AppendText":
            target = re.search(r"/N=([^/\s]+)", args)
            name_target = target[1] if target else annotation_target
            if name_target:
                no_cr = re.search(r"/NOCR(?:=(\d))?(?=/|\s|$)", args)
                prefix = "" if no_cr and no_cr[1] != "0" else "\n"
                for index, annotation in enumerate(annotations):
                    if annotation.name == name_target:
                        annotations[index] = replace(
                            annotation, raw=annotation.raw + prefix + _quote(args)
                        )
            elif colorbar_target:
                for index, image in enumerate(images):
                    if image.wave == colorbar_target:
                        images[index] = replace(
                            image, colorbar_label_raw=image.colorbar_label_raw + _quote(args)
                        )
        elif command in {"ErrorBars", "Cursor", "Tag"}:
            try:
                from materials_data_studio.tools.command_language import (
                    Operation,
                    evaluate,
                    expression,
                    parse,
                )
                from materials_data_studio.tools.pxp_model import PxpCursor
                operation = parse(command + args if args.startswith("/") else command + " " + args)[0]
                if not isinstance(operation, Operation):
                    raise ValueError("Operation required")
                def scalar(raw):
                    return evaluate(raw, lambda name: (_ for _ in ()).throw(ValueError("Literal settings required")))
                literal_flags: dict[str, Any] = {f.name: scalar(f.expression) if f.expression is not None and f.name in {"L", "T", "X", "Y", "RGB", "C", "CLIP"} else f.value for f in operation.flags}
                if command == "ErrorBars":
                    target, direction, low, high = operation.arguments[:4]
                    changes: dict[str, Any] = dict(error_width=float(literal_flags.get("L", .9)), error_cap=float(literal_flags.get("Y", literal_flags.get("X", 2))), error_cap_thickness=float(literal_flags.get("T", 1)), error_clip=bool(literal_flags.get("CLIP", 1)))
                    if "RGB" in literal_flags:
                        changes["error_color"] = tuple(map(int, literal_flags["RGB"]))
                    if direction in {"X", "XY"}:
                        changes.update(x_error_low=_wave_reference(low.path, data_folder), x_error_high=_wave_reference(high.path, data_folder))
                    if direction in {"Y", "XY"}:
                        low, high = operation.arguments[4:] if direction == "XY" else (low, high)
                        changes.update(error_axis="Y", error_low=_wave_reference(low.path, data_folder), error_high=_wave_reference(high.path, data_folder))
                    _replace_trace(traces, target.path, **changes)
                elif command == "Cursor":
                    values = [a.text for a in operation.arguments]
                    if "P" not in literal_flags or len(values) != 3:
                        raise ValueError("Imported Cursor requires /P and three operands")
                    cursors.append(PxpCursor(values[0], values[1], int(values[2]), literal_flags.get("C", (65535, 0, 0))))
                else:
                    values = operation.arguments
                    identifier = next((f.value for f in operation.flags if f.name == "N"), "tag0") or "tag0"
                    annotations.append(PxpAnnotation(identifier, str(scalar(expression(values[2].text))), _annotation(args, PxpAnnotationStyle()), values[0].text, int(values[1].text), int(literal_flags.get("L", 0))))
            except (ValueError, IndexError, AttributeError, TypeError) as exc:
                issues.append(PxpIssue(command, str(exc)))
        elif command == "SetDrawLayer":
            draw_layer = args
        elif command == "SetDrawEnv":
            keys = {
                "xcoord": "xcoord",
                "ycoord": "ycoord",
                "arrow": "arrow",
                "arrowlen": "arrow_length",
                "arrowfat": "arrow_fat",
                "linethick": "line_width",
                "linefgc": "color",
                "textrgb": "color",
                "fsize": "font_size",
                "fstyle": "font_style",
                "fillfgc": "fill_color",
                "fillpat": "fill_mode",
                "dash": "line_style",
                "font": "font_family",
                "fname": "font_family",
                "textrot": "rotation",
                "textxjust": "textxjust",
                "textyjust": "textyjust",
                "linecap": "line_cap",
                "linejoin": "line_join",
            }
            for part in _parts(args):
                key, _, value = part.partition("=")
                key, value = key.strip(), value.strip()
                if key in keys:
                    try:
                        if key in {"textxjust", "textyjust"}:
                            current = draw_settings.get("anchor", "LB")
                            draw_settings["anchor"] = (
                                {0: "L", 1: "M", 2: "R"}.get(int(value), "L") + current[1]
                                if key == "textxjust"
                                else current[0] + {0: "T", 1: "C", 2: "B"}.get(int(value), "B")
                            )
                        else:
                            draw_settings[keys[key]] = (
                                value.strip('"')
                                if key.endswith("coord") or key in {"font", "fname"}
                                else _rgb(value)
                                if key in {"linefgc", "textrgb", "fillfgc"}
                                else int(value)
                                if key
                                in {"arrow", "fstyle", "fillpat", "dash", "linecap", "linejoin"}
                                else float(value)
                            )
                    except ValueError:
                        issues.append(PxpIssue(command, f"Invalid drawing option: {key}"))
                else:
                    issues.append(PxpIssue(command, f"Unsupported drawing option: {key}"))
        elif command in {"DrawPoly", "DrawBezier"}:
            try:
                # Literal-path form only; wave-connected drawings are not executed.
                flags = re.match(r"^(?:/[A-Za-z]+\s*)*", args)
                parts = _parts(args[flags.end() :] if flags else args)
                append = bool(re.search(r"/A(?:\s|/|$)", args))
                values = tuple(float(v) for v in parts[-1].strip("{} ").split(","))
                if len(values) % 2:
                    raise ValueError("Invalid path pairs")
                if append and drawings and drawings[-1].kind == command:
                    drawings[-1] = replace(
                        drawings[-1], coordinates=drawings[-1].coordinates + values
                    )
                elif len(parts) == 5:
                    ox, oy, sx, sy = map(float, parts[:4])
                    x0, y0 = (0.0, 0.0) if "/ABS" in args else values[:2]
                    coords = tuple(
                        v
                        for i in range(0, len(values), 2)
                        for v in (ox + sx * (values[i] - x0), oy + sy * (values[i + 1] - y0))
                    )
                    if command == "DrawBezier" and (len(coords) // 2 - 1) % 3:
                        raise ValueError("Invalid cubic control points")
                    drawings.append(PxpDrawing(command, coords, **draw_settings, layer=draw_layer))
                    draw_settings = {}
                else:
                    raise ValueError("Wave-connected drawing unsupported")
            except (ValueError, IndexError):
                issues.append(PxpIssue(command, "Unsupported/invalid literal drawing path"))
        elif command in {"DrawLine", "DrawText", "DrawRect", "DrawOval", "DrawRRect", "DrawArc"}:
            try:
                parts = _parts(args)
                count = 2 if command == "DrawText" else 6 if command == "DrawArc" else 4
                coordinates = tuple(float(p) for p in parts[:count])
                if len(coordinates) != count:
                    raise ValueError("Missing drawing coordinates")
                drawings.append(
                    PxpDrawing(
                        command,
                        coordinates,
                        _quote(args) if command == "DrawText" else "",
                        **draw_settings,
                        layer=draw_layer,
                    )
                )
                draw_settings = {}
            except ValueError:
                issues.append(PxpIssue(command, "Invalid drawing coordinates"))
        elif command in {"ShowTools", "MoveWindow"}:
            pass  # Igor editing/minimized-window state does not change graph content.
        else:
            issues.append(PxpIssue(command, "Unsupported graph command"))
    if kind:
        finish()
    return tuple(graphs), tuple(tables)
