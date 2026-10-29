"""Capability-limited text resources for converted Igor procedures.

No operation in this module opens a host path. Selected input text is immutable;
temporary writes and deletions affect only this run's bounded memory buffers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np

from materials_data_studio.tools.command_language import operation_head, split
from materials_data_studio.tools.igor_general_text import parse_general_text

INPUT_RESOURCES_KEY = "igor_input_resources"
MAX_RESOURCE_BYTES = 64 * 1024**2
VIRTUAL_COMMANDS = frozenset({"newpath", "open", "close", "fstatus", "fbinread",
                              "fbinwrite", "fprintf", "deletefile", "loadwave", "abort",
                              "newpanel", "groupbox", "setvariable", "button", "tilewindows",
                              "prompt", "doprompt", "doalert"})
VIRTUAL_FLAGS = {
    "newpath": ({"O", "M"}, {"M"}),
    "open": ({"R", "A", "D", "F", "M"}, {"F", "M"}),
    "close": ({"A"}, set()), "loadwave": ({"G", "D", "N", "A", "O", "P", "L", "Q"}, {"P", "L"}),
    "newpanel": ({"W", "N"}, {"W", "N"}),
    "tilewindows": ({"O", "C"}, {"O"}),
    "doprompt": ({"HELP"}, {"HELP"}), "doalert": ({"T"}, {"T"}),
}


def validate_virtual_command(source: str) -> None:
    name, flags, rest = operation_head(source)
    if name not in VIRTUAL_COMMANDS:
        raise ValueError("Unsupported virtual operation: " + name)
    names = [f.name for f in flags]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate virtual-operation flags")
    allowed, required = VIRTUAL_FLAGS.get(name, (set(), set()))
    if set(names) - allowed:
        raise ValueError("Unsupported " + name + " flag(s): " + ", ".join(sorted(set(names) - allowed)))
    for flag in flags:
        if flag.name in required and flag.value is None:
            raise ValueError("Virtual flag /" + flag.name + " requires a value")
        optional = name == "loadwave" and flag.name in {"N", "A"}
        if flag.value is not None and flag.name not in required and not optional:
            raise ValueError("Virtual flag /" + flag.name + " does not accept a value")
    if name == "open" and not re.fullmatch(r"[A-Za-z]\w*(?:\s+as\s+.+)?", rest, re.I):
        raise ValueError("Use Open [flags] handle [as stringExpression]")
    if name == "open" and "D" not in names and "R" not in names and not re.search(r"\s+as\s+", rest, re.I):
        raise ValueError("Virtual output Open requires 'as stringExpression'")
    if name == "prompt":
        parts = split(rest)
        if len(parts) not in {2, 4} or not re.fullmatch(r"[A-Za-z]\w*", parts[0]) or (len(parts) == 4 and parts[2].lower() != "popup"):
            raise ValueError("Use Prompt variable,label [,popup,choices]")
    if name == "doprompt":
        parts = split(rest)
        if not 2 <= len(parts) <= 11 or any(not re.fullmatch(r"[A-Za-z]\w*", p) for p in parts[1:]) or len({p.lower() for p in parts[1:]}) != len(parts) - 1:
            raise ValueError("DoPrompt requires a title and 1–10 distinct prompted variable names")
    if name == "doalert" and len(split(rest)) != 2:
        raise ValueError("DoAlert requires type,text")
    if name in {"fstatus", "fbinread", "fbinwrite", "fprintf", "deletefile", "loadwave", "abort"} and not rest:
        raise ValueError(name + " requires operands")


def validate_resources(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError("Prepared input resources must be a list of at most 1000 text files")
    result: list[dict[str, str]] = []
    seen_paths, seen_names, total = set(), set(), 0
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"name", "path", "text"} or any(not isinstance(v, str) for v in entry.values()):
            raise ValueError("Malformed prepared input resource")
        name, path, text = entry["name"], entry["path"], entry["text"]
        if not name or not path or len(name) > 1024 or len(path) > 4096 or any(c in name + path for c in "\0\r\n"):
            raise ValueError("Prepared input names/paths must be nonempty plain strings")
        if any(c in name for c in "/\\:"):
            raise ValueError("Prepared input name must be a basename, not a path")
        if "\0" in text:
            raise ValueError("Only text input resources are supported (NUL bytes found)")
        key = canonical_path(path)
        if key in seen_paths or name.casefold() in seen_names:
            raise ValueError("Ambiguous duplicate prepared input path/basename")
        seen_paths.add(key)
        seen_names.add(name.casefold())
        total += len(text.encode("utf-8"))
        if total > MAX_RESOURCE_BYTES:
            raise ValueError("Prepared input text exceeds the 64 MiB limit")
        result.append({"name": name, "path": path, "text": text})
    return result


def canonical_path(path: str) -> str:
    if not isinstance(path, str) or not path or any(c in path for c in "\0\r\n"):
        raise ValueError("Virtual path must be a nonempty plain string")
    # This is a string identifier only. Do not Path.resolve, stat, open, mkdir,
    # expand environment variables or follow a filesystem/network location.
    return re.sub(r"/+", "/", path.replace("\\", "/").replace(":", "/")).casefold()


@dataclass
class VirtualHandle:
    path: str
    position: int
    readable: bool
    writable: bool


class VirtualTextIO:
    def __init__(self, resources: Any) -> None:
        self.inputs = validate_resources(resources)
        self.input_map = {canonical_path(e["path"]): e for e in self.inputs}
        self.virtual: dict[str, str] = {}
        self.paths: set[str] = set()
        self.path_collections: dict[str, set[str]] = {}
        self.handles: dict[int, VirtualHandle] = {}
        self.next_handle, self.dialog_index = 1, 0
        self.input_bytes = sum(len(e["text"].encode("utf-8")) for e in self.inputs)
        self.virtual_bytes = 0

    def find_path(self, path: str) -> str:
        key = canonical_path(path)
        if key in self.virtual or key in self.input_map:
            return key
        matches = [canonical_path(e["path"]) for e in self.inputs if e["name"].casefold() == path.casefold()]
        if len(matches) == 1:
            return matches[0]
        raise PermissionError("Input was not prepared in the MDS GUI (no host file access): " + path)

    def accept_resources(self, value: Any) -> None:
        """Install only bounded snapshots returned from an explicit trusted dialog."""
        selected = validate_resources(value)
        merged = {canonical_path(e["path"]): e for e in self.inputs}
        merged.update({canonical_path(e["path"]): e for e in selected})
        resources = validate_resources(list(merged.values()))
        size = sum(len(e["text"].encode("utf-8")) for e in resources)
        if size + self.virtual_bytes > MAX_RESOURCE_BYTES:
            raise ValueError("Selected inputs and virtual buffers exceed the 64 MiB limit")
        self.inputs = resources
        self.input_map = {canonical_path(e["path"]): e for e in resources}
        self.input_bytes = size

    def text(self, path: str) -> str:
        key = self.find_path(path)
        return self.virtual[key] if key in self.virtual else self.input_map[key]["text"]

    def write(self, key: str, text: str) -> None:
        if key in self.input_map or any(key == canonical_path(e["name"]) for e in self.inputs):
            raise PermissionError("Prepared input text is read-only; choose a distinct virtual output name")
        size = len(text.encode("utf-8"))
        before = len(self.virtual.get(key, "").encode("utf-8"))
        if self.input_bytes + self.virtual_bytes - before + size > MAX_RESOURCE_BYTES:
            raise ValueError("Prepared inputs and virtual buffers exceed the 64 MiB limit")
        self.virtual[key] = text
        self.virtual_bytes += size - before

    def indexed_file(self, symbolic: str, index: Any, extension: str) -> str:
        if symbolic.casefold() not in self.paths:
            raise ValueError("NewPath must register a prepared-input collection first")
        extension = str(extension).casefold()
        allowed = self.path_collections.get(symbolic.casefold())
        entries = [e["name"] for e in self.inputs if (allowed is None or canonical_path(e["path"]) in allowed)
                   and (extension in {"", "????", "*", ".*"} or e["name"].casefold().endswith(extension))]
        if int(index) != index:
            raise ValueError("IndexedFile index must be an integer")
        if index == -1:
            return ";".join(entries) + (";" if entries else "")
        return entries[int(index)] if 0 <= index < len(entries) else ""

    def handle(self, number: Any) -> VirtualHandle:
        if not isinstance(number, (int, float, np.number)) or int(number) != number or int(number) not in self.handles:
            raise ValueError("Unknown/closed virtual file handle")
        return self.handles[int(number)]

    @staticmethod
    def ascii_binary_text(text: str, operation: str) -> str:
        # The GUI sidecar intentionally contains decoded text, not original
        # encoding/raw bytes. A non-ASCII byte count/substring would be an
        # untrustworthy approximation: reject it instead of silently changing
        # scientific input offsets. General-text LoadWave remains Unicode-safe.
        if not text.isascii():
            raise ValueError(operation + " byte-oriented compatibility requires ASCII text; use LoadWave/G directly for decoded Unicode, or explicitly prepare an ASCII copy. No host file was changed.")
        return text

    def execute(self, scope: Any, source: str) -> None:
        validate_virtual_command(source)
        name, flag_list, rest = operation_head(source)
        flags = {f.name: f.value for f in flag_list}
        if name in {"prompt", "doprompt", "doalert"}:
            from materials_data_studio.tools.igor_interaction_operations import interact
            interact(scope, name, flags, rest)
            return
        if name == "abort":
            text = str(scope.evaluate(rest))
            if scope.owner._interaction is not None:
                scope.owner._interaction("message", {"title": "Igor macro stopped", "text": text, "buttons": ["OK"]})
            raise RuntimeError("Igor Abort: " + text)
        if name == "newpath":
            if not re.fullmatch(r"[A-Za-z]\w*", rest):
                raise ValueError("NewPath requires one symbolic name")
            if rest.casefold() in self.paths and "O" not in flags:
                raise ValueError("Symbolic input path already exists")
            if scope.owner._interaction is not None:
                title = str(scope.evaluate(flags["M"])) if flags.get("M") is not None else "Select a folder"
                reply = scope.owner._interaction("directory", {"title": title})
                if reply["status"] == "cancelled":
                    scope.set_global("V_flag", -1)
                    return
                self.accept_resources(reply["resources"])
                selected = canonical_path(reply["selected"]).rstrip("/")
                self.path_collections[rest.casefold()] = {canonical_path(e["path"]) for e in reply["resources"]
                    if canonical_path(e["path"]).rsplit("/", 1)[0] == selected}
                scope.set_global("S_path", reply["selected"])
            elif not self.inputs:
                raise PermissionError("Run in Graph Editor for a file dialog, or explicitly prepare input files")
            self.paths.add(rest.casefold())
            scope.set_global("V_flag", 0)
            return
        if name == "open":
            handle_name, separator, path_expr = re.split(r"\s+(as)\s+", rest, maxsplit=1, flags=re.I) if re.search(r"\s+as\s+", rest, re.I) else (rest, "", "")
            if "D" in flags:
                if scope.owner._interaction is not None:
                    title = str(scope.evaluate(flags["M"])) if flags.get("M") is not None else "Select an input file"
                    file_filter = str(scope.evaluate(flags["F"])) if flags.get("F") is not None else ""
                    reply = scope.owner._interaction("file_open", {"title": title, "filter": file_filter})
                    if reply["status"] == "cancelled":
                        scope.set_global("S_fileName", "")
                        return
                    self.accept_resources(reply["resources"])
                    selected_path = reply["selected"]
                    self.find_path(selected_path)  # Never accept an unprepared worker path.
                else:
                    if self.dialog_index >= len(self.inputs):
                        raise PermissionError("Run in Graph Editor for a file dialog, or prepare another input file")
                    selected_path = self.inputs[self.dialog_index]["path"]
                    self.dialog_index += 1
                scope.set_global("S_fileName", selected_path)
                # Native /D leaves the supplied reference number unchanged.
                return
            if not separator and "R" not in flags:
                raise ValueError("A virtual Open requires 'as stringExpression'")
            path = scope.evaluate(path_expr) if separator else ""
            if not isinstance(path, str):
                raise ValueError("Virtual file path must be text")
            readable = "R" in flags
            if readable and not path:
                if scope.owner._interaction is None:
                    raise PermissionError("Open/R without a filename needs a live Graph Editor file dialog")
                title = str(scope.evaluate(flags["M"])) if flags.get("M") is not None else "Select an input file"
                file_filter = str(scope.evaluate(flags["F"])) if flags.get("F") is not None else ""
                reply = scope.owner._interaction("file_open", {"title": title, "filter": file_filter})
                if reply["status"] == "cancelled":
                    scope.set_global("S_fileName", "")
                    return
                self.accept_resources(reply["resources"])
                path = reply["selected"]
            key = self.find_path(path) if readable else canonical_path(path)
            if not readable:
                previous = self.virtual.get(key, "") if "A" in flags else ""
                self.write(key, previous)
            number = self.next_handle
            self.next_handle += 1
            self.handles[number] = VirtualHandle(key, 0 if readable or "A" not in flags else len(self.virtual[key]), readable, not readable)
            scope.assign(handle_name, str(number))
            scope.set_global("S_fileName", path)
            return
        if name == "close":
            if "A" in flags:
                self.handles.clear()
            elif rest:
                number = scope.evaluate(rest)
                self.handle(number)
                del self.handles[int(number)]
            else:
                raise ValueError("Close requires a handle or /A")
            return
        if name == "fstatus":
            handle = self.handle(scope.evaluate(rest))
            text = self.ascii_binary_text(self.text(handle.path), "FStatus")
            scope.set_global("V_logEOF", len(text))
            scope.set_global("V_filePos", handle.position)
            return
        if name in {"fbinread", "fbinwrite", "fprintf"}:
            args = split(rest)
            if len(args) < 2:
                raise ValueError(name + " requires a handle and text")
            handle = self.handle(scope.evaluate(args[0]))
            if name == "fbinread":
                if not handle.readable or len(args) != 2 or not re.fullmatch(r"[A-Za-z]\w*", args[1]):
                    raise ValueError("FBinRead supports a readable handle and a local String")
                template = scope.evaluate(args[1])
                if not isinstance(template, str):
                    raise ValueError("FBinRead destination must be a preallocated String")
                text = self.ascii_binary_text(self.text(handle.path), "FBinRead")
                value = text[handle.position:handle.position + len(template)]
                handle.position += len(value)
                scope.assign_value(args[1], value)
                return
            if not handle.writable:
                raise PermissionError("Cannot write a prepared read-only input")
            value = scope.evaluate(args[1])
            if not isinstance(value, str):
                raise ValueError("Virtual binary/formatted writes support text only")
            if name == "fprintf":
                parameters = tuple(scope.evaluate(expr) for expr in args[2:])
                value = value % parameters
            elif len(args) != 2:
                raise ValueError("FBinWrite supports one String expression")
            self.ascii_binary_text(value, "FPrintf" if name == "fprintf" else "FBinWrite")
            previous = self.virtual[handle.path]
            self.write(handle.path, previous[:handle.position] + value + previous[handle.position + len(value):])
            handle.position += len(value)
            return
        if name == "deletefile":
            path = scope.evaluate(rest)
            key = canonical_path(path)
            if key in self.input_map:
                raise PermissionError("DeleteFile cannot delete an input or a host file")
            if key not in self.virtual:
                raise PermissionError("DeleteFile accepts only an existing run-local virtual buffer")
            self.virtual_bytes -= len(self.virtual[key].encode("utf-8"))
            del self.virtual[key]
            print("Deleted run-local virtual buffer (no host file changed).")
            return
        if name == "loadwave":
            self.load_wave(scope, flags, rest)
            return
        if name == "tilewindows":
            if flags.get("O") not in {None, "1"}:
                raise ValueError("Only TileWindows/O=1/C is supported")
            print("Presentation: graphs are retained in the MDS graph browser; Igor window tiling is not reproduced.")
            return
        self.panel_command(scope, name, flags, rest)

    def load_wave(self, scope: Any, flags: dict[str, str | None], rest: str) -> None:
        if "G" not in flags:
            raise ValueError("Only LoadWave/G general numeric text is supported")
        path = scope.evaluate(rest)
        if not isinstance(path, str):
            raise ValueError("LoadWave filename must be a string expression")
        if "P" in flags and str(flags["P"]).casefold() not in self.paths:
            raise ValueError("Unknown prepared symbolic path")
        tweaks = [0, 0, 0, 0, 0]
        if "L" in flags:
            items = split(str(flags["L"]).strip("{} "))
            if len(items) != 5:
                raise ValueError("LoadWave/L needs five integer tweaks")
            raw = [scope.evaluate(item) for item in items]
            if any(not np.isfinite(v) or v < 0 or int(v) != v for v in raw):
                raise ValueError("LoadWave/L tweaks must be nonnegative integers")
            tweaks = list(map(int, raw))
        blocks = parse_general_text(self.text(path), tweaks)
        if sum(block.size for block in blocks) > 10_000_000:
            raise ValueError("LoadWave is limited to 10 million selected elements")
        prefix = str(flags.get("N") or flags.get("A") or "wave")
        if not re.fullmatch(r"[A-Za-z]\w*", prefix):
            raise ValueError("LoadWave base name must be a literal Igor identifier")
        overwrite = "N" in flags or "O" in flags
        names, created = [], []
        index = 0
        for block in blocks:
            for column in range(block.shape[1]):
                name = prefix + str(index)
                if not overwrite:
                    while scope.wave_exists(name):
                        index += 1
                        name = prefix + str(index)
                values = block[:, column].astype(np.float64 if "D" in flags else np.float32)
                created.append((name, values))
                names.append(name)
                index += 1
        # One write validates all dependencies; a failed load cannot leave only
        # some imported columns even if the caller catches the exception.
        from materials_data_studio.tools.graph_waves import make_wave
        scope.services.write([make_wave(scope.services.path(n), values) for n, values in created], overwrite=overwrite)
        scope.set_global("S_waveNames", ";".join(names) + ";")
        scope.set_global("S_fileName", path)
        scope.set_global("V_flag", len(names))
        counts = ", ".join(str(len(block)) for block in blocks)
        print(f"LoadWave imported {len(names)} numeric columns from {len(blocks)} block(s) ({counts} rows) of prepared/virtual text.")

    def panel_command(self, scope: Any, name: str, flags: dict[str, str | None], rest: str) -> None:
        def vector(text: str, count: int) -> tuple[float, ...]:
            values = scope.evaluate("(" + text.strip("{} ") + ")")
            if not isinstance(values, tuple) or len(values) != count or any(
                    isinstance(v, (str, bool)) or np.ndim(v) or not np.isfinite(v) or abs(v) > 100_000 for v in values):
                raise ValueError("Panel geometry/colors require bounded finite numeric vectors")
            return tuple(float(v) for v in values)

        panels = scope.services.environment.setdefault("igor_parameter_panels", [])
        if name == "newpanel":
            panel_name = flags.get("N")
            if not panel_name or not re.fullmatch(r"[A-Za-z]\w*", panel_name):
                raise ValueError("NewPanel requires a literal /N=name")
            if any(p["name"] == panel_name for p in panels):
                raise ValueError("Converted parameter panel already exists")
            title = scope.evaluate(rest[3:].strip()) if rest.lower().startswith("as ") else panel_name
            new_panel: dict[str, Any] = {"name": panel_name, "title": str(title), "fields": [], "buttons": [], "groups": []}
            if flags.get("W"):
                rect = vector(flags["W"] or "", 4)
                if rect[2] <= rect[0] or rect[3] <= rect[1]:
                    raise ValueError("Panel rectangle requires positive width/height")
                new_panel["rect"] = rect
            panels.append(new_panel)
            scope.owner.active_panel = panel_name
            return
        panel = next((p for p in panels if p["name"] == scope.owner.active_panel), None)
        if panel is None:
            raise ValueError("Create a converted parameter panel before adding controls")
        pieces = split(rest)
        identifier, _, first_option = pieces[0].partition(" ")
        options = ([first_option] if first_option else []) + pieces[1:]
        fields = {}
        for item in options:
            key, separator, value = item.partition("=")
            if not separator:
                raise ValueError("Invalid converted panel control option")
            fields[key.strip().lower()] = value.strip()
        allowed = {"pos", "size", "title", "fcolor", "bcolor", "valuecolor"} | ({"value", "limits"} if name == "setvariable" else {"proc"} if name == "button" else set())
        if set(fields) - allowed:
            raise ValueError("Unsupported converted panel control option")
        title = str(scope.evaluate(fields.get("title", '\"\"')))
        control: dict[str, Any] = {"name": identifier, "title": title}
        for key, count in (("pos", 2), ("size", 2), ("fcolor", 3), ("bcolor", 3), ("valuecolor", 3)):
            if key in fields:
                control[key] = vector(fields[key], count)
        if "size" in control and min(control["size"]) <= 0:
            raise ValueError("Panel control sizes must be positive")
        for key in ("fcolor", "bcolor", "valuecolor"):
            if key in control and any(v < 0 or v > 65535 for v in control[key]):
                raise ValueError("Panel colors use RGB values from 0 to 65535")
        if name == "groupbox":
            panel["groups"].append(control)
        elif name == "setvariable":
            path = fields.get("value", "")
            value = scope.services.resolve(path) if path else None
            if not isinstance(value, (str, int, float, np.number)) or np.ndim(value):
                raise ValueError("SetVariable must bind an existing numeric/text global")
            control.update(path=scope.services.path(path), value_type="string" if isinstance(value, str) else "number")
            if "limits" in fields:
                limits = scope.evaluate("(" + fields["limits"].strip("{} ") + ")")
                if not isinstance(limits, tuple) or len(limits) != 3 or any(isinstance(v, (str, bool)) or np.ndim(v) or np.isnan(v) for v in limits):
                    raise ValueError("SetVariable limits require {minimum,maximum,step}")
                if limits[0] > limits[1] or not np.isfinite(limits[2]) or limits[2] < 0:
                    raise ValueError("Invalid SetVariable limits/step")
                control["limits"] = [float(limits[0]) if np.isfinite(limits[0]) else None,
                                     float(limits[1]) if np.isfinite(limits[1]) else None, float(limits[2])]
            panel["fields"].append(control)
        elif name == "button":
            callback = fields.get("proc", "")
            function = scope.functions.get(callback.lower())
            if function is None or not hasattr(function, "__name__"):
                raise ValueError("Button proc must name a supported converted routine")
            control.update(callback=callback, python_callable=function.__name__)
            panel["buttons"].append(control)
        else:
            raise ValueError("Unsupported converted panel command")
