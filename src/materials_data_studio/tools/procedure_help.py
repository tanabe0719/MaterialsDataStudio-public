"""Modeless, searchable help for the actual isolated scientific Python API."""
from __future__ import annotations

from PySide6.QtGui import QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.tools.procedure_policy import NUMPY, SCIPY

START = r"""
# Python Procedure: start here

This is MDS scientific Python, not the Igor programming language and not a normal
unrestricted Python console. Imported Igor procedures are read-only and never run
automatically. Use Convert Igor / IPF to review a supported translated copy.

1. Open **View > Procedure Window**. Select **Python · procedure.py**.
2. Write Python or use **Open .py…**. Loading code never runs it.
3. Press **Run All**, or select complete statements and press **Run Selection**.
   A selection is a separate program: include its imports and required variables.
4. Read print output and exceptions in **Command Window**. Traceback line numbers
   retain the original selection offset. **Stop** cancels the entire run.
5. Successful changes appear together as **one local Undo step**. Errors, Stop, or
   concurrent GUI changes discard the worker result. Do not edit while awaiting
   a result you want to keep. This editor's Undo/Redo is separate from the main app.

Each run uses a fresh Python process. Local Python variables/imports do not survive
between runs. Waves, graphs, folders and the Python source can survive in .mdsg.
Save .py stores source only; export data/figures through the editor, not Python I/O.
The source is not autosaved to .mdsg until you explicitly save the document.

Use **Separate window** to move Command/Procedure independently and **Dock back**
to restore the last dock area. Closing a dock hides it; reopen it using View.
This Help is modeless: you can keep it open while editing. Ctrl+F focuses search.

```python
import numpy as np
from mds import wave, graph, transaction

with transaction("Sine demonstration"):
    time = wave.create("demo_time", np.linspace(0, 10, 201), units="s")
    signal = wave.create("demo_signal", np.sin(time.values), units="a.u.")
    g = graph.new("Demo")
    trace = g.append(signal, time)
    trace.color = "#2361ab"
    trace.line_width = 1.5
    g.axis("bottom").label = "Time (s)"
    g.axis("left").label = "Signal (a.u.)"
print(wave.names())
```

Run once, or change the names / explicitly use overwrite=True for existing waves.
graph.new automatically selects a unique name if the requested name already exists.
"""

