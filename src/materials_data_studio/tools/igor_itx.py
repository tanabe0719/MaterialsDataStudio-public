"""Read and write Igor Text waves without executing embedded Igor commands."""
from __future__ import annotations

import io
import json
import math
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from threading import Event

import numpy as np

from materials_data_studio.plotting.igor_names import unique_igor_name
from materials_data_studio.tools.graph_waves import make_wave
from materials_data_studio.tools.pxp_model import PxpWave

_TOKEN = re.compile(r'''"(?:[^"\\]|\\.)*"|'[^']+'|[^\s,]+''')
_QUOTED = r'"(?:[^"\\]|\\.)*"'
_SCALE = re.compile(
    rf'SetScale(?:/([PI]))?\s+([xyztd])\s+([^,]+),([^,]+),({_QUOTED}),\s*(.+)',
    re.IGNORECASE,
)
_NUMBER_TYPES = {"D": "f8", "S": "f4", "I": "i4", "W": "i2", "B": "i1"}
_NOTE = re.compile(rf"Note(?:/K)?\s+([^,]+),\s*({_QUOTED})", re.IGNORECASE)
_DIM_LABEL = re.compile(r"SetDimLabel\s+([0-3]),\s*(-?\d+),\s*([^,]+),\s*(.+)", re.IGNORECASE)
_MAX_POINTS = 10_000_000
_MAX_FILE = 256 * 1024**2


def _strings(value: str) -> list[str]:
    return [json.loads(token) if token.startswith('"') else token for token in _TOKEN.findall(value)]


def _numbers(value: str, dtype: np.dtype) -> list[float] | list[int]:
    tokens = value.replace(",", " ").replace("D", "E").replace("d", "e").split()
    if dtype.kind not in "iu":
        return [float(token) for token in tokens]
    bounds = np.iinfo(dtype)
    result = [int(token) for token in tokens]
    if any(not bounds.min <= number <= bounds.max for number in result):
        raise ValueError("Integer value is outside the declared ITX wave type.")
    return result


def _apply_metadata(lines: list[str], waves: dict[str, PxpWave]) -> None:
    for line in lines:
        # Split semicolons only outside string literals. ITX can contain general
        # commands. Only fully matched, declarative SetScale is interpreted.
        for command in re.findall(rf'(?:{_QUOTED}|[^;"])+', line[2:]):
            command = command.strip()
            note = _NOTE.fullmatch(command)
            if note:
                name, value = note.groups()
                key = name.strip().strip("'").casefold()
                if key in waves:
                    waves[key] = replace(waves[key], wave_note=json.loads(value))
                continue
            label = _DIM_LABEL.fullmatch(command)
            if label:
                dimension_text, index_text, value, name = label.groups()
                key = name.strip().strip("'").casefold()
                if key in waves:
                    waves[key] = _set_dim_label(waves[key], int(dimension_text), int(index_text), value.strip().strip("'"))
                continue
            match = _SCALE.fullmatch(command)
            if match is None:
                continue
            mode, axis, start, second, units, names = match.groups()
            offset, delta = float(start), float(second)
            if not math.isfinite(offset) or not math.isfinite(delta):
                raise ValueError("ITX scaling must use finite numbers.")
            for name in _TOKEN.findall(names):
                key = name.strip("'").casefold()
                wave = waves.get(key)
                if wave is None:
                    continue
                dimension = "xyztd".index(axis.lower())
                if dimension == 4 or (dimension == 1 and len(wave.shape) == 1 and mode is None):
                    waves[key] = replace(wave, data_units=json.loads(units))
                elif dimension < len(wave.shape):
                    scale = list(wave.dimension_scaling)
                    labels = list(wave.dimension_units)
                    step = delta
                    if mode is None or mode.upper() == "I":
                        step = (delta - offset) / max(1, wave.shape[dimension] - int(mode is not None))
                    scale[dimension] = (offset, step)
                    labels[dimension] = json.loads(units)
                    waves[key] = replace(wave, dimension_scaling=tuple(scale), dimension_units=tuple(labels))


def _set_dim_label(wave: PxpWave, dimension: int, index: int, value: str) -> PxpWave:
    if dimension >= len(wave.shape) or not -1 <= index < wave.shape[dimension]:
        raise ValueError("ITX dimension label index is outside the wave.")
    if index == -1:
        names = list(wave.dimension_names)
        names[dimension] = value
        return replace(wave, dimension_names=tuple(names))
    labels = list(wave.dimension_labels)
    entries = list(labels[dimension])
    entries.extend([""] * (index + 1 - len(entries)))
    entries[index] = value
    labels[dimension] = tuple(entries)
    return replace(wave, dimension_labels=tuple(labels))


