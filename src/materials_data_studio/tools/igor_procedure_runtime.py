"""Bounded Igor compatibility helpers for explicitly run isolated procedures.

These helpers never interpret Python, open files, dispatch GUI operations or run
Igor/XOP code. All document commands go through the canonical atomic engine.
"""
from __future__ import annotations

import ast
import copy
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import numpy as np

from materials_data_studio.tools.command_engine import (
    FLAGS,
    GRAPH_COMMANDS,
    TABLE_COMMANDS,
    CommandEngine,
)
from materials_data_studio.tools.command_language import (
    FUNCTIONS,
    Argument,
    Assignment,
    Expression,
    Setting,
    evaluate,
    expression,
    operation_head,
    parse,
    split,
)
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_waves import make_wave
from materials_data_studio.tools.igor_prf_compat import reflection_columns
from materials_data_studio.tools.igor_virtual_io import (
    INPUT_RESOURCES_KEY,
    VIRTUAL_COMMANDS,
    VirtualTextIO,
    validate_resources,
)

MAX_RUNTIME_STEPS = 1_000_000

# Deliberately explicit: adding a future Command Window command does not grant
# converted/imported Python new authority (especially host I/O or GUI actions).
SAFE_COMMANDS = frozenset(["make", "duplicate", "rename", "killwaves", "redimension", "setscale", "insertpoints", "deletepoints", "concatenate", "sort", "extract", "print", "newdatafolder", "setdatafolder", "killdatafolder", "display", "appendtograph", "removefromgraph", "modifygraph", "setaxis", "label", "legend", "textbox", "appendtext", "tag", "errorbars", "reordertraces", "newfreeaxis", "modifyfreeaxis", "killfreeaxis", "appendimage", "removeimage", "modifyimage", "colorscale", "reorderimages", "setdrawenv", "setdrawlayer", "drawline", "drawrect", "drawoval", "drawtext", "edit", "appendtotable", "removefromtable", "dowindow", "killwindow", "renamewindow", "wavestats", "differentiate", "integrate", "smooth", "histogram", "fft", "findpeak", "findlevel", "curvefit"])

GRAPH_OPTIONS = frozenset(["mode", "marker", "msize", "lsize", "rgb", "mrkfillrgb", "mrkstrokergb", "mrkthick", "opaque", "usemrkstrokergb", "lstyle", "loptions", "offset", "muloffset", "linejoin", "hide", "hidetrace", "tick", "mirror", "axthick", "btlen", "stlen", "btthick", "stthick", "ftlen", "ftthick", "minor", "log", "grid", "fsize", "fstyle", "font", "gfont", "lblmargin", "axoffset", "lblpos", "lblposmode", "lbllatpos", "lblrot", "tklblrot", "nolabel", "tickunit", "zaptz", "reverse", "nticks", "sep", "axrgb", "alblrgb", "tickrgb", "tlblrgb", "gridrgb", "mantick", "manminor", "userticks", "width", "height", "margin", "gbrgb", "swapxy", "freepos", "axisenab", "standoff", "zero", "gridhair", "lowtrip", "hightrip", "notation", "zisz", "tickenab", "gfsize"])

MATH_FUNCTIONS = frozenset(FUNCTIONS) | frozenset(["round", "numpnts", "wavemin", "wavemax", "interp", "strlen", "itemsinlist", "stringfromlist", "replacestring", "str2num", "num2str", "numtype", "waveexists", "getdatafolder", "abs", "min", "max", "mod", "indexedfile", "parsefilepath", "padstring", "strsearch"])
MATH_FUNCTIONS |= frozenset({"axisinfo", "traceinfo", "dimsize", "dimoffset", "dimdelta"})
MATH_FUNCTIONS |= frozenset({"note", "getdimlabel", "finddimlabel"})
SAFE_COMMANDS |= frozenset({"splitwave", "resample", "note", "setdimlabel", "appendboxplot", "modifyboxplot"})
SAFE_COMMANDS |= frozenset({"imagethreshold", "statsttest", "rotate", "unwrap"})
SAFE_COMMANDS |= frozenset({"mdsstatistics", "mdsprocess"})
SAFE_COMMANDS |= frozenset({"appendviolinplot", "modifyviolinplot"})
SAFE_COMMANDS |= frozenset({"cursor", "modifytable"})
MATH_FUNCTIONS |= frozenset({"pcsr", "hcsr", "vcsr"})
SAFE_COMMANDS |= frozenset({"setimagecoordinates", "newlayout", "appendlayoutobject", "appendmatrixcontour", "modifycontour"})
GRAPH_OPTIONS |= frozenset({"dateinfo", "zcolor", "logzcolor", "zmrksize", "zmrknum"})
SAFE_COMMANDS |= frozenset({"movedatafolder", "renamedatafolder", "movewave", "smoothderived", "histogramderived", "fftderived", "curvefitderived"})