REFERENCE = r"""
# mds: document API reference

```python
from mds import wave, graph, folder, document, selection, transaction, command, igor
```

The mds module exists only inside an explicit Procedure run. It operates on a
canonical working document, never live Qt widgets or Matplotlib artists. The
explicit command bridge accepts only its documented canonical subset, not arbitrary
Igor strings or UI/file operations. Paths are names, not filesystem locations.

## Names and current folder

Use full paths such as `root:scan:signal` to avoid ambiguity. Relative wave names
resolve in folder.current, then root; an arbitrary matching leaf is not guessed.
Folder paths omit the trailing colon. Parent folders must exist before creation.
Wave names can contain spaces and units: `wave("root:Moment (emu)")`.

## wave API

| Call | Meaning |
| --- | --- |
| wave(name) | Resolve an existing wave and return a WaveHandle; unknown names raise ValueError. |
| wave.names() | Full root: paths of all working waves, including imported waves. |
| wave.create(name, values, x=None, overwrite=False, units="") | Create a non-empty 1D–4D numeric or text wave and return a handle. Duplicate names require explicit overwrite=True. Object arrays are rejected. |
| wave.delete(name) | Delete an unreferenced wave. A graph/table/user-tick reference prevents deletion; remove the dependency first. |

`x=` must be finite, uniformly spaced, contain at least two points, and match the
first dimension. It sets (start, delta), not an explicit X wave. For irregular X,
create a separate X wave and pass it to graph.append. Wave creation is limited to
10 million elements / 256 MiB; all other process limits still apply.

## WaveHandle

| Property | Read / write |
| --- | --- |
| values | Read-only NumPy view. Replace using handle.values = new_values; do not assign in-place to the view. Linked X/Y/error lengths must still match. |
| assign(values) | Broadcast into the existing shape/type with the same finite integer range checks as Command assignment; values= may instead replace shape/type. |
| name | Leaf name. Assigning renames the wave and updates references; a full root:path moves/renames it. |
| units | Data-unit string, readable/writable. Physical units are not inferred by arithmetic. |
| shape | Read-only tuple of dimension sizes. |
| scaling | Tuple of (start, delta) per dimension, readable/writable. Each pair must be finite with nonzero delta and match the dimension count. |
| path | Full root:path identifying this handle. Prefer this over leaf names in dependent operations. |
| coordinates | Read-only dictionary of sparse p/q/r/s indices and x/y/z/t scaled coordinate arrays; no full multidimensional mesh allocation. |

```python
from mds import wave
signal = wave("root:demo_signal")
signal.values = signal.values * 2
signal.units = "a.u."
signal.scaling = ((0.0, 0.05),)
print(signal.name, signal.path, signal.shape, signal.scaling)
```

## graph API and GraphHandle

| Call | Meaning |
| --- | --- |
| graph.new(name="Graph0") | Create an empty graph, choose a unique name, activate it, return a GraphHandle. |
| graph(name) | Return a handle for an existing named graph. |
| graph.active() | Resolve the current active graph; errors if none exists. |
| g.activate() | Make this graph the current target in the run context. |
| g.append(y, x=None, axis="left", x_axis="bottom", x_side="") | Append Y against its scaling or an explicit X wave. y/x can be names or WaveHandles. X may be bottom/top or a custom axis; x_side is bottom/top for a new custom X. Returns TraceHandle. Missing named axes are created with validated orientation. |
| g.axis(name) | Return an AxisHandle for an existing axis (left/bottom/right/top/custom). |
| g.trace(name) | Return a TraceHandle for an existing trace. Duplicate Y traces have distinct names. |

GraphHandle.name is the graph's identifier. A trace name is not necessarily its
Y-wave name; use the handle returned by append. This API is intentionally smaller
than Igor: no arbitrary GUI calls, image/table/drawing creation or all ModifyGraph
properties through GraphHandle. Use GUI/Command or the bounded command bridge.

## AxisHandle and TraceHandle

| Member | Meaning |
| --- | --- |
| axis.label | Read/write raw Igor-formatted label string (one backslash in actual text). |
| axis.set_range(lower, upper) | Finite bounds; None means automatic for that endpoint. set_range(None, None) restores autoscale. |
| axis.info() | Semantic dictionary of axis identity, range and properties; not native Igor AxisInfo string formatting. |
| trace.info() | Semantic dictionary of trace wave/axis bindings and properties. |
| trace.line_width | Read/write nonnegative finite line width in native points. Read may be None when inherited. |
| trace.color | Read returns native RGB components 0-65535 or None. Write accepts a color name, hex string, or RGB fractions in [0,1]. Read and write numeric scales differ. |

Use a raw Python string for label controls, e.g. `axis.label = r"\f02Temperature\f00 (K)"`.
Axis/trace getters expose canonical settings; an older GUI-only display override
can still affect rendering. When needed, explicitly clear/replace it through GUI
or ModifyGraph. No claim of complete Igor styling compatibility is made.

## folder API

| Call | Meaning |
| --- | --- |
| folder.current | Read-only current data-folder path. |
| folder.create(name) | Create one new folder under an existing parent; returns its full path. Existing folders raise ValueError. |
| folder.set(name) | Select an existing folder for subsequent relative names. |
| folder.rename(old, new_name) | Rename a folder leaf and update dependent references/context; no collisions or cycles. |
| folder.move(old, destination) | Move a folder under an existing destination and update dependent references/context. |

```python
from mds import folder, wave
folder.create("root:analysis")
folder.set("root:analysis")
wave.create("result", [1.0, 2.0, 3.0], units="K")
folder.set("root")
```

## transaction, selection and document

`with transaction("title"):` snapshots working state and run context. An exception
rolls this block back. If you catch the exception outside the block, later code can
continue. An uncaught exception discards the entire run. The title is descriptive;
nested transactions do not create separate GUI Undo steps.

`selection` is the run-context dictionary, with optional graph/table identifiers
and selected_kind. It is not the live GUI selection; graph.new/activate/append
update the active graph context. Prefer graph.active() or explicit graph names.

`document` is the low-level EditableGraphDocument for this run; `document.current`
holds working graphs, layout, waves, tables and environment; `document.source`
is immutable import provenance. Read-only inspection is the intended advanced
use. Low-level mutation is not a stable public editing API: use wave/graph/folder
to retain reference checks. Never change source provenance or internal services.
Private attributes and internal service access are blocked by the Procedure policy.
"""