def _read_block(
    lines: Iterator[str], dtype: np.dtype, *, width: int, expected: int | None,
    remaining: int, is_text: bool,
) -> np.ndarray:
    if expected is not None and expected > remaining:
        raise ValueError("ITX contains too many wave points.")
    real_dtype = np.dtype("f8" if dtype.itemsize == 16 else "f4") if dtype.kind == "c" else dtype
    buffer = np.empty(expected, dtype=real_dtype) if expected is not None and not is_text else None
    tokens: list[str | int | float] = []
    count = 0
    for row in lines:
        row = row.strip()
        if row.upper() == "END":
            break
        if not row:
            continue
        items = _strings(row) if is_text else _numbers(row, real_dtype)
        if expected is None and len(items) != width:
            raise ValueError("ITX row width does not match the WAVES declaration.")
        stop = count + len(items)
        if expected is not None and stop > expected:
            raise ValueError("ITX data length does not match the WAVES declaration.")
        if stop > remaining:
            raise ValueError("ITX contains too many wave points.")
        if buffer is None:
            tokens.extend(items)
        else:
            buffer[count:stop] = items
        count = stop
    else:
        raise ValueError("Missing END for ITX wave data.")
    if not count or (expected is not None and count != expected):
        raise ValueError("ITX data length does not match the WAVES declaration.")
    return buffer if buffer is not None else np.asarray(tokens, dtype=str if is_text else real_dtype)


def _bounded_lines(stream: io.TextIOWrapper, raw: io.BufferedReader, cancelled: Event | None) -> Iterator[str]:
    while True:
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError("ITX import stopped")
        line = stream.readline(_MAX_FILE + 1)
        if raw.tell() > _MAX_FILE:
            raise ValueError("ITX import is limited to 256 MiB.")
        if not line:
            return
        yield line


def read_itx(path: Path, *, cancelled: Event | None = None) -> list[PxpWave]:
    """Read standard text or numeric WAVES blocks, including 2–4 dimensions."""
    if path.stat().st_size > _MAX_FILE:
        raise ValueError("ITX import is limited to 256 MiB.")
    with path.open("rb") as raw:
        bom = raw.peek(2).startswith((b"\xff\xfe", b"\xfe\xff"))
    encodings = ("utf-16",) if bom else ("utf-8-sig", "cp932")
    for encoding in encodings:
        with path.open("rb") as raw, io.TextIOWrapper(raw, encoding=encoding) as stream:
            try:
                return _read_waves(_bounded_lines(stream, raw, cancelled))
            except UnicodeDecodeError:
                continue
    raise ValueError("ITX encoding could not be recognized (UTF-8 / UTF-16 / CP932).")