def dynamic_references(source: str, resolver: Any) -> str:
    """Resolve $name / $(expression) only outside strings, without interpolation.

    The result is a single quoted object reference, not executable command text.
    Quotes/control characters in the *name* are rejected instead of escaped into
    another statement. Static analysis supplies a harmless placeholder resolver.
    """
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(source):
        char = source[i]
        if quote:
            out.append(char)
            if char == "\\" and i + 1 < len(source):
                i += 1
                out.append(source[i])
            elif char == quote:
                quote = ""
            i += 1
            continue
        if char in "\"'":
            quote = char
            out.append(char)
            i += 1
            continue
        if char != "$":
            out.append(char)
            i += 1
            continue
        i += 1
        if i < len(source) and source[i] == "(":
            start = i + 1
            depth, inner_quote = 1, ""
            i += 1
            while i < len(source) and depth:
                current = source[i]
                if inner_quote:
                    if current == "\\":
                        i += 2
                        continue
                    if current == inner_quote:
                        inner_quote = ""
                elif current in "\"'":
                    inner_quote = current
                elif current == "(":
                    depth += 1
                elif current == ")":
                    depth -= 1
                i += 1
            if depth:
                raise ValueError("Unclosed dynamic object reference")
            value = resolver(source[start:i - 1])
        else:
            match = re.match(r"[A-Za-z][A-Za-z0-9_]*(?:\[[^]\n]*\])*", source[i:])
            if match is None:
                raise ValueError("Expected $name or $(expression)")
            end = i + len(match[0])
            if end < len(source) and source[end] == "(":
                depth, inner_quote = 1, ""
                end += 1
                while end < len(source) and depth:
                    current = source[end]
                    if inner_quote:
                        if current == "\\":
                            end += 2
                            continue
                        if current == inner_quote:
                            inner_quote = ""
                    elif current in "\"'":
                        inner_quote = current
                    elif current == "(":
                        depth += 1
                    elif current == ")":
                        depth -= 1
                    end += 1
                if depth:
                    raise ValueError("Unclosed dynamic function reference")
                value = resolver(source[i:end])
            else:
                value = resolver(match[0])
            i = end
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise ValueError("Dynamic object references require a nonempty name string")
        if any(c in value for c in "'\"\0\r\n"):
            raise ValueError("Dynamic object names cannot contain quotes/control characters")
        out.append("'" + value + "'")
    if quote:
        raise ValueError("Unclosed literal")
    return "".join(out)


def _prepare_expression(source: str) -> Expression:
    """Parse the numeric/string subset; never eval Python source."""
    parts = re.split(r'("(?:[^"\\]|\\.)*"|\x27[^\x27]*\x27)', source)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(r"!(?!=)", " not ", parts[index])
    source = "".join(parts)
    value = expression(source)

    class Indexes(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.AST:
            canonical = {"nan": "NaN", "inf": "Inf", "pi": "pi", "e": "e"}.get(node.id.lower())
            return ast.Name(canonical, ast.Load()) if canonical else node

        def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
            indices: list[ast.expr] = []
            current: ast.expr = node
            while isinstance(current, ast.Subscript):
                index = current.slice
                if isinstance(index, ast.Tuple):
                    indices[0:0] = index.elts
                elif isinstance(index, ast.Slice):
                    raise ValueError("Igor expression ranges are unsupported; use point indices")
                else:
                    indices.insert(0, index)
                current = current.value
            return ast.Call(ast.Name("mdsindex", ast.Load()),
                            [self.visit(current), *(self.visit(i) for i in indices)], [])

        def visit_Call(self, node: ast.Call) -> ast.AST:
            # WaveExists(alias) needs the reference even when no wave exists.
            if (isinstance(node.func, ast.Name) and node.func.id.lower() in {"waveexists", "indexedfile"}
                    and len(node.args) == 1 and isinstance(node.args[0], ast.Name)):
                name = value.references.get(node.args[0].id, node.args[0].id)
                return ast.Call(ast.Name(node.func.id.lower(), ast.Load()), [ast.Constant(name)], [])
            if (isinstance(node.func, ast.Name) and node.func.id.lower() == "indexedfile"
                    and node.args and isinstance(node.args[0], ast.Name)):
                name = value.references.get(node.args[0].id, node.args[0].id)
                return ast.Call(ast.Name("indexedfile", ast.Load()),
                                [ast.Constant(name), *(self.visit(a) for a in node.args[1:])], [])
            return self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
            if isinstance(node.value, ast.Name) and node.attr.lower() == "eventcode":
                return ast.Call(ast.Name("mdsevent", ast.Load()), [self.visit(node.value)], [])
            return self.generic_visit(node)

    return Expression(ast.fix_missing_locations(Indexes().visit(value.node)), value.references)  # type: ignore[arg-type]


# Cache syntax, never resolved values or a scope's function implementations.
# Long literals bypass the cache so a sequence of large expressions cannot
# retain hundreds of MiB within the worker's existing memory limit. Cached
# trees/references are read-only after the transformer above has completed.
_CACHED_EXPRESSION_LENGTH = 4096
_cached_expression = lru_cache(maxsize=256)(_prepare_expression)


def prepare_expression(source: str) -> Expression:
    if len(source) <= _CACHED_EXPRESSION_LENGTH:
        return _cached_expression(source)
    return _prepare_expression(source)


def _validate_expression(source: str, functions: frozenset[str]) -> None:
    value = prepare_expression(dynamic_references(source, lambda _s: "mdsDynamicReference"))
    allowed = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Tuple, ast.List,
               ast.UnaryOp, ast.USub, ast.UAdd, ast.Not, ast.BinOp, ast.Add, ast.Sub,
               ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.BitAnd, ast.BitOr, ast.Compare,
               ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq, ast.BoolOp,
               ast.And, ast.Or, ast.Call)
    names = MATH_FUNCTIONS | {"mdsindex", "mdsevent"} | {f.lower() for f in functions}
    for node in ast.walk(value.node):
        if not isinstance(node, allowed):
            raise ValueError(f"Unsupported expression syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError("Private/runtime identifiers are not Igor expressions")
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name)
                or node.func.id.lower() not in names or node.keywords):
            name = node.func.id if isinstance(node.func, ast.Name) else type(node.func).__name__
            raise ValueError("Unsupported function: " + name)