CONVERSION = r"""
# Convert Igor / IPF: explicit review, then Run

1. Press **Convert Igor / IPF…** in Procedure Window. Choose an imported read-only
   source, paste a copy, or **Open .ipf…**. Loading never runs code or resolves includes.
2. Press **Analyze / Convert**, select an **Entry routine**, and supply a JSON list
   of arguments, e.g. `[2.0, "root:signal"]`. Missing required arguments are not guessed.
3. Review **Diagnostics / limits** and **MDS Python preview**. Unsupported operations
   block their routine and dependent callers; independent supported routines remain
   selectable. Successful conversion is not scientific validation or full Igor support.
4. **Insert into Python (not run)** replaces the working Python source only after
   confirmation if it is nonempty. Original imported Igor tabs are unchanged. Local
   Graph Editor Undo restores the prior working Python source.
5. Review the result and press **Run All** explicitly. The existing AppContainer,
   no-network policy, Stop, time/memory limits and atomic run-result handling apply.

Supported constructs include Function/Proc/Macro, local Variable/String/Wave
declarations and aliases, bounded for/if/elseif/else, break/return, wave arithmetic,
calls to supported routines, selected scalar/list/wave functions and canonical
wave/graph/table/analysis commands. There is a shared 1000000-step runtime budget.
Local variables are not persisted as global document variables. Runtime names,
dimensions, scaling, units and numerical limitations still need verification.

IPF drops open this same review dialog; they never run code. During explicit
Graph Editor runs, Open/D opens a modeless file chooser with the source title/filter.
Cancel empties S_fileName without changing the supplied reference. NewPath asks
for a folder and confirmation of immediate text files; no subfolder scans/includes.
IndexedFile sees only the explicitly selected symbolic-path collection.
**Input files…** also prepares text for noninteractive workflows. The cumulative
limit is 64 MiB decoded UTF-8 / 1000 unique basenames. Inputs are per-run snapshots,
not saved in MDSG. Reads cannot access arbitrary host paths; writes/deletions affect
bounded memory buffers only.
Byte-offset FStatus/FBinRead-style operations require ASCII bodies; direct
LoadWave/G supports Unicode. Original source-byte encodings are not retained.
Variable/G, NVAR, selected string/path helpers and limited virtual text operations
are supported. Converted panels automatically appear after successful initialization,
retaining source positions/sizes, groups, numeric steppers, text fields and button
colors. Source-titled buttons apply values and explicitly run supported callbacks;
Apply alone is undoable. **Macro parameters…** reopens a closed panel. Arbitrary Igor
controls/events and OS chrome are not reproduced exactly.
Prompt/DoPrompt support numeric/text/popup fields, with Cancel preserving values
and setting V_flag. DoAlert supports OK/Yes/No/Cancel. Stop remains available while
dialogs wait (five minutes/dialog, one hour absolute; computation limits remain).
New conversions honor rtGlobals=3 strict wave bounds, or 1/2 native endpoint
clipping (Igor defaults to 1). Original source/loops are never rewritten. Existing
stored conversions and handwritten igor.scope() retain their prior strict default.

Converted LoadWave/G collapses consecutive comma/tab/space delimiters, retains
explicit NaN and reads successive numeric blocks. /L controls search/row/column
selection. The GUI Load General Text importer still preserves empty CSV columns.
A narrowly recognized split-PRF reflection pattern emits a compatibility diagnostic
and validates header/columns before reconciling an extra index. All columns remain
available; unknown or ambiguous layouts fail. Ordinary LoadWave/G never reorders
columns. Reconvert the original IPF to use the explicit adapter.
Absent local Wave references stay null, with a warning unless /Z. WaveExists is 0;
actual numeric/graph access still fails. No wave is invented. Creating a target
later requires explicitly rebinding its null alias. Expression syntax is cached,
not calculated values; the existing CPU/memory/Stop and isolation limits remain.

Unsupported includes, external file/network/OS operations, Execute, XOPs,
recursion, general STRUCT/reference declarations and unimplemented flags
or graph settings are not translated into unrestricted Python. No new user-import
modules or direct host-file/network permissions are enabled. Edit the source copy or rewrite using
the documented mds/scientific APIs when conversion is blocked.

## Explicit canonical command bridge

```python
from mds import command
command('Make/D/O/N=21 demo_signal; demo_signal=sin(p/4)')
command('Display/N=Demo demo_signal; ModifyGraph tick=2,mirror=1')
```

`command(source)` operates only on the worker's working graph document, using a
fixed command/flag/graph-option allowlist. It is not Execute, a shell, a host-file
loader or a way to run arbitrary Igor code. Commands fail on unsupported syntax;
check the Command compatibility document for numerical qualifications. Use
`with transaction("title"):` when catching errors across several bridge calls.

The analysis bridge supports bounded native subsets: even finite 1D FFT with
positive exponent/unscaled amplitude and real half/centered complex spectra;
explicit half-open Histogram bins (`/P` divides by all finite source points and bin width);
line/poly CurveFit with weights, masks, coefficient/sigma/result waves. Unsupported
flags/models/constraints fail. Residual-axis auto-layout and logarithmic fit-X
sampling are not reproduced. Line-only additional covariance is MDS_Covar.
The former MDS-specific algorithms remain FFTDerived / HistogramDerived /
CurveFitDerived / SmoothDerived. These are distinct contracts, not aliases.
Make/Redimension support bounded 1D–4D integer/complex data and sparse indices;
expressions use double precision before storage conversion, not arbitrary-precision
integer arithmetic. AxisInfo/TraceInfo use MDS semantic JSON, not native strings.

`igor.scope(parameters, functions=...)` is the converter's limited runtime adapter:
declare/alias/assign/evaluate/condition/command operate on explicit locals and
canonical waves, with the shared step budget. It is not a persistent Igor runtime.
Prefer the higher-level wave/graph/folder API for handwritten Python.
"""

