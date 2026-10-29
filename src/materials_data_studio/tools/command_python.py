"""Typed core-command to public Python API serialization; never a replay engine."""
from __future__ import annotations

import ast
import json
from typing import Any

from materials_data_studio.tools.command_language import (
    FUNCTIONS,
    Assignment,
    Expression,
    Operation,
    evaluate,
    parse,
)
from materials_data_studio.tools.command_services import DocumentServices


def to_python(source: str, services: DocumentServices) -> str:
    lines = ["import numpy as np", "from mds import wave, graph"]
    known = {w.igor_path for w in services.document.render_source().waves}
    declared: set[str] = set()

    def quote(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    def expr(value: Expression, target: str = "") -> str:
        if any(isinstance(n, (ast.Attribute, ast.Lambda, ast.ListComp, ast.DictComp, ast.GeneratorExp, ast.NamedExpr)) for n in ast.walk(value.node)):
            raise NotImplementedError("Unsupported expression for Python conversion")
        class References(ast.NodeTransformer):
            def visit_Call(self, node: ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords:
                    raise NotImplementedError("Unsupported call")
                if node.func.id == "cmplx" and len(node.args) == 2:
                    return ast.BinOp(self.visit(node.args[0]), ast.Add(), ast.BinOp(ast.Constant(1j), ast.Mult(), self.visit(node.args[1])))
                name = {"asin": "arcsin", "acos": "arccos", "atan": "arctan", "ln": "log", "log": "log10"}.get(node.func.id, node.func.id)
                return ast.Call(ast.Attribute(ast.Name("np", ast.Load()), name, ast.Load()), [self.visit(a) for a in node.args], [])

            def visit_Name(self, node: ast.Name):
                name = value.references.get(node.id, node.id)
                if name in {"p", "q", "r", "s", "x", "y", "z", "t"} and target:
                    return ast.parse(f"wave({quote(target)}).coordinates[{quote(name)}]", mode="eval").body
                if name in {"pi", "e", "NaN", "Inf"}:
                    code = "np." + {"NaN": "nan", "Inf": "inf"}.get(name, name)
                elif services.path(name) in known:
                    data = f"wave({quote(services.path(name))}).values"
                    code = f"{data}.astype(np.result_type({data}.dtype, np.float64))"
                elif name in declared:
                    return ast.Name(name, ast.Load())
                else:
                    try:
                        code = repr(services.resolve(name))
                    except ValueError:
                        if not name.isidentifier():
                            raise NotImplementedError("Liberal scalar name cannot be a Python variable") from None
                        return ast.Name(name, ast.Load())
                return ast.parse(code, mode="eval").body
        # Copy: serialization must not alter the typed AST used for execution.
        import copy
        return ast.unparse(ast.fix_missing_locations(References().visit(copy.deepcopy(value.node))))

    for command in parse(source):
        if isinstance(command, Assignment):
            path = services.path(command.target.path)
            if path in known and not command.declaration:
                value = expr(command.value, path)
                if command.operator != "=":
                    data = f"wave({quote(path)}).values"
                    value = f"{data}.astype(np.result_type({data}.dtype, np.float64)) {command.operator[0]} ({value})"
                lines.append(f"wave({quote(path)}).assign({value})")
            elif command.target.path.isidentifier():
                lines.append(f"{command.target.path} {command.operator} {expr(command.value)}")
                declared.add(command.target.path)
            else:
                raise NotImplementedError("Use a Python identifier for scalar variables")
            continue
        assert isinstance(command, Operation)
        flags = {f.name: f for f in command.flags}
        args = command.arguments
        def flag(name: str, default: Any = None, current_flags=flags) -> Any:
            return current_flags[name].value if name in current_flags and current_flags[name].value is not None else default
        if command.name == "make":
            shape = evaluate(flags["N"].expression, services.resolve) if "N" in flags and flags["N"].expression is not None else 128
            from materials_data_studio.tools.igor_context import checked_shape, wave_dtype
            resolved_dtype = wave_dtype(set(flags))
            shape = checked_shape(shape, resolved_dtype)
            dtype = quote(resolved_dtype.str)
            for arg in args:
                path = services.path(arg.text)
                lines.append(f"wave.create({quote(path)}, np.zeros({shape!r}, dtype={dtype}), overwrite={'O' in flags})")
                known.add(path)
        elif command.name in {"display", "appendtograph"}:
            lines.append(f"g = graph.new({quote(flag('N', 'Graph0'))})" if command.name == "display" else f"g = graph({quote(flag('W', services.context.get('graph', 'Graph0')))})")
            axis = flag("R", "right") if "R" in flags else flag("L", "left")
            x_axis = flag("T", "top") if "T" in flags else flag("B", "bottom")
            for y in args[0]:
                lines.append(f"g.append({quote(services.path(y.path))}, {quote(services.path(args[1].path)) if args[1] else 'None'}, axis={quote(axis)}, x_axis={quote(x_axis)}, x_side={quote('top' if 'T' in flags else 'bottom')})")
        elif command.name == "label":
            lines.append(f"graph.active().axis({quote(args[0].path)}).label = {expr(args[1])}")
        elif command.name == "setaxis":
            values = ["None", "None"] if "A" in flags else [expr(v) if v is not None else "None" for v in args[1:]]
            lines.append(f"graph.active().axis({quote(args[0].path)}).set_range({', '.join(values)})")
        elif command.name == "print":
            lines.append("print(" + ", ".join(expr(v) for v in args) + ")")
        else:
            raise NotImplementedError(f"Python conversion not implemented for {command.name}; edit Python explicitly")
    return "\n".join(lines) + "\n"
