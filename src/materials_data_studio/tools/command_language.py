"""Bounded command lexer, typed AST and numeric interpreter. Never eval/exec."""
from __future__ import annotations

import ast
import json
import operator
import re
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Reference:
    path: str
    point_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class Expression:
    node: ast.expr
    references: dict[str, str]


@dataclass(frozen=True)
class Flag:
    name: str
    value: str | None = None
    expression: Expression | None = None


@dataclass(frozen=True)
class Assignment:
    target: Reference
    operator: str
    value: Expression
    declaration: str = ""


@dataclass(frozen=True)
class Setting:
    name: str
    target: str | None
    value: str
    expression: Expression | None = None
    index: int | None = None


@dataclass(frozen=True)
class Operation:
    name: str
    flags: tuple[Flag, ...]
    arguments: tuple[Any, ...]


@dataclass(frozen=True)
class Argument:
    text: str
    expression: Expression | None


def argument(text: str) -> Argument:
    try:
        value = expression(text)
    except (SyntaxError, ValueError):
        value = None
    return Argument(text, value)


def split(source: str, delimiter: str = ",", *, whitespace: bool = False) -> list[str]:
    """Igor-aware delimiters/comments; quoted URLs and semicolons are literals."""
    parts, buffer, stack, quote = [], [], [], ""
    i = 0
    while i < len(source):
        char = source[i]
        if quote:
            buffer.append(char)
            if char == "\\" and i + 1 < len(source):
                i += 1
                buffer.append(source[i])
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            buffer.append(char)
        elif source[i:i + 2] == "//":
            end = source.find("\n", i)
            i = len(source) if end < 0 else end
            continue
        elif char in "({[":
            stack.append(char)
            buffer.append(char)
        elif char in ")}]":
            if not stack or "({[".index(stack.pop()) != ")}]".index(char):
                raise ValueError("Unbalanced command delimiters")
            buffer.append(char)
        elif not stack and (char == delimiter or (whitespace and char.isspace()) or (delimiter == ";" and char == "\n")):
            parts.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
        i += 1
    if quote or stack:
        raise ValueError("Unclosed quote or bracket")
    parts.append("".join(buffer).strip())
    return [part for part in parts if part]


def reference(value: str) -> Reference:
    value = value.strip()
    bounds = re.search(r"\[(\d+)\s*,\s*(\d+)\]$", value)
    point_range = (int(bounds[1]), int(bounds[2])) if bounds else None
    if bounds:
        value = value[:bounds.start()]
        if point_range is not None and point_range[0] > point_range[1]:
            raise ValueError("Trace point range must be increasing")
    # Quoted path segments, including root:folder:'liberal name'.
    unquoted = re.sub(r"'([^']*)'", "quoted_name", value)
    if any(c in unquoted for c in ";=(){}[]\"") or any(c in value for c in "\r\n"):
        raise ValueError("Invalid object reference")
    value = re.sub(r"'([^']*)'", lambda m: m[1], value)
    if not value:
        raise ValueError("Invalid object reference")
    return Reference(value, point_range)


def expression(source: str) -> Expression:
    refs: dict[str, str] = {}
    def replace(match: re.Match[str]) -> str:
        key = f"_reference_{len(refs)}"
        refs[key] = reference(match[0]).path
        return key
    # Strings use double quotes; single quotes identify liberal wave names.
    tokens = re.split(r'("(?:\\.|[^"\\])*")', source)
    for i in range(1, len(tokens), 2):
        # Igor text controls such as \f02 / \Z16 are NOT Python form-feed
        # escapes. Decode only literal string escaping and explicit newlines.
        literal = re.sub(r'\\([\\"rnt])', lambda m: {"\\": "\\", '"': '"', "r": "\r", "n": "\n", "t": "\t"}[m[1]], tokens[i][1:-1])
        tokens[i] = json.dumps(literal)
    for i in range(0, len(tokens), 2):
        tokens[i] = re.sub(r"(?:[A-Za-z_]\w*:)+(?:[A-Za-z_]\w*|'[^']+')|'[^']+'", replace, tokens[i])
        tokens[i] = tokens[i].replace("^", "**").replace("&&", " and ").replace("||", " or ")
    tree = ast.parse("".join(tokens).strip(), mode="eval").body
    if sum(1 for _ in ast.walk(tree)) > 512:
        raise ValueError("Expression is too complex")
    return Expression(tree, refs)