_cached_validation = lru_cache(maxsize=256)(_validate_expression)


def validate_expression(source: str, functions: set[str] | frozenset[str] = frozenset()) -> None:
    names = frozenset(name.lower() for name in functions)
    if len(source) <= _CACHED_EXPRESSION_LENGTH and len(names) <= 1024:
        _cached_validation(source, names)
    else:
        _validate_expression(source, names)


def validate_command(source: str, functions: set[str] | frozenset[str] = frozenset()) -> None:
    if len(source) > 1_000_000:
        raise ValueError("Command exceeds the source size limit")
    commands = parse(dynamic_references(source, lambda _s: "mdsDynamicReference"))
    if len(commands) > 1000:
        raise ValueError("A command call is limited to 1000 statements")

    def check(value: Any) -> None:
        if isinstance(value, (Argument, Setting)):
            check(value.expression)
        elif isinstance(value, Expression):
            for node in ast.walk(value.node):
                if isinstance(node, (ast.Attribute, ast.Lambda, ast.ListComp, ast.DictComp,
                                     ast.GeneratorExp, ast.NamedExpr)):
                    raise ValueError("Unsupported command expression")
                if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name)
                        or node.func.id.lower() not in MATH_FUNCTIONS | functions):
                    raise ValueError("Unsupported command function")
        elif isinstance(value, (tuple, list)):
            for item in value:
                check(item)

    for command in commands:
        if isinstance(command, Assignment):
            # The conversion scope owns local assignments; direct mds.command
            # assignments still use the canonical expression interpreter.
            check(command.value)
            continue
        if command.name not in SAFE_COMMANDS:
            raise ValueError("Unsupported document command: " + command.name)
        allowed = FLAGS.get(command.name, "W" if command.name in GRAPH_COMMANDS | TABLE_COMMANDS else "").split()
        bad = {f.name for f in command.flags} - set(allowed)
        if bad:
            raise ValueError("Unsupported " + command.name + " flag(s): " + ", ".join(sorted(bad)))
        flags = [flag.name for flag in command.flags]
        if len(flags) != len(set(flags)):
            raise ValueError("Duplicate command flags are unsupported")
        optional_values = {"L", "R", "B", "T"} if command.name in {"display", "appendtograph", "newfreeaxis"} else {"B", "L"} if command.name == "appendimage" else {"B"} if command.name == "smooth" else {"D", "R"} if command.name == "curvefit" else set()
        if command.name == "concatenate":
            optional_values |= {"NP"}
        if command.name in {"appendboxplot", "appendviolinplot"}:
            optional_values |= {"L", "R", "B", "T", "VERT", "CATL"}
        required_values = {
            "make": {"N"}, "redimension": {"N"}, "display": {"N", "HOST", "W"},
            "appendtograph": {"W"}, "setaxis": {"W"}, "modifygraph": {"W"},
            "label": {"W"}, "legend": {"W", "N", "F", "A", "X", "Y", "B"},
            "textbox": {"W", "N", "F", "A", "X", "Y", "B"}, "tag": {"W", "N", "L", "X", "Y", "A", "F"},
            "appendtext": {"W", "N"}, "newfreeaxis": {"W"},
            "colorscale": {"W", "N", "A", "X", "Y", "F", "B"}, "edit": {"N"},
            "differentiate": {"D"}, "integrate": {"D"}, "fft": {"DEST", "OUT"},
            "smooth": {"S", "E"}, "findlevel": {"EDGE", "R"}, "wavestats": {"R"},
            "histogram": {"B", "DEST", "W"}, "curvefit": {"X", "W", "I", "M", "MASK", "L"},
            "smoothderived": {"S"}, "histogramderived": {"B"}, "fftderived": {"D"}, "curvefitderived": {"D"},
        }.get(command.name, {"W"} if command.name in GRAPH_COMMANDS | TABLE_COMMANDS else set())
        required_values |= {"splitwave": {"N", "NAME", "DDF", "SDIM", "SCL"},
                            "resample": {"UP", "DOWN", "RATE", "SAME", "DIM", "N", "WINF", "E", "COEF"},
                            "appendboxplot": {"W", "TN"}, "appendviolinplot": {"W", "TN"},
                            "imagethreshold": {"T", "M", "R", "P", "DEST", "W"},
                            "statsttest": {"MEAN", "TAIL", "ALPH", "DFM", "DEST"},
                            "mdsstatistics": {"KIND", "METH", "ALPH", "TAIL", "DEST", "N", "SEED"},
                            "mdsprocess": {"KIND", "METH", "DEST", "N"},
                            "newlayout": {"N"}, "appendlayoutobject": {"W", "R"}, "cursor": {"W", "C"}, "errorbars": {"W", "L", "T", "X", "Y", "RGB", "CLIP"}}.get(command.name, set())
        for flag in command.flags:
            check(flag.expression)
            if flag.name in required_values and flag.value is None:
                raise ValueError("Flag /" + flag.name + " requires a value")
            if flag.name not in required_values | optional_values and flag.value is not None:
                raise ValueError("Flag /" + flag.name + " does not accept a value in the supported subset")
        check(command.arguments)
        if command.name in {"modifygraph", "modifyfreeaxis"}:
            bad_options = [s.name for s in command.arguments if s.name.lower() not in GRAPH_OPTIONS]
            if bad_options:
                raise ValueError("Unsupported graph option(s): " + ", ".join(bad_options))