def _read_waves(lines: Iterator[str]) -> list[PxpWave]:
    if next(lines, "").strip().upper() != "IGOR":
        raise ValueError("An Igor Text file must begin with IGOR.")
    waves: dict[str, PxpWave] = {}
    metadata = []
    allocated = 0
    for line in lines:
        line = line.strip()
        if line.upper().startswith("X "):
            metadata.append(line)
            continue
        if not re.match(r"WAVES(?:[\s/]|$)", line, re.IGNORECASE):
            continue
        declaration = re.fullmatch(r"WAVES((?:/[A-Z]+(?:=\([^)]*\))?)*)\s+(.+)", line, re.IGNORECASE)
        if declaration is None:
            raise ValueError("Invalid ITX WAVES declaration.")
        raw_options, raw_names = declaration.groups()
        options = raw_options.upper().split("/")[1:]
        dims = next((flag[2:] for flag in options if flag.startswith("N=")), "")
        shape = tuple(map(int, dims.strip("()").split(","))) if dims else ()
        names = [name.strip("'") for name in _TOKEN.findall(raw_names)]
        is_text = "T" in options
        code = next((flag for flag in options if flag in _NUMBER_TYPES), "S")
        dtype = np.dtype(("u" if "U" in options else "") + _NUMBER_TYPES[code].lstrip("i")) if "U" in options else np.dtype(_NUMBER_TYPES[code])
        if "C" in options:
            dtype = np.dtype("c16" if code == "D" else "c8")
        if not names or len(set(name.casefold() for name in names)) != len(names) or any(name.casefold() in waves for name in names):
            raise ValueError("ITX wave names must be unique.")
        if shape and (len(shape) > 4 or min(shape) < 1 or math.prod(shape) * len(names) > _MAX_POINTS):
            raise ValueError("Invalid or oversized ITX wave dimensions.")
        marker = next((ln.strip() for ln in lines if ln.strip()), "")
        if marker.upper() != "BEGIN":
            raise ValueError("Missing BEGIN for ITX wave data.")
        width = (shape[1] if len(shape) >= 2 else 1) * len(names)
        components = 2 if dtype.kind == "c" else 1
        expected = math.prod(shape) * len(names) * components if shape else None
        # With declared dimensions Igor reads a stream of values. Physical
        # lines may contain part of a row, several rows, or cross a layer.
        data = _read_block(lines, dtype, width=width * components, expected=expected,
                           remaining=_MAX_POINTS - allocated, is_text=is_text)
        allocated += data.size
        values = (data.view(dtype) if components == 2 else data).reshape(-1, width)
        actual_shape = shape or (len(values),)
        for index, name in enumerate(names):
            block = values[:, index::len(names)]
            if len(actual_shape) > 2:
                rows, columns, *planes = actual_shape
                block = block.reshape(-1, rows, columns).transpose(1, 2, 0)
                block = block.reshape((rows, columns, *planes), order="F")
            else:
                block = block.reshape(actual_shape)
            waves[name.casefold()] = make_wave(name, block)
    if not waves:
        raise ValueError("No ITX waves were found.")
    _apply_metadata(metadata, waves)
    return list(waves.values())


def write_itx(path: Path, wave: PxpWave) -> None:
    """Write an Igor Save/T-compatible block and explicit axis scalings."""
    data = wave.data
    kind, size = data.dtype.kind, data.dtype.itemsize
    if kind in "US":
        flags = "/T"
    elif kind == "f":
        flags = "/D" if size > 4 else "/S"
    elif kind == "c":
        flags = "/D/C" if size > 8 else "/S/C"
    elif kind in "iub" and size <= 4:
        flags = {1: "/B", 2: "/W", 4: "/I"}[size] + ("/U" if kind == "u" else "")
    else:
        raise ValueError("This wave type cannot be saved without precision loss as ITX.")
    if data.ndim > 1:
        flags += "/N=(" + ",".join(map(str, data.shape)) + ")"
    name = unique_igor_name(wave.name, set(), fallback="wave")
    if data.ndim > 2:
        values = data.reshape((*data.shape[:2], -1), order="F").transpose(2, 0, 1).reshape(-1, data.shape[1])
    elif data.ndim == 1:
        values = data[:, None]
    else:
        values = data
    descriptor, temporary = tempfile.mkstemp(prefix=".mds-itx-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"IGOR\nWAVES{flags}\t{name}\nBEGIN\n")
            for row in values:
                tokens = (json.dumps(str(cell), ensure_ascii=False) if kind in "US" else
                          str(int(cell)) if kind in "iub" else
                          f"{cell.real:.17g}\t{cell.imag:.17g}" if kind == "c" else
                          format(float(cell), ".17g") for cell in row)
                handle.write("\t" + "\t".join(tokens) + "\n")
            handle.write("END\n")
            scale = [f"SetScale/P {axis} {offset:.17g},{step:.17g},{json.dumps(units, ensure_ascii=False)}, {name}"
                     for axis, (offset, step), units in zip("xyzt"[:data.ndim], wave.dimension_scaling, wave.dimension_units, strict=True)]
            scale.append(f'SetScale d 0,0,{json.dumps(wave.data_units, ensure_ascii=False)}, {name}')
            handle.write("X " + "; ".join(scale) + "\n")
            for dim, (dim_name, labels) in enumerate(zip(wave.dimension_names, wave.dimension_labels, strict=True)):
                for index, label in enumerate((dim_name, *labels), start=-1):
                    if label:
                        if any(char in label for char in "',;\r\n"):
                            raise ValueError("A dimension label contains characters unsupported by ITX.")
                        handle.write(f"X SetDimLabel {dim}, {index}, '{label}', {name}\n")
            if wave.wave_note:
                handle.write(f"X Note {name}, {json.dumps(wave.wave_note, ensure_ascii=False)}\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
