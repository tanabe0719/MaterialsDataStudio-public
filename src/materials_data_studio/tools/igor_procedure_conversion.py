"""Source-only conversion of a documented Igor subset, never auto execution.

Unsupported code blocks its containing routine and callers. Independent safe
routines remain usable. The original PXP/IPF text is never edited or executed.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field, replace
from typing import Any

from materials_data_studio.tools.command_language import operation_head, reference, split
from materials_data_studio.tools.igor_procedure_runtime import (
    SAFE_COMMANDS,
    dynamic_references,
    validate_command,
    validate_expression,
)
from materials_data_studio.tools.igor_virtual_io import VIRTUAL_COMMANDS, validate_virtual_command


@dataclass(frozen=True)
class ConversionDiagnostic:
    line: int
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class Statement:
    line: int
    kind: str
    text: str
    extra: tuple[str, ...] = ()


@dataclass(frozen=True)
class Routine:
    name: str
    parameters: tuple[str, ...]
    defaults: dict[str, Any]
    diagnostics: tuple[ConversionDiagnostic, ...]
    supported: bool
    statements: tuple[Statement, ...] = field(repr=False, default=())
    parameter_types: dict[str, str] = field(default_factory=dict)
    line: int = 1
    return_type: str = "number"


@dataclass(frozen=True)
class ConversionReport:
    source: str
    routines: tuple[Routine, ...]
    diagnostics: tuple[ConversionDiagnostic, ...]
    rt_globals: int = 1


def logical_statements(source: str) -> tuple[tuple[int, str], ...]:
    """Keep physical line numbers while handling comments and continuations."""
    if len(source.encode("utf-8")) > 1024**2:
        raise ValueError("Igor source exceeds the 1 MiB conversion limit")
    result: list[tuple[int, str]] = []
    buffer: list[str] = []
    quote = ""
    depth = 0
    block_comment = False
    line, start, i = 1, 1, 0
    while i < len(source):
        char = source[i]
        pair = source[i:i + 2]
        if block_comment:
            if pair == "*/":
                block_comment = False
                i += 2
            else:
                if char == "\n":
                    line += 1
                i += 1
            continue
        if quote:
            if char in "\r\n":
                raise ValueError(f"Line {line}: unclosed Igor string")
            buffer.append(char)
            if char == "\\" and i + 1 < len(source):
                i += 1
                buffer.append(source[i])
            elif char == quote:
                quote = ""
            i += 1
            continue
        if pair == "//":
            end = source.find("\n", i)
            i = len(source) if end < 0 else end
            continue
        if pair == "/*":
            block_comment = True
            buffer.append(" ")
            i += 2
            continue
        if char in "\"'":
            if not buffer:
                start = line
            quote = char
            buffer.append(char)
        elif char == "\\" and i + 1 < len(source) and source[i + 1] in "\r\n":
            if source[i + 1] == "\r" and source[i + 2:i + 3] == "\n":
                i += 1
            i += 2
            line += 1
            buffer.append(" ")
            continue
        elif char in "([{":
            depth += 1
            buffer.append(char)
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                raise ValueError(f"Line {line}: unbalanced Igor delimiters")
            buffer.append(char)
        elif (char == ";" and not depth) or char == "\n":
            text = "".join(buffer).strip()
            if text:
                result.append((start, text))
                if len(result) > 10_000:
                    raise ValueError("Igor source exceeds the 10000-statement conversion limit")
            buffer = []
            if char == "\n":
                line += 1
            start = line
        else:
            if not buffer:
                start = line
            buffer.append(char)
        i += 1
    if quote or block_comment or depth:
        raise ValueError(f"Line {line}: unclosed Igor literal/comment/delimiter")
    if text := "".join(buffer).strip():
        result.append((start, text))
    return tuple(result)


def _header(text: str) -> re.Match[str] | None:
    return re.fullmatch(r"(?:Function(?:/[A-Za-z]+)?|Proc|Macro)\s+([A-Za-z][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?::\s*\w+)?", text, re.I)


def _assignment(text: str) -> tuple[str, str, str] | None:
    # '=' inside a dynamic $(...) name/string is not an assignment separator.
    quote, depth = "", 0
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "=" and depth == 0 and text[i - 1:i] not in "=!<>" and text[i + 1:i + 2] != "=":
            target, operator = text[:i].rstrip(), "="
            if target[-1:] in {"+", "-", "*", "/"}:
                operator, target = target[-1:] + "=", target[:-1].rstrip()
            return target, operator, text[i + 1:].strip()
        i += 1
    return None


def _calls(source: str, routines: set[str]) -> set[str]:
    # String contents must never become apparent dependencies/commands.
    clean = re.sub(r'"(?:[^"\\]|\\.)*"|\x27[^\x27]*\x27', "", source)
    return {n.lower() for n in re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\(", clean)
            if n.lower() in routines}


def _prf_compatibility(statements: list[Statement], diagnostics: list[ConversionDiagnostic]) -> None:
    """Recognize a legacy PRF pipeline by operations, never private identifiers."""
    headers = [s.text for s in statements if s.kind == "declare" and s.extra[0] == "string"
               and re.fullmatch(r'\s*([A-Za-z]\w*)\s*\[\s*0\s*,\s*strsearch\s*\(\s*\1\s*,\s*" 999"\s*,\s*0\s*\)\s*\]\s*', s.extra[1], re.I)]
    loads = [i for i, s in enumerate(statements) if s.kind == "command"
             and re.match(r"LoadWave(?:/|\s)", s.text, re.I)
             and any(flag.name == "G" for flag in operation_head(s.text)[1])]
    if len(headers) != 1 or len(loads) < 2:
        return
    groups: dict[str, list[tuple[int, int, str]]] = {}
    for i, statement in enumerate(statements):
        if statement.kind != "command":
            continue
        match = re.fullmatch(r'Rename\s+\$\s*StringFromList\s*\(\s*(\d+)\s*,\s*([A-Za-z]\w*)\s*,\s*";"\s*\)\s*,\s*.+', statement.text, re.I)
        if match:
            groups.setdefault(match[2].lower(), []).append((i, int(match[1]), match[2]))
    insertions = []
    for key, group in groups.items():
        if [index for _i, index, _name in group] not in ([0, 1, 2, 4, 5, 6], [0, 1, 2, 4, 5, 6, 9]):
            continue
        first, _index, spelling = group[0]
        declarations = [i for i, s in enumerate(statements[:first]) if s.kind == "declare"
                        and s.text.lower() == key and s.extra[0] == "string"
                        and s.extra[1].lower() == "s_wavenames"]
        if len(declarations) != 1 or sum(i < declarations[0] for i in loads) < 2:
            continue
        insertions.append((first, Statement(statements[first].line, "prf_columns", spelling, (headers[0],))))
    if len(insertions) != 1:
        return
    index, statement = insertions[0]
    statements.insert(index, statement)
    diagnostics.append(ConversionDiagnostic(statement.line,
        "Explicit legacy PRF reflection compatibility will validate 3/4-index column layouts before renaming. "
        "An additional index is retained last, with a runtime warning; native LoadWave/G is unchanged.", "warning"))


def _analyze_routine(name: str, parameters: tuple[str, ...], body: list[tuple[int, str]],
                     names: set[str], line: int, rt_globals: int = 1) -> Routine:
    statements: list[Statement] = []
    diagnostics: list[ConversionDiagnostic] = []
    defaults: dict[str, Any] = {}
    types: dict[str, str] = {}
    stack: list[str] = []
    parameter_names = {p.lower() for p in parameters}
    parameter_spelling = {p.lower(): p for p in parameters}
    if_else: list[bool] = []
    wave_names: set[str] = set()
    point_counts: set[str] = set()
    for _number, text in body:
        declaration = re.fullmatch(r"Wave(?:/[A-Za-z]+)*\s+(.+)", text, re.I)
        if declaration:
            wave_names.update(item.partition("=")[0].strip().lower() for item in split(declaration[1]))
        if re.match(r"Make(?:/|\s)", text, re.I):
            try:
                _name, _flags, operands = operation_head(text)
                wave_names.update(re.findall(r"^[A-Za-z]\w*", operands))
            except ValueError:
                pass
        declaration_items = split(re.sub(r"^Variable\s+", "", text, flags=re.I)) if re.match(r"Variable\s+", text, re.I) else [text]
        for item in declaration_items:
            count = re.fullmatch(r"([A-Za-z]\w*)\s*=\s*numpnts\s*\(.+\)", item, re.I)
            if count:
                point_counts.add(count[1].lower())

    def check(source: str) -> None:
        # Also validate expressions hidden inside $(...), not only placeholders.
        dynamic_references(source, lambda text: (validate_expression(text, names | wave_names), "mdsDynamicReference")[1])
        validate_expression(source, names | wave_names)

    for number, text in body:
        try:
            head = re.match(r"([A-Za-z]+)", text)
            keyword = head[1].lower() if head else ""
            declaration = re.fullmatch(r"(Variable|String|Wave)(/[A-Za-z]+)*\s+(.+)", text, re.I)
            if declaration:
                kind = declaration[1].lower()
                flags = [flag.upper() for flag in re.findall(r"/([A-Za-z]+)", text[:declaration.start(3)])]
                if any(flag.upper() not in ({"T", "D", "Z"} if kind == "wave" else {"G"}) for flag in flags):
                    raise ValueError("Unsupported scalar/wave declaration flag")
                for item in split(declaration[3]):
                    assignment = _assignment(item)
                    target, value = (assignment[0], assignment[2]) if assignment else (item.strip(), "" if kind == "wave" else '\"\"' if kind == "string" else "0")
                    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", target):
                        raise ValueError("Declarations need simple local names")
                    parameter = target.lower() in parameter_names
                    if parameter:
                        types[target.lower()] = kind
                    if kind == "wave":
                        value = value or target
                        reference(dynamic_references(value, lambda expr: (check(expr), "mdsDynamicReference")[1]))
                        statements.append(Statement(number, "alias", target,
                                                    (value, str("Z" in flags), str(parameter))))
                    else:
                        check(value)
                        if parameter and assignment:
                            try:
                                from materials_data_studio.tools.command_language import expression
                                literal = ast.literal_eval(expression(value).node)
                                if isinstance(literal, (str, int, float, bool)) and (isinstance(literal, str) or math.isfinite(literal)):
                                    defaults[parameter_spelling[target.lower()]] = literal
                            except (ValueError, SyntaxError):
                                pass
                        statements.append(Statement(number, "declare", target, (kind, value, str(parameter), str("G" in flags))))
                continue
            scalar_alias = re.fullmatch(r"(NVAR|SVAR)\s+([A-Za-z]\w*)\s*=\s*(.+)", text, re.I)
            if scalar_alias:
                reference(scalar_alias[3])
                statements.append(Statement(number, "scalar_alias", scalar_alias[2],
                                            ("variable" if scalar_alias[1].lower() == "nvar" else "string", scalar_alias[3])))
                continue
            event = re.fullmatch(r"STRUCT\s+WMButtonAction\s*&([A-Za-z]\w*)", text, re.I)
            if event:
                key = event[1].lower()
                if key not in parameter_names:
                    raise ValueError("Button event must be a declared routine parameter")
                types[key] = "button_event"
                defaults[parameter_spelling[key]] = {"eventCode": 2}
                statements.append(Statement(number, "button_event", event[1]))
                continue
            if keyword in {"pauseupdate", "delayupdate", "silent"}:
                if not re.fullmatch(r"(?:PauseUpdate|DelayUpdate|Silent)(?:\s+\d+)?", text, re.I):
                    raise ValueError("Unsupported display scheduling syntax")
                statements.append(Statement(number, "comment", "Display refresh is deferred until successful completion"))
                continue
            if keyword in {"if", "elseif"}:
                match = re.fullmatch(r"(?:if|elseif)\s*\((.*)\)", text, re.I)
                if match is None:
                    raise ValueError("Use an Igor if(condition) block")
                check(match[1])
                if keyword == "if":
                    stack.append("if")
                    if_else.append(False)
                    if len(stack) > 48:
                        raise ValueError("Igor block nesting exceeds 48 levels")
                elif not stack or stack[-1] != "if":
                    raise ValueError("elseif without a matching if")
                elif if_else[-1]:
                    raise ValueError("elseif cannot follow else")
                statements.append(Statement(number, keyword, match[1]))
                continue
            if keyword == "else":
                if text.lower() != "else" or not stack or stack[-1] != "if":
                    raise ValueError("else without a matching if")
                if if_else[-1]:
                    raise ValueError("Duplicate else in the same if block")
                if_else[-1] = True
                statements.append(Statement(number, "else", ""))
                continue
            if keyword in {"endif", "endfor"}:
                expected = "if" if keyword == "endif" else "for"
                if text.lower() != keyword or not stack or stack.pop() != expected:
                    raise ValueError("Mismatched Igor block terminator")
                if expected == "if":
                    if_else.pop()
                statements.append(Statement(number, keyword, ""))
                continue
            if keyword == "for":
                match = re.fullmatch(r"for\s*\((.*)\)", text, re.I)
                parts = split(match[1], ";") if match else []
                if len(parts) != 3:
                    raise ValueError("Use for(initialization; scalarCondition; increment)")
                init, condition, step = parts
                if _assignment(init) is None or not (_assignment(step) or re.fullmatch(r"[A-Za-z]\w*(?:\+\+|--)", step)):
                    raise ValueError("Unsupported for-loop initialization/increment")
                check(condition)
                inclusive = re.fullmatch(r"[A-Za-z]\w*\s*<=\s*(.+)", condition)
                if rt_globals == 3 and inclusive and (inclusive[1].strip().lower() in point_counts or re.fullmatch(r"numpnts\s*\(.+\)", inclusive[1], re.I)):
                    diagnostics.append(ConversionDiagnostic(number, "Potential one-past-end wave access: an inclusive loop reaches numpnts(w), but the last valid point is numpnts(w)-1. Review a copy of this source; MDS does not silently clip indices.", "warning"))
                for clause in (init, step):
                    assignment = _assignment(clause)
                    if assignment:
                        if not re.fullmatch(r"[A-Za-z]\w*", assignment[0]):
                            raise ValueError("Loop control must be a scalar local variable")
                        check(assignment[2])
                stack.append("for")
                if len(stack) > 48:
                    raise ValueError("Igor block nesting exceeds 48 levels")
                statements.append(Statement(number, "for", init, (condition, step)))
                continue
            if keyword == "break":
                if text.lower() != "break" or "for" not in stack:
                    raise ValueError("break outside a supported loop")
                statements.append(Statement(number, "break", ""))
                continue
            if keyword == "return":
                result = text[len(head[0]):].strip() if head else ""
                check(result or "0")
                statements.append(Statement(number, "return", result or "0"))
                continue
            if len(stack) > 48:
                raise ValueError("Igor block nesting exceeds 48 levels")
            increment = re.fullmatch(r"([A-Za-z]\w*)(\+\+|--)", text)
            if increment:
                statements.append(Statement(number, "assign", increment[1], ("1", "+=" if increment[2] == "++" else "-=")))
                continue
            # Operation assignments (ModifyGraph rgb=..., Make/N=...) are not
            # local variable assignments merely because their syntax has '='.
            assignment = _assignment(text)
            is_operation = keyword in SAFE_COMMANDS | VIRTUAL_COMMANDS
            if assignment and not is_operation:
                target, operator, value = assignment
                target_text = dynamic_references(target, lambda expr: (check(expr), "mdsDynamicReference")[1])
                if not re.fullmatch(r"(?:[A-Za-z]\w*(?::(?:[A-Za-z]\w*|'[^']+'))*|'[^']+')(?:\[[^\[\]]*\])*", target_text):
                    raise ValueError("Unsupported assignment target")
                for index in re.findall(r"\[([^\]]+)\]", target_text):
                    check(index)
                check(value)
                statements.append(Statement(number, "assign", target, (value, operator)))
                continue
            call = re.fullmatch(r"([A-Za-z]\w*)\s*\((.*)\)", text)
            if call and call[1].lower() in names:
                check(text)
                statements.append(Statement(number, "evaluate", text))
                continue
            if keyword == "make":
                _name, flags, operands = operation_head(text)
                initializer = re.fullmatch(r"('(?:[^']+)'|[A-Za-z]\w*)\s*=(?!=)\s*(.+)", operands)
                if initializer:
                    check(initializer[2])
                    make_source = "Make" + "".join("/" + f.name + ("=" + f.value if f.value is not None else "") for f in flags) + " " + initializer[1]
                    validate_command(make_source, names | wave_names)
                    statements.append(Statement(number, "command", make_source))
                    statements.append(Statement(number, "assign", initializer[1], (initializer[2], "=")))
                    continue
            if keyword in VIRTUAL_COMMANDS:
                validate_virtual_command(text)
                _name, flags, operands = operation_head(text)
                for flag in flags:
                    if flag.value is not None and flag.name not in {"N", "P"}:
                        check("(" + flag.value.strip("{} ") + ")" if flag.value.startswith("{") else flag.value)
                if keyword == "button":
                    callback = re.search(r"(?:^|,)\s*proc\s*=\s*([A-Za-z]\w*)", operands, re.I)
                    if callback is None or callback[1].lower() not in names:
                        raise ValueError("Button proc must reference a converted routine")
                if keyword in {"loadwave", "deletefile", "abort", "fstatus"} or (keyword == "close" and operands):
                    check(operands)
                elif keyword in {"fbinread", "fbinwrite", "fprintf"}:
                    for operand in split(operands):
                        check(operand)
                elif keyword == "open":
                    filename = re.search(r"\s+as\s+(.+)", operands, re.I)
                    if filename:
                        check(filename[1])
                statements.append(Statement(number, "command", text))
                continue
            # Reject unknown operations / all I/O, panels, XOP and Execute.
            dynamic_references(text, lambda expr: (check(expr), "mdsDynamicReference")[1])
            validate_command(text, names | wave_names)
            statements.append(Statement(number, "command", text))
        except (ValueError, SyntaxError, IndexError) as exc:
            diagnostics.append(ConversionDiagnostic(number, str(exc)))
    if stack:
        diagnostics.append(ConversionDiagnostic(body[-1][0] if body else line, "Unclosed Igor control-flow block"))
    _prf_compatibility(statements, diagnostics)
    return Routine(name, parameters, defaults, tuple(diagnostics),
                   not any(d.severity == "error" for d in diagnostics), tuple(statements), types, line)


def analyze_procedures(source: str) -> ConversionReport:
    """Inspect source only. No file/network access and no imported execution."""
    try:
        lines = logical_statements(source)
    except ValueError as exc:
        return ConversionReport(source, (), (ConversionDiagnostic(1, str(exc)),))
    raw: list[tuple[str, tuple[str, ...], list[tuple[int, str]], int]] = []
    diagnostics: list[ConversionDiagnostic] = []
    current: tuple[str, tuple[str, ...], list[tuple[int, str]], int] | None = None
    loose: list[tuple[int, str]] = []
    skip_menu = False
    string_returns: set[str] = set()
    rt_globals = 1
    for line, text in lines:
        if text.startswith("#pragma"):
            pragma = re.fullmatch(r"#pragma\s+rtGlobals\s*=\s*([123])", text, re.I)
            if pragma:
                rt_globals = int(pragma[1])
            elif not re.match(r"#pragma\s+(?:version|IgorVersion|TextEncoding|DefaultTab|hide)\b", text, re.I):
                diagnostics.append(ConversionDiagnostic(line, "Unsupported Igor pragma; review module/runtime semantics"))
            continue
        match = _header(text)
        if match:
            if current is not None:
                diagnostics.append(ConversionDiagnostic(line, "Nested/unclosed routine declaration"))
                current[2].append((line, "UnclosedNestedRoutine"))
            parameters = tuple(split(match[2]))
            if len(parameters) > 32 or any(not re.fullmatch(r"[A-Za-z]\w*", p) for p in parameters) or len({p.lower() for p in parameters}) != len(parameters):
                diagnostics.append(ConversionDiagnostic(line, "Unsupported/duplicate routine parameter syntax"))
                current = (match[1], (), [(line, "UnsupportedParameterDeclaration")], line)
            else:
                current = (match[1], parameters, [], line)
            raw.append(current)
            header_kind = text.split(None, 1)[0].lower()
            if header_kind == "function/s":
                string_returns.add(match[1].lower())
            if "/" in header_kind and header_kind not in {"function/s"}:
                current[2].append((line, "UnsupportedFunctionReturnType"))
            if len(raw) > 256:
                return ConversionReport(source, (), (ConversionDiagnostic(line, "Igor source exceeds the 256-routine limit"),))
            continue
        if current is not None:
            if text.lower() in {"end", "endmacro"}:
                current = None
            else:
                current[2].append((line, text))
            continue
        if re.match(r"menu\b", text, re.I):
            skip_menu = True
            diagnostics.append(ConversionDiagnostic(line, "Igor menu registration is not executed; choose a routine explicitly", "info"))
            continue
        if skip_menu:
            if text.lower() == "end":
                skip_menu = False
            continue
        loose.append((line, text))
    if current is not None:
        diagnostics.append(ConversionDiagnostic(current[3], "Routine has no End/EndMacro"))
        current[2].append((current[3], "UnclosedRoutine"))
    if loose and not raw:
        raw.append(("Main", (), loose, loose[0][0]))
    elif loose:
        diagnostics.extend(ConversionDiagnostic(line, "Top-level declaration/directive is not executed", "info") for line, _ in loose)
    names = {r[0].lower() for r in raw}
    duplicate_names = {n for n in names if sum(r[0].lower() == n for r in raw) > 1}
    routines = [_analyze_routine(name, params, body, names, line, rt_globals) for name, params, body, line in raw]
    routines = [replace(r, return_type="string") if r.name.lower() in string_returns else r for r in routines]
    for index, routine in enumerate(routines):
        if routine.name.lower() in duplicate_names:
            diagnostic = ConversionDiagnostic(routine.line, "Duplicate routine name (Igor names are case-insensitive)")
            routines[index] = replace(routine, supported=False, diagnostics=(*routine.diagnostics, diagnostic))
    dependencies = {r.name.lower(): set().union(*(_calls(s.text + " " + " ".join(s.extra), names) for s in r.statements)) if r.statements else set() for r in routines}
    for routine in routines:
        for statement in routine.statements:
            if re.match(r"button\b", statement.text, re.I):
                callback = re.search(r"(?:^|,)\s*proc\s*=\s*([A-Za-z]\w*)", statement.text, re.I)
                if callback:
                    dependencies[routine.name.lower()].add(callback[1].lower())
    # Recursion can otherwise evade a per-scope budget by creating new scopes.
    recursion_cache: dict[str, bool] = {}
    def recursive(name: str, trail: set[str]) -> bool:
        if name in trail:
            return True
        if name not in recursion_cache:
            recursion_cache[name] = any(recursive(dep, trail | {name}) for dep in dependencies.get(name, set()))
        return recursion_cache[name]
    for index, routine in enumerate(routines):
        if recursive(routine.name.lower(), set()):
            routines[index] = replace(routine, supported=False,
                                      diagnostics=(*routine.diagnostics, ConversionDiagnostic(routine.line, "Recursive routine dependencies are unsupported")))
    for _ in routines:
        blocked = {r.name.lower() for r in routines if not r.supported}
        changed = False
        for index, routine in enumerate(routines):
            bad = dependencies.get(routine.name.lower(), set()) & blocked
            if routine.supported and bad:
                routines[index] = replace(routine, supported=False,
                                          diagnostics=(*routine.diagnostics, ConversionDiagnostic(routine.line, "Calls unsupported routine(s): " + ", ".join(sorted(bad)))))
                changed = True
        if not changed:
            break
    return ConversionReport(source, tuple(routines), tuple(diagnostics), rt_globals)


def build_python(report: ConversionReport, entry: str, arguments: list[Any]) -> str:
    """Return reviewable Python for an explicit later Run All; never execute it."""
    from materials_data_studio.tools.procedure_policy import validate
    selected = next((r for r in report.routines if r.name.lower() == entry.lower()), None)
    if selected is None or not selected.supported:
        raise ValueError("Choose a supported routine; review source-line diagnostics")
    if not isinstance(arguments, list) or len(arguments) > len(selected.parameters):
        raise ValueError("Arguments must be a JSON list matching the routine parameters")
    actual = list(arguments)
    for parameter in selected.parameters[len(actual):]:
        if parameter not in selected.defaults:
            raise ValueError("Missing argument: " + parameter)
        actual.append(selected.defaults[parameter])
    for parameter, value in zip(selected.parameters, actual, strict=True):
        if selected.parameter_types.get(parameter.lower()) == "button_event":
            if not isinstance(value, dict) or value != {"eventCode": 2}:
                raise ValueError("Button callbacks require the explicit eventCode=2 event object")
            continue
        if not isinstance(value, (str, int, float, bool)) or (isinstance(value, str) and len(value) > 10000) or (not isinstance(value, str) and not math.isfinite(value)):
            raise ValueError("Arguments must be finite numbers or short text/wave-name strings")
        kind = selected.parameter_types.get(parameter.lower(), "")
        if (kind in {"string", "wave"} and not isinstance(value, str)) or (kind == "variable" and isinstance(value, str)):
            raise ValueError("Argument type does not match Igor declaration: " + parameter)
    supported = [r for r in report.routines if r.supported]
    functions = {r.name.lower(): f"mdsRoutine{index + 1}" for index, r in enumerate(supported)}
    registry = "{" + ", ".join(repr(name) + ": " + pyname for name, pyname in functions.items()) + "}"
    wave_parameters = {r.name.lower(): tuple(i for i, p in enumerate(r.parameters)
                        if r.parameter_types.get(p.lower()) == "wave") for r in supported}
    wave_parameters = {name: indices for name, indices in wave_parameters.items() if indices}
    lines = ["# Converted Igor subset. Review diagnostics and arguments before Run All.",
             "# Original source is unchanged; prepared text/temporary buffers only, no host I/O, Execute or XOPs.",
             "from mds import igor", ""]

    def assignment(text: str, indent: str) -> None:
        value = _assignment(text)
        if value:
            target, operator, expr = value
        else:
            match = re.fullmatch(r"([A-Za-z]\w*)(\+\+|--)", text)
            if match is None:
                raise ValueError("Invalid loop increment")
            target, expr, operator = match[1], "1", "+=" if match[2] == "++" else "-="
        lines.append(f"{indent}scope.assign({target!r}, {expr!r}, {operator!r})")

    for routine in supported:
        pyargs = [f"mdsArgument{index + 1}" for index in range(len(routine.parameters))]
        lines.extend([f"# Igor routine: {routine.name}",
                      f"def {functions[routine.name.lower()]}({', '.join(pyargs)}):"])
        params = "{" + ", ".join(repr(name) + ": " + py for name, py in zip(routine.parameters, pyargs, strict=True)) + "}"
        wave_keyword = f", wave_parameters={wave_parameters!r}" if wave_parameters else ""
        lines.append(f"    scope = igor.scope({params}, functions={registry}{wave_keyword}, rt_globals={report.rt_globals})")
        depth = 1
        blocks: list[tuple[str, str]] = []
        for statement in routine.statements:
            kind = statement.kind
            if kind in {"endif", "endfor"}:
                block, step = blocks.pop()
                if block == "for":
                    assignment(step, "    " * depth)
                depth -= 1
                continue
            if kind in {"else", "elseif"}:
                depth -= 1
            indent = "    " * depth
            lines.append(f"{indent}# Igor line {statement.line}")
            if kind == "declare":
                type_name, expr, parameter, global_ = statement.extra
                lines.append(f"{indent}scope.declare({type_name!r}, {statement.text!r}, {expr!r}, parameter={parameter}, global_={global_})")
            elif kind == "alias":
                target, optional, parameter = statement.extra
                lines.append(f"{indent}scope.alias({statement.text!r}, {target!r}, optional={optional}, parameter={parameter})")
            elif kind == "assign":
                expr, operator = statement.extra
                lines.append(f"{indent}scope.assign({statement.text!r}, {expr!r}, {operator!r})")
            elif kind == "scalar_alias":
                type_name, target = statement.extra
                lines.append(f"{indent}scope.alias_scalar({type_name!r}, {statement.text!r}, {target!r})")
            elif kind == "button_event":
                lines.append(f"{indent}scope.button_event({statement.text!r})")
            elif kind == "prf_columns":
                lines.append(f"{indent}scope.prf_columns({statement.text!r}, {statement.extra[0]!r})")
            elif kind == "for":
                condition, step = statement.extra
                assignment(statement.text, indent)
                lines.append(f"{indent}while scope.condition({condition!r}):")
                lines.append(indent + "    pass")
                blocks.append(("for", step))
                depth += 1
            elif kind in {"if", "elseif"}:
                lines.append(f"{indent}{'if' if kind == 'if' else 'elif'} scope.condition({statement.text!r}):")
                lines.append(indent + "    pass")
                if kind == "if":
                    blocks.append(("if", ""))
                depth += 1
            elif kind == "else":
                lines.append(indent + "else:")
                lines.append(indent + "    pass")
                depth += 1
            elif kind == "break":
                lines.append(indent + "break")
            elif kind == "return":
                lines.append(f"{indent}return scope.evaluate({statement.text!r})")
            elif kind == "comment":
                lines.append(indent + "# " + statement.text)
            elif kind in {"command", "evaluate"}:
                lines.append(f"{indent}scope.{kind}({statement.text!r})")
        lines.extend(["    return " + ('\"\"' if routine.return_type == "string" else "0"), ""])
    lines.append("# Explicit entry point; runs only when Run All is pressed.")
    lines.append(functions[selected.name.lower()] + "(" + ", ".join(repr(v) for v in actual) + ")")
    output = "\n".join(lines) + "\n"
    validate(output)
    return output