def _index(data: Any, *indices: Any, legacy_clip: bool = False) -> Any:
    if isinstance(data, str):
        if len(indices) not in {1, 2}:
            raise ValueError("String indexing requires one index or an inclusive start,end range")
        if any(np.ndim(v) or np.isnan(v) or (np.isfinite(v) and int(v) != v) for v in indices):
            raise ValueError("String range indices must be scalar integers (Inf may end a range)")
        first = max(0, int(indices[0])) if np.isfinite(indices[0]) else len(data)
        if len(indices) == 1:
            return data[first:first + 1]
        last = int(indices[1]) if np.isfinite(indices[1]) else len(data) - 1
        return data[first:max(first, min(last + 1, len(data)))]
    if len(indices) not in {1, 2}:
        raise ValueError("Only one/two-dimensional wave indexing is supported")
    checked = []
    data = np.asarray(data)
    for dimension, value in enumerate(indices):
        array = np.asarray(value)
        if array.size > 10_000_000 or array.dtype.kind not in "iuf" or not np.isfinite(array).all() or not np.equal(array, np.floor(array)).all():
            raise ValueError("Wave indices must be bounded finite integers")
        if dimension >= data.ndim:
            raise ValueError("Too many wave indices")
        if legacy_clip:
            array = np.clip(array, 0, data.shape[dimension] - 1)
        elif np.any(array < 0) or np.any(array >= data.shape[dimension]):
            raise IndexError("Wave point index is outside its valid bounds (rtGlobals=3)")
        checked.append(int(array) if not array.ndim else array.astype(np.int64))
    return data[tuple(checked)]


def _interpolate(x: Any, coordinates: Any, values: Any) -> Any:
    xp, yp = np.asarray(coordinates, dtype=float), np.asarray(values, dtype=float)
    if xp.ndim != 1 or yp.shape != xp.shape or len(xp) < 2 or not np.isfinite(xp).all():
        raise ValueError("interp requires equal 1D X/Y waves with two finite X points")
    if np.all(np.diff(xp) < 0):
        xp, yp = xp[::-1], yp[::-1]
    if not np.all(np.diff(xp) > 0):
        raise ValueError("interp X coordinates must be strictly monotonic")
    query = np.asarray(x, dtype=float)
    result = np.interp(query, xp, yp)
    return np.where(query < xp[0], yp[0] + (query - xp[0]) * (yp[1] - yp[0]) / (xp[1] - xp[0]),
                    np.where(query > xp[-1], yp[-1] + (query - xp[-1]) * (yp[-1] - yp[-2]) / (xp[-1] - xp[-2]), result))


class _ScopedServices(DocumentServices):
    def __init__(self, scope: IgorScope) -> None:
        super().__init__(scope.owner.services.document, scope.owner.services.context)
        self.scope = scope
        self.expression_functions = {**self.expression_functions, **scope.functions}
        self.expression_wave_parameters = {**self.expression_wave_parameters, **scope.wave_parameters}
        self.expression_wave_reference = lambda name: self.wave(name).igor_path

    def path(self, name: str) -> str:
        if name.lower() in self.scope.null_aliases:
            raise ValueError("Null/missing wave reference: " + name)
        return super().path(self.scope.aliases.get(name.lower(), name))

    def resolve(self, name: str) -> Any:
        if name.lower() in self.scope.variables:
            return self.scope.variables[name.lower()]
        if name.lower() in self.scope.scalar_aliases:
            name = self.scope.scalar_aliases[name.lower()]
        candidates = {self.path(name).lower(), ("root:" + name).lower()}
        for section in ("variables", "strings"):
            values = self.environment.get(section, {})
            for key, value in values.items():
                if key.lower() in candidates:
                    return value
        return super().resolve(self.scope.aliases.get(name.lower(), name))

    def set_results(self, values: dict[str, float]) -> None:
        super().set_results(values)
        # Igor operation results also replace function-local V_* variables.
        # Keep converted-IPF evaluation and Command evaluation in sync.
        self.scope.variables.update({name.lower():value for name,value in values.items()})

    def wave(self, name: str):
        try:
            return super().wave(name)
        except ValueError:
            paths = {self.path(name).lower(), ("root:" + name).lower()}
            matches = [w for w in self.document.render_source().waves if w.igor_path.lower() in paths]
            if len(matches) == 1:
                return matches[0]
            raise