EXAMPLES = r"""
# Scientific examples

## Derivative against explicit irregular X

```python
import numpy as np
from mds import wave, graph, transaction

with transaction("Derivative"):
    time = wave("root:demo_time")
    signal = wave("root:demo_signal")
    derivative = wave.create("derivative", np.gradient(signal.values, time.values))
    g = graph.new("Derivative")
    g.append(derivative, time)
    g.axis("bottom").label = "Time (s)"
```

## Curve fit with selected SciPy

```python
import numpy as np
from scipy.optimize import curve_fit
from mds import wave, graph

def model(x, slope, intercept):
    return slope * x + intercept

time = wave("root:demo_time")
signal = wave("root:demo_signal")
mask = np.isfinite(time.values) & np.isfinite(signal.values)
coeff, covariance = curve_fit(model, time.values[mask], signal.values[mask])
fitted = wave.create("linear_fit", model(time.values, *coeff))
g = graph.new("LinearFit")
g.append(signal, time)
g.append(fitted, time).color = "#c73737"
print("Coefficients:", coeff)
variances = np.array([covariance[i, i] for i in range(len(coeff))])
print("Standard errors:", np.sqrt(variances))
```

The installed NumPy facade does not expose every NumPy function. The example
extracts the covariance diagonal explicitly. Check shapes/units, fit range,
weighting and model suitability yourself. These examples use generated waves,
not research sample files. Repeated runs need new names or explicit overwrite=True.

## Recovering a failed block

```python
from mds import wave, transaction

try:
    with transaction("Trial"):
        wave.create("trial", [1.0, 2.0])
        raise ValueError("discard trial")
except ValueError:
    print("Trial rolled back")
print(wave.names())
```
"""