FUNCTIONS = {name: getattr(np, name) for name in
             ("sin", "cos", "tan", "exp", "log10", "sqrt", "abs", "floor", "ceil")}
FUNCTIONS.update(asin=np.arcsin, acos=np.arccos, atan=np.arctan, ln=np.log, log=np.log10,
                 minimum=np.minimum, maximum=np.maximum, cmplx=lambda real, imag: np.asarray(real) + 1j * np.asarray(imag),
                 real=np.real, imag=np.imag, conj=np.conj)
OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
             ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
             ast.Gt: operator.gt, ast.Lt: operator.lt, ast.GtE: operator.ge,
             ast.LtE: operator.le, ast.Eq: operator.eq, ast.NotEq: operator.ne,
             ast.BitAnd: np.logical_and, ast.BitOr: np.logical_or}


def evaluate(value: Expression, resolve: Any, special: dict[str, Any] | None = None,
             functions: dict[str, Any] | None = None,
             wave_parameters: dict[str, tuple[int, ...]] | None = None,
             wave_reference: Any = None) -> Any:
    work = 0
    def numeric(result: Any) -> Any:
        nonlocal work
        if isinstance(result, np.ndarray) and result.dtype.kind in "biufc":
            work += result.size
            if result.size > 10_000_000 or work > 100_000_000:
                raise ValueError("Expression exceeds the bounded element/work budget")
            # Igor wave expressions calculate in double precision and convert
            # once on assignment. FFT's distinct single/double contract is separate.
            return result.astype(np.complex128 if result.dtype.kind == "c" else np.float64, copy=False)
        return result
    def visit(node: ast.AST) -> Any:
        nonlocal work
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, complex, str, bool)):
            if isinstance(node.value, int) and node.value.bit_length() > 1024:
                raise ValueError("Integer constant is too large")
            return node.value
        if isinstance(node, ast.Name):
            if node.id in (special or {}):
                return special[node.id]  # type: ignore[index]
            if node.id.lower() in {"pi", "e", "nan", "inf"}:
                return {"pi": np.pi, "e": np.e, "nan": np.nan, "inf": np.inf}[node.id.lower()]
            return numeric(resolve(value.references.get(node.id, node.id)))
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return tuple(visit(item) for item in node.elts)
        if isinstance(node, ast.UnaryOp):
            operations = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: np.logical_not}
            return operations[type(node.op)](visit(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and np.ndim(right) == 0 and abs(float(right)) > 1024:
                raise ValueError("Exponent exceeds the command limit")
            if isinstance(node.op, ast.Pow) and isinstance(left, int) and isinstance(right, int) and right > 0 and left.bit_length() * right > 4096:
                raise ValueError("Integer power exceeds the command limit")
            if isinstance(left, str) or isinstance(right, str):
                if isinstance(node.op, ast.Add) and isinstance(left, str) and isinstance(right, str) and len(left) + len(right) <= 1_000_000:
                    return left + right
                raise ValueError("Numeric arithmetic requires numeric operands")
            if np.ndim(left) or np.ndim(right):
                shape = np.broadcast_shapes(np.shape(left), np.shape(right))
                count = int(np.prod(shape, dtype=np.int64))
                work += count
                if count > 10_000_000 or work > 100_000_000:
                    raise ValueError("Expression broadcast exceeds 10 million elements")
            return OPERATORS[type(node.op)](left, right)
        if isinstance(node, ast.Compare):
            left, result = visit(node.left), True
            for op, right_node in zip(node.ops, node.comparators, strict=True):
                right = visit(right_node)
                result = np.logical_and(result, OPERATORS[type(op)](left, right))
                left = right
            return result
        if isinstance(node, ast.BoolOp):
            function = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
            result = visit(node.values[0])
            for item in node.values[1:]:
                result = function(result, visit(item))
            return result
        if isinstance(node, ast.Subscript) and not isinstance(node.slice, ast.Slice):
            data, index = visit(node.value), visit(node.slice)
            indices = index if isinstance(index, tuple) else (index,)
            if any(np.ndim(i) or int(i) != i for i in indices):
                raise ValueError("Scalar integer point indices are required")
            return data[tuple(map(int, indices))]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id.lower()
            function = (functions or {}).get(name, FUNCTIONS.get(name))
            if function is not None:
                arguments = []
                for index, arg in enumerate(node.args):
                    if index in (wave_parameters or {}).get(name, ()) and isinstance(arg, ast.Name):
                        if wave_reference is None:
                            raise ValueError("Wave arguments require a canonical wave reference")
                        arguments.append(wave_reference(value.references.get(arg.id, arg.id)))
                    else:
                        arguments.append(visit(arg))
                return numeric(function(*arguments))
        raise ValueError(f"Unsupported command expression: {type(node).__name__}")
    with np.errstate(all="ignore"):
        return visit(value.node)


def parse(source: str) -> tuple[Operation | Assignment, ...]:
    result: list[Operation | Assignment] = []
    for statement in split(source, ";"):
        declaration = re.match(r"(Variable|String)\s+(.+)", statement, re.I)
        if declaration:
            for item in split(declaration[2]):
                target, separator, value = item.partition("=")
                result.append(Assignment(reference(target), "=", expression(value if separator else '""' if declaration[1].lower() == "string" else "0"), declaration[1].lower()))
            continue
        assignment = re.match(r"((?:[^=+*/<>!]|\*(?![=]))+?)\s*(\+=|-=|\*=|/=|=)(?!=)(.*)$", statement)
        if assignment and not re.match(r"[A-Za-z]+(?:/[^\s]+)?\s", assignment[1].strip()):
            result.append(Assignment(reference(assignment[1]), assignment[2], expression(assignment[3])))
            continue
        name, flags, rest = operation_head(statement)
        if name == "curvefit":
            tokens = split(rest, whitespace=True)
            operands, parameters = [], []
            for token in tokens:
                if token.startswith("/"):
                    _, additions, leftover = operation_head("CurveFit" + token)
                    if leftover:
                        raise ValueError("Invalid CurveFit flag parameter")
                    parameters.extend(Flag("MASK" if f.name == "M" else f.name, f.value, f.expression) for f in additions)
                else:
                    operands.append(token)
            if operands and operands[0].lower() == "poly" and len(operands) >= 3:
                operands[:2] = ["poly " + operands[1]]
            flags = (*flags, *parameters)
            rest = ",".join(operands)
        args: tuple[Any, ...]
        if name in {"modifygraph", "setdrawenv", "modifyfreeaxis", "modifyimage", "modifycontour", "modifytable"}:
            image = None
            if name in {"modifyimage", "modifycontour"}:
                image_name, _, rest = rest.partition(",") if name == "modifycontour" else rest.partition(" ")
                image = reference(image_name)
            settings = []
            for item in split(rest):
                assignment = split(item, "=")
                m = re.fullmatch(r"([A-Za-z]\w*)(?:\((.*)\)|\[(\d+)\])?", assignment[0]) if len(assignment) == 2 else None
                if not m:
                    raise ValueError(f"Invalid style option: {item}")
                if m[3] is not None and name != "modifygraph":
                    raise ValueError("Indexed style options are supported only for ModifyGraph traces")
                settings.append(Setting(m[1], reference(m[2]).path if m[2] else None, assignment[1], argument(assignment[1]).expression,
                                        int(m[3]) if m[3] is not None else None))
            args = (image, tuple(settings)) if image is not None else tuple(settings)
        elif name == "appendimage":
            parts = re.split(r"\s+vs\s+", rest, maxsplit=1, flags=re.I)
            coordinates = split(parts[1].strip("{} ")) if len(parts) > 1 else []
            if coordinates and len(coordinates) != 2:
                raise ValueError("AppendImage requires vs {xEdgeWave,yEdgeWave}")
            args = (argument(parts[0]), *(argument(v) for v in coordinates))
        elif name == "errorbars":
            head, *groups = split(rest)
            operands = split(head, whitespace=True)
            if len(operands) == 1 and groups:
                operands.append(groups.pop(0))
            if len(operands) != 2 or operands[1].upper() not in {"X", "Y", "XY"}:
                raise ValueError("Use ErrorBars trace Y,wave=(low,high) or X,wave=(err,err)")
            direction = operands[1].upper()
            if len(groups) != (2 if direction == "XY" else 1):
                raise ValueError("ErrorBars requires one wave pair per direction")
            errors = []
            for group in groups:
                match = re.fullmatch(r"wave\s*=\s*\((.*)\)", group, re.I)
                pair = split(match[1]) if match else []
                if len(pair) != 2:
                    raise ValueError("ErrorBars requires wave=(low,high)")
                errors.extend(reference(value) for value in pair)
            args = (reference(operands[0]), direction, *errors)
        elif name in {"display", "appendtograph", "appendboxplot", "appendviolinplot"}:
            match = re.split(r"\s+vs\s+", rest, maxsplit=1, flags=re.I)
            args = (tuple(reference(v) for v in split(match[0])) if match[0] else (), reference(match[1]) if len(match) > 1 else None)
        elif name in {"label", "setaxis"}:
            target_match = re.fullmatch(r"([^\s,]+)\s*,?\s*(.*)", rest)
            if target_match is None:
                raise ValueError("Expected an axis name and value")
            args = (reference(target_match[1]), *(expression(v) if v != "*" else None for v in split(target_match[2])))
        elif name == "print":
            args = tuple(expression(v) for v in split(rest))
        elif name in {"textbox", "legend", "appendtext"}:
            args = (expression(rest),) if rest else ()
        else:
            # Space-separated object operands are Igor's usual Duplicate/Rename
            # syntax. Do not split expression-bearing commands at arithmetic spaces.
            spaced = name in {"appendlayoutobject", "appendmatrixcontour","cursor", "tag", "make", "duplicate", "rename", "killwaves", "redimension", "sort", "newdatafolder", "setdatafolder", "killdatafolder", "renamedatafolder", "movedatafolder", "movewave", "removefromgraph", "appendimage", "removeimage", "newfreeaxis", "killfreeaxis", "edit", "appendtotable", "removefromtable", "dowindow", "killwindow", "renamewindow"}
            args = tuple(argument(item) for item in split(rest, whitespace=spaced))
        result.append(Operation(name, tuple(flags), args))
    return tuple(result)


def operation_head(statement: str) -> tuple[str, tuple[Flag, ...], str]:
    """Read an operation/flags without assigning semantics to its operands."""
    head = re.match(r"([A-Za-z]+|\?)", statement)
    if not head:
        raise ValueError("Expected a command or assignment")
    name, rest, flags = head[1].lower(), statement[head.end():].lstrip(), []
    while rest.startswith("/"):
        match = re.match(r"/([A-Za-z]+)", rest)
        if match is None:
            raise ValueError("Invalid flag")
        end, val = match.end(), None
        if rest[end:end + 1] == "=":
            start = end = end + 1
            depth, quote = 0, ""
            while end < len(rest):
                c = rest[end]
                if quote:
                    if c == quote and rest[end - 1] != "\\":
                        quote = ""
                elif c in "\"'":
                    quote = c
                elif c in "({[":
                    depth += 1
                elif c in ")}]":
                    depth -= 1
                elif depth == 0 and (c.isspace() or c == "/"):
                    break
                end += 1
            val = rest[start:end]
        flags.append(Flag(match[1].upper(), val, argument(val).expression if val is not None else None))
        rest = rest[end:].lstrip()
    return name, tuple(flags), rest