class IgorScope:
    """Local variable/wave bindings and a step budget, not Python globals."""
    def __init__(self, owner: IgorAPI, parameters: dict[str, Any], functions: dict[str, Any],
                 wave_parameters: dict[str, tuple[int, ...]] | None = None,
                 rt_globals: int = 3) -> None:
        if type(rt_globals) is not int or rt_globals not in {1, 2, 3}:
            raise ValueError("Converted rtGlobals must be 1, 2 or 3")
        self.owner = owner
        self.rt_globals = rt_globals
        self.variables = {name.lower(): value for name, value in parameters.items()}
        self.parameters = set(self.variables)
        self.types: dict[str, str] = {}
        self.aliases: dict[str, str] = {}
        self.null_aliases: set[str] = set()
        self.scalar_aliases: dict[str, str] = {}
        self.prompts: dict[str, dict[str, Any]] = {}
        self.wave_parameters = {**owner.services.expression_wave_parameters, **(wave_parameters or {})}
        self.functions: dict[str, Any] = {
            **owner.services.expression_functions,
            **FUNCTIONS, "round": lambda x: np.copysign(np.floor(np.abs(x) + .5), x),
            "wavemin": lambda w: float(np.nanmin(w)), "wavemax": lambda w: float(np.nanmax(w)),
            "interp": _interpolate, "strlen": lambda s: len(str(s)),
            "itemsinlist": lambda s, sep=";": len(str(s).rstrip(str(sep)).split(str(sep))) if s else 0,
            "stringfromlist": self.string_from_list,
            "replacestring": lambda old, text, new: str(text).replace(str(old), str(new)),
            "str2num": self.string_to_number, "num2str": lambda x: format(float(x), ".15g"),
            "numtype": lambda x: 2 if np.isnan(x) else 1 if np.isinf(x) else 0,
            "waveexists": self.wave_exists, "getdatafolder": self.data_folder,
            "min": np.minimum, "max": np.maximum, "mod": np.mod,
            "mdsindex": lambda data, *indices: _index(data, *indices, legacy_clip=rt_globals != 3),
            "mdsevent": self.event_code,
            "indexedfile": owner._io.indexed_file,
            "parsefilepath": self.parse_file_path,
            "padstring": self.pad_string, "strsearch": self.string_search,
            **{name.lower(): function for name, function in functions.items()},
        }
        self.services = _ScopedServices(self)
        # Do not expose the raw engine as a public Procedure attribute: only
        # command() performs the explicit capability/flag/option validation.
        self._engine = CommandEngine(self.services)

    @staticmethod
    def string_from_list(index: Any, text: Any, separator: str = ";") -> str:
        number = int(index)
        if number != index:
            raise ValueError("List indices must be integers")
        parts = str(text).rstrip(separator).split(separator) if text else []
        return parts[number] if 0 <= number < len(parts) else ""

    @staticmethod
    def string_to_number(text: Any) -> float:
        try:
            return float(str(text).strip())
        except ValueError:
            return float("nan")

    def wave_exists(self, name: Any) -> int:
        if str(name).lower() in self.null_aliases:
            return 0
        try:
            self.services.wave(self.aliases.get(str(name).lower(), str(name)))
            return 1
        except ValueError:
            return 0

    def data_folder(self, mode: Any = 1) -> str:
        if mode != 1:
            raise ValueError("Only GetDataFolder(1), the full current folder, is supported")
        return self.services.current_folder + ":"

    @staticmethod
    def event_code(value: Any) -> int:
        if not isinstance(value, dict) or set(value) != {"eventCode"} or value["eventCode"] != 2:
            raise ValueError("Only an explicit MDS button-click event (eventCode=2) is supported")
        return 2

    @staticmethod
    def parse_file_path(mode: Any, path: str, separator: str, which_end: Any, which_element: Any) -> str:
        if not isinstance(path, str) or separator not in {":", "\\", "/"} or mode not in {0, 1, 2, 3, 4} or which_end not in {0, 1} or int(which_element) != which_element or which_element < 0:
            raise ValueError("Unsupported ParseFilePath mode/arguments")
        positions = [m for m in re.finditer(r"[^:/\\]+", path)]
        index = len(positions) - 1 - int(which_element) if which_end else int(which_element)
        if mode == 2:
            return path if path.endswith((":", "/", "\\")) else path + separator
        leaf = positions[-1][0] if positions else ""
        if mode == 3:
            return leaf.rsplit(".", 1)[0] if "." in leaf else leaf
        if mode == 4:
            return leaf.rsplit(".", 1)[1] if "." in leaf else ""
        if not 0 <= index < len(positions):
            return path if mode == 1 else ""
        return positions[index][0] if mode == 0 else path[:positions[index].start()]

    @staticmethod
    def pad_string(value: str, length: Any, character: Any) -> str:
        if not isinstance(value, str) or np.ndim(length) or not np.isfinite(length) or int(length) != length or not 0 <= length <= 64 * 1024**2 or np.ndim(character) or int(character) != character or not 0 <= character <= 255:
            raise ValueError("PadString requires bounded length and a byte-sized character code")
        return value[:int(length)].ljust(int(length), chr(int(character)))

    @staticmethod
    def string_search(text: str, sought: str, start: Any, options: Any = 0) -> int:
        if not isinstance(text, str) or not isinstance(sought, str) or int(start) != start or start < 0 or options != 0:
            raise ValueError("Only forward, case-sensitive StrSearch is supported")
        return text.find(sought, int(start))

    def evaluate(self, source: str, special: dict[str, Any] | None = None) -> Any:
        self.owner.step()
        source = dynamic_references(source, self.evaluate)
        value = prepare_expression(source)
        for node in ast.walk(value.node):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.lower() not in self.functions:
                name = node.func.id
                if self.wave_exists(name):
                    self.functions[name.lower()] = lambda coordinate, wave_name=name: self.wave_coordinate(wave_name, coordinate)
        validate_expression(source, set(self.functions))
        return evaluate(value, self.services.resolve, special, self.functions,
                        self.wave_parameters, lambda name: self.services.wave(name).igor_path)

    def condition(self, source: str) -> bool:
        value = self.evaluate(source)
        if np.ndim(value) or isinstance(value, str) or not np.isfinite(value):
            raise ValueError("Igor conditions require a finite scalar")
        return bool(value)

    def wave_coordinate(self, name: str, coordinate: Any) -> Any:
        model = self.services.wave(name)
        if model.data.ndim != 1 or model.data.dtype.kind not in "biuf":
            raise ValueError("Wave-coordinate calls support real one-dimensional numeric waves")
        query = np.asarray(coordinate, dtype=float)
        count = model.shape[0]
        origin, delta = model.dimension_scaling[0]
        # For ordinary regular scaling a scalar query needs only its two
        # neighboring points. Keep the generic validator for arrays and exotic
        # scales (overflow, zero steps, or floating-point-collapsed coordinates).
        with np.errstate(all="ignore"):
            last = origin + delta * (count - 1)
            spacing = np.spacing(max(abs(origin), abs(last)))
        regular = (not query.ndim and count >= 2 and np.isfinite(origin)
                   and np.isfinite(delta) and np.isfinite(last)
                   and abs(delta) >= 2 * spacing and delta != 0)
        if regular:
            increasing = delta > 0
            if query <= min(origin, last):
                point = 0 if increasing else count - 2
            elif query >= max(origin, last):
                point = count - 2 if increasing else 0
            elif not np.isfinite(query):  # NaN, after the infinite end cases.
                point = 0
            else:
                point = max(0, min(int(np.floor((query - origin) / delta)), count - 2))
                left, right = origin + delta * point, origin + delta * (point + 1)
                if (query < left if increasing else query > left) and point:
                    point -= 1
                elif (query > right if increasing else query < right) and point < count - 2:
                    point += 1
                left, right = origin + delta * point, origin + delta * (point + 1)
                if not min(left, right) <= query <= max(left, right):
                    return _interpolate(coordinate, model.coordinate(0), model.data)
            coordinates = np.array([origin + delta * point, origin + delta * (point + 1)])
            # Reuse the exact existing two-point interpolation/extrapolation,
            # including exact-knot, NaN/Inf and descending-scale behavior.
            return _interpolate(coordinate, coordinates, model.data[point:point + 2])
        return _interpolate(coordinate, model.coordinate(0), model.data)

    def declare(self, kind: str, name: str, source: str, *, parameter: bool = False,
                global_: bool = False) -> None:
        key = name.lower()
        value = self.variables[key] if parameter and key in self.parameters else self.evaluate(source)
        if kind == "string":
            if not isinstance(value, str):
                raise ValueError("String parameters/variables require text")
        elif isinstance(value, str) or np.ndim(value) or not np.isfinite(value):
            raise ValueError("Variable parameters/variables require a finite number")
        if global_:
            self.set_global(name, value)
            self.scalar_aliases[key] = self.services.path(name)
        else:
            self.variables[key] = value
        self.types[key] = kind

    def set_global(self, name: str, value: Any) -> None:
        if not isinstance(value, (str, int, float, bool, np.number)) or np.ndim(value):
            raise ValueError("A global scalar must be a number or string")
        path = self.services.path(name)
        if any(wave.igor_path.casefold() == path.casefold()
               for wave in self.services.document.render_source().waves):
            raise ValueError("A global scalar cannot shadow an existing wave")
        section = "strings" if isinstance(value, str) else "variables"
        other = "variables" if section == "strings" else "strings"
        if path in self.services.environment.get(other, {}):
            raise ValueError("Cannot change the type of an existing global scalar")
        after = self.services.document.current.clone()
        after.environment.setdefault(section, {})[path] = value.item() if isinstance(value, np.generic) else value
        self.services.document.current = after

    def alias_scalar(self, kind: str, name: str, target: str) -> None:
        value = self.services.resolve(target)
        if (kind == "string") != isinstance(value, str) or np.ndim(value):
            raise ValueError("Global scalar alias type does not match its target")
        self.scalar_aliases[name.lower()] = self.services.path(target)
        self.types[name.lower()] = kind

    def button_event(self, name: str) -> None:
        self.event_code(self.variables.get(name.lower()))

    def assign_value(self, name: str, value: Any) -> None:
        key = name.lower()
        if key in self.scalar_aliases:
            self.set_global(self.scalar_aliases[key], value)
        elif key in self.variables:
            if self.types.get(key) == "string" and not isinstance(value, str):
                raise ValueError("String destination requires text")
            if self.types.get(key) == "variable" and isinstance(value, str):
                raise ValueError("Variable destination requires a number")
            self.variables[key] = value
        else:
            path = self.services.path(name)
            matches = [existing for section in ("variables", "strings")
                       for existing in self.services.environment.get(section, {})
                       if existing.casefold() == path.casefold()]
            if len(matches) != 1:
                raise ValueError("Destination must be a declared local/global scalar")
            self.set_global(matches[0], value)

    def prf_columns(self, names: str, header: str) -> None:
        """Explicit converter opt-in; never alter generic numeric import order."""
        self.owner.step()
        self.assign_value(names, reflection_columns(self.services, self.evaluate(names), self.evaluate(header)))

    def alias(self, name: str, reference: str, *, optional: bool = False, parameter: bool = False) -> None:
        key = name.lower()
        source_null = False
        if parameter and key in self.parameters:
            target = self.variables.pop(key)
            if not isinstance(target, str):
                raise ValueError("Wave parameters require a wave-name string")
        else:
            target = dynamic_references(reference, self.evaluate).strip()
            if target.startswith("'") and target.endswith("'"):
                target = target[1:-1]
            source_null = target.lower() in self.null_aliases
            target = self.aliases.get(target.lower(), target)
        path = self.services.path(target)
        try:
            if source_null:
                raise ValueError("Cannot bind a wave through a null reference")
            path = self.services.wave(path).igor_path
        except ValueError:
            if parameter:
                raise
            self.null_aliases.add(key)
            if not optional:
                print("Warning: WAVE reference is null (no wave was created): " + name +
                      ". Use Wave/Z and WaveExists to handle an absent optional wave.")
        else:
            self.null_aliases.discard(key)
        self.aliases[key] = path

    def assign(self, target: str, source: str, operator: str = "=") -> None:
        self.owner.step()
        target = dynamic_references(target, self.evaluate)
        match = re.fullmatch(r"([^\[\]]+)((?:\[[^\[\]]*\])*)", target.strip())
        if match is None:
            raise ValueError("Unsupported assignment target")
        name, suffix = match.groups()
        name = name.strip().strip("'")
        key = name.lower()
        try:
            wave = None if key in self.variables else self.services.wave(name)
        except ValueError:
            wave = None
        if wave is None:
            if suffix:
                raise ValueError("Indexed assignment requires an existing wave")
            if key in self.aliases:
                raise ValueError("Assignment requires the wave bound to this alias")
            value = self.evaluate(source)
            scalar_target = self.scalar_aliases.get(key)
            if ":" in name:
                path = self.services.path(name)
                if any(path.lower() == existing.lower() for section in ("variables", "strings") for existing in self.services.environment.get(section, {})):
                    scalar_target = path
                else:
                    raise ValueError("Qualified scalar/global assignment requires an existing global")
            if operator != "=":
                previous = self.services.resolve(scalar_target) if scalar_target else self.variables.get(key)
                if previous is None:
                    raise ValueError("Compound assignment requires an existing variable")
                value = self.compound(previous, value, operator)
            if np.ndim(value) or not isinstance(value, (str, int, float, bool, np.number)):
                raise ValueError("Local variables require a scalar; use Make for a wave")
            if self.types.get(key) == "string" and not isinstance(value, str):
                raise ValueError("Cannot assign a number to a String")
            if self.types.get(key) == "variable" and isinstance(value, str):
                raise ValueError("Cannot assign text to a Variable")
            if not isinstance(value, str) and not np.isfinite(value):
                raise ValueError("Local numeric variables must be finite")
            if scalar_target:
                self.set_global(scalar_target, value)
            else:
                self.variables[key] = value
            return
        dimensions = wave.data.ndim
        indices = [self.evaluate(index) if index.strip() else slice(None)
                   for index in re.findall(r"\[([^\]]*)\]", suffix)]
        if len(indices) > dimensions:
            raise ValueError("Too many wave assignment indices")
        checked: list[Any] = []
        for index in indices:
            if isinstance(index, slice):
                checked.append(index)
            elif np.ndim(index) == 0 and np.isfinite(index) and int(index) == index:
                dimension = len(checked)
                if self.rt_globals != 3:
                    checked.append(max(0, min(int(index), wave.shape[dimension] - 1)))
                elif 0 <= int(index) < wave.shape[dimension]:
                    checked.append(int(index))
                else:
                    raise IndexError("Wave point index is outside its valid bounds (rtGlobals=3)")
            else:
                raise ValueError("Assignment point indices must be scalar integers")
        selection = tuple(checked) if checked else (slice(None),) * dimensions
        special: dict[str, Any] = {}
        scalar_point = len(checked) == dimensions and all(isinstance(index, int) for index in checked)
        for dimension in range(dimensions):
            if scalar_point:
                points = np.int64(checked[dimension])
            else:
                shape = [1] * dimensions
                shape[dimension] = wave.shape[dimension]
                points = np.broadcast_to(np.arange(wave.shape[dimension]).reshape(shape), wave.shape)[selection]
            special["pqrs"[dimension]] = points
            origin, delta = wave.dimension_scaling[dimension]
            special["xyzt"[dimension]] = origin + delta * points
        value = self.evaluate(source, special)
        if operator != "=":
            value = self.compound(wave.data[selection], value, operator)
        data = np.array(wave.data, copy=True)
        if data.dtype.kind in "US":
            data = data.astype(np.result_type(data.dtype, np.asarray(value).dtype))
        elif data.dtype.kind in "iu":
            from materials_data_studio.tools.igor_context import assignment_values
            value = assignment_values(value, data.dtype)
        elif not np.can_cast(np.asarray(value).dtype, data.dtype, casting="same_kind"):
            raise ValueError("Wave assignment would change numeric type")
        data[selection] = value
        self.services.write([make_wave(wave.igor_path, data, wave)], overwrite=True)

    @staticmethod
    def compound(left: Any, right: Any, operator: str) -> Any:
        if isinstance(left, str) or isinstance(right, str):
            if operator == "+=" and isinstance(left, str) and isinstance(right, str) and len(left) + len(right) <= 1_000_000:
                return left + right
            raise ValueError("String compound assignment supports bounded text += only")
        operations = {"+=": np.add, "-=": np.subtract, "*=": np.multiply, "/=": np.divide}
        if operator not in operations:
            raise ValueError("Unsupported assignment operator")
        if isinstance(left, (np.ndarray, np.generic)):
            left = np.asarray(left).astype(np.complex128 if np.iscomplexobj(left) else np.float64)
        return operations[operator](left, right)

    def command(self, source: str) -> str:
        self.owner.step()
        before, context = self.services.document.current, self.owner.services.context.copy()
        local = (self.variables.copy(), self.aliases.copy(), self.scalar_aliases.copy(),
                 copy.deepcopy(self.prompts), self.types.copy(), self.null_aliases.copy())
        io_before = copy.deepcopy(self.owner._io)
        panel_before = self.owner.active_panel
        try:
            # Some virtual operations edit an environment list in place. Work
            # on a private state, including dynamic-expression evaluation, so
            # even a caught command failure cannot leak a partial panel/global.
            self.services.document.current = before.clone()
            source = dynamic_references(source, self.evaluate)
            commands = split(source, ";")
            if not commands or len(commands) > 1000:
                raise ValueError("Provide between 1 and 1000 document/virtual operations")
            messages = [self._command_one(command) for command in commands]
            return "\n".join(message for message in messages if message)
        except BaseException:
            self.services.document.current = before
            self.owner.services.context.clear()
            self.owner.services.context.update(context)
            self.services.context = context.copy()
            self.variables, self.aliases, self.scalar_aliases, self.prompts, self.types, self.null_aliases = local
            self.owner._io.__dict__.clear()
            self.owner._io.__dict__.update(io_before.__dict__)
            self.owner.active_panel = panel_before
            raise

    def _command_one(self, source: str) -> str:
        name, flags, operands = operation_head(source)
        if name in VIRTUAL_COMMANDS:
            self.owner._io.execute(self, source)
            return ""
        if name == "dowindow":
            bad = {f.name for f in flags} - {"F", "K"}
            if bad or len(flags) != 1 or flags[0].value is not None:
                raise ValueError("DoWindow supports exactly /F or /K with a literal window name")
            panels = self.services.environment.get("igor_parameter_panels", [])
            panel = next((p for p in panels if p["name"] == operands), None)
            graph = next((g for g in self.services.document.current.graphs if g.name == operands), None)
            table = next((t for t in self.services.document.render_source().table_definitions if t.name == operands), None)
            self.set_global("V_flag", int(panel is not None or graph is not None or table is not None))
            if panel:
                if flags[0].name == "K":
                    self.services.environment["igor_parameter_panels"] = [p for p in panels if p is not panel]
                else:
                    self.owner.active_panel = panel["name"]
                return ""
            if graph is None and table is None:
                return ""
            source = "KillWindow " + operands if flags[0].name == "K" else source
        if name == "make":
            initializer = re.fullmatch(r"('(?:[^']+)'|[A-Za-z]\w*)\s*=(?!=)\s*(.+)", operands)
            if initializer:
                make_source = "Make" + "".join("/" + f.name + ("=" + f.value if f.value is not None else "") for f in flags) + " " + initializer[1]
                self._canonical_command(make_source)
                self.assign(initializer[1], initializer[2])
                return ""
        return self._canonical_command(source)

    def _canonical_command(self, source: str) -> str:
        validate_command(source, set(self.functions))
        # A called converted routine can change the active graph/table. Igor's
        # active window is shared across calls, not lexical function state.
        self.services.context = self.owner.services.context.copy()
        before, context = self.services.document.current, self.services.context.copy()
        result = self._engine.execute(source)
        self.owner.services.context.clear()
        self.owner.services.context.update(self.services.context)
        if not result.success:
            raise ValueError(result.message)
        # Analysis warnings describe units/normalization, not omitted commands.
        # Unknown operations/options/flags are rejected by validation above.
        if result.status == "Unsupported":
            self.services.document.current, self.services.context = before, context
            self.owner.services.context.clear()
            self.owner.services.context.update(context)
            raise ValueError("Unsupported command details: " + "; ".join(result.warnings))
        for warning in result.warnings:
            print("Warning: " + warning)
        if result.message:
            print(result.message)
        return result.message