LIMITS = """
# Environment, security and troubleshooting

- A fresh Windows AppContainer with **zero network capabilities** runs each
  procedure; no ordinary-process fallback. Native file/network denial is checked
  before user code. Other platforms or unavailable isolation refuse execution.
- The runtime is privately copied. Original application/research folders are not
  granted to the worker. First use takes longer and needs additional disk space;
  later runs reuse the cache. Brief Windows runtime-directory locks are retried.
- Only the Modules tab's scientific facades and mds are exposed. No pip/install,
  arbitrary/relative/wildcard imports, classes/async, private introspection,
  open/eval/exec, file I/O, sockets, subprocesses, ctypes or registry operations.
  pandas, matplotlib/pyplot, pathlib, os, requests, time and h5py are unavailable.
- A Windows Job blocks child processes and clipboard/UI interaction. Limits:
  1 GiB process memory, 120 seconds CPU, 180 seconds worker elapsed time,
  256 MiB output directory and 1 MiB displayed text per stdout/stderr stream.
- This is standard AppContainer plus API/audit restrictions, **not a VM or an
  absolute guarantee against OS/native-library vulnerabilities or access to
  Windows-shared system resources**. No shared desktop ACL is changed.
- Saved documents and imported procedures never execute automatically. Python
  files are source, not trusted just because they have a .py extension.

## Common errors

**Name/Attribute not allowed**: use the documented APIs; names beginning with an
underscore and reserved names (including data, base, services, load/save) are
blocked. Prefer ordinary variable names such as values, signal, coeff, result.

**Module not available / AttributeError**: only facade functions listed in Modules
are provided. Full NumPy/SciPy module APIs, file functions and arbitrary imports
are not exposed. NumPy random is not part of this API.

**Wave already exists**: rename the result or use overwrite=True intentionally.
Overwriting referenced waves must retain compatible dimensions/lengths.

**Unknown graph/wave/folder**: use full root: paths, create parent folders first,
and select/create a graph before graph.active(). Local variables from a previous
run are absent. Run Selection must include its dependencies.

**Stale worker results discarded**: you changed the document while Python ran.
Wait for completion before further edits, or rerun against the updated document.

**Isolation unavailable / limit reached**: execution/results are refused, never
retried without sandboxing. Check Windows support, disk space and the diagnostic
output. Split large calculations into smaller runs or waves; Stop remains available.
"""


def sections() -> dict[str, str]:
    import numpy as np
    modules = "# Allowed scientific modules\n\nOnly the named facade members are exposed; this is not a full module namespace.\n\n"
    modules += "## numpy\n\n`" + "`, `".join(n for n in NUMPY if hasattr(np, n)) + "`\n\n"
    modules += "`numpy.linalg` and `numpy.fft` expose the selected scipy.linalg / scipy.fft functions below.\n\n"
    for name, functions in SCIPY.items():
        modules += f"## scipy.{name}\n\n`" + "`, `".join(functions.split()) + "`\n\n"
    modules += "## math, statistics, mds\n\nmath exposes its public constants/functions; statistics exposes its public functions except NormalDist. mds is described in the mds reference tab. Import explicitly in every run.\n\nBuiltins: abs, all, any, bool, complex, dict, enumerate, filter, float, int, isinstance, iter, len, list, map, max, min, next, pow, print, range, reversed, round, set, slice, sorted, str, sum, tuple, zip, and selected exception classes.\n"
    return {"Start here": START, "Modules": modules, "mds reference": REFERENCE, "Igor / IPF conversion": CONVERSION, "Examples": EXAMPLES, "Environment / errors": LIMITS}


class ProcedureHelpDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Python Procedure / mds Help")
        self.setModal(False)
        self.resize(1020, 760)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.browsers: list[QTextBrowser] = []
        for title, content in sections().items():
            browser = QTextBrowser()
            browser.setOpenExternalLinks(False)
            browser.setObjectName("ProcedureHelp" + title.split()[0])
            browser.document().setDefaultStyleSheet("body { line-height: 1.4; } pre { white-space: pre-wrap; } td, th { padding: 5px; }")
            browser.setMarkdown(content)
            self.tabs.addTab(browser, title)
            self.browsers.append(browser)
        layout.addWidget(self.tabs)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find in current help tab (Ctrl+F)")
        self.search.returnPressed.connect(self.find_next)
        row.addWidget(self.search, 1)
        for title, callback in (("Previous", lambda: self.find_next(True)), ("Next", self.find_next), ("Close", self.hide)):
            button = QPushButton(title)
            button.setAutoDefault(False)
            button.setDefault(False)
            button.clicked.connect(callback)
            row.addWidget(button)
        self.search_status = QLabel()
        self.search_status.setMinimumWidth(80)
        row.addWidget(self.search_status)
        layout.addLayout(row)
        self.shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self.shortcut.activated.connect(self.search.setFocus)

    def find_next(self, backward: bool = False) -> None:
        browser = self.browsers[self.tabs.currentIndex()]
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        term = self.search.text()
        found = bool(term) and browser.find(term, flags)
        if term and not found:
            cursor = browser.textCursor()
            cursor.movePosition(cursor.MoveOperation.End if backward else cursor.MoveOperation.Start)
            browser.setTextCursor(cursor)
            found = browser.find(term, flags)
        self.search_status.setText("" if found or not term else "Not found")
