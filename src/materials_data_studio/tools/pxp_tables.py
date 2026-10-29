"""Whitelist-only reconstruction of Igor Edit/AppendToTable windows."""

from __future__ import annotations

import json
import re

from materials_data_studio.tools.command_language import Operation, evaluate, parse
from materials_data_studio.tools.pxp_model import PxpTable


def parse_tables(text: str) -> tuple[PxpTable, ...]:
    result: list[PxpTable] = []
    name = ""
    columns: list[str] = []
    widths: dict[str, float] = {}
    styles: dict[tuple[str, str], str] = {}

    def finish() -> None:
        if name:
            result.append(PxpTable(name, tuple(columns), tuple(widths.items()), tuple((column, key, raw) for (column, key), raw in styles.items())))

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Window "):
            finish()
            match = re.match(r"Window\s+([^()]+)\(\)\s*:\s*Table", line)
            name = match[1].strip() if match else ""
            columns, widths, styles = [], {}, {}
        elif line == "EndMacro":
            finish()
            name = ""
        elif name:
            if re.match(r"(?:Edit|AppendToTable)(?:\s|/)", line):
                args = re.sub(r"^(?:Edit|AppendToTable)", "", line).strip()
                while args.startswith("/"):
                    args = re.sub(
                        r"^/[A-Za-z]+(?:=(?:\([^)]*\)|\"[^\"]*\"|[^\s]+))?\s*", "", args, count=1
                    )
                    if args.startswith("/") and not re.match(r"/[A-Za-z]", args):
                        break
                refs = re.findall(r"'([^']+)'|\"([^\"]+)\"|([^,\s]+)", args)
                columns.extend(next(v for v in ref if v) for ref in refs)
            elif line.startswith("ModifyTable "):
                try:
                    operation = parse(line)[0]
                    if not isinstance(operation, Operation):
                        raise ValueError("ModifyTable operation required")
                    for setting in operation.arguments:
                        if setting.name.lower() != "width":
                            value = evaluate(setting.expression, lambda name: (_ for _ in ()).throw(ValueError("Literal table format required")))
                            styles[setting.target or "*", setting.name.lower()] = json.dumps(value, ensure_ascii=False)
                except (ValueError, TypeError, SyntaxError):
                    pass  # Raw recreation is retained by the import layer.
                for ref, value in re.findall(r"width\(([^)]+)\)=([0-9.]+)", line):
                    width = float(value)
                    if 0 < width <= 10000:
                        widths[ref.strip("'\"")] = width
    finish()
    return tuple(result)