class IgorAPI:
    """Factory for independent converted function scopes in the same run."""
    def __init__(self, services: DocumentServices) -> None:
        self.services = services
        resources = services.environment.get(INPUT_RESOURCES_KEY, [])
        validate_resources(resources)
        services.environment.pop(INPUT_RESOURCES_KEY, None)
        self._io = VirtualTextIO(resources)
        self._interaction: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
        self.active_panel = ""
        self._steps = 0
        self.command_scope: IgorScope | None = None

    def step(self) -> None:
        self._steps += 1
        if self._steps > MAX_RUNTIME_STEPS:
            raise RuntimeError(f"Converted Igor procedure exceeded its {MAX_RUNTIME_STEPS}-step budget")

    @property
    def steps(self) -> int:
        return self._steps

    def scope(self, parameters: dict[str, Any] | None = None,
              functions: dict[str, Any] | None = None,
              wave_parameters: dict[str, tuple[int, ...]] | None = None,
              rt_globals: int = 3) -> IgorScope:
        return IgorScope(self, parameters or {}, functions or {}, wave_parameters, rt_globals)

    def command(self, source: str) -> str:
        if self.command_scope is None:
            self.command_scope = self.scope()
        return self.command_scope.command(source)

    def finish(self) -> None:
        """Make temporary-output semantics visible; never save to a host path."""
        if self._io.virtual:
            print("Run-local virtual outputs (not host files; discarded when this run closes):")
            for path, text in self._io.virtual.items():
                print(f"  {path}: {len(text.encode('utf-8'))} UTF-8 bytes")
