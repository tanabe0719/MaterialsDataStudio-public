# PXP editor Command / Procedure compatibility (0.10.6)

This is an interactive **subset**, not an Igor interpreter. `Help` in Command
Window lists the accepted signatures and flags. Unrecognized commands/flags/options
fail atomically; no imported history, macro, procedure, or arbitrary command
string is executed to reconstruct a document. Command expressions use a bounded
AST interpreter, never Python eval/exec. GUI, command and Python changes use the
canonical graph document and pure wave/graph services. Logs are not state.

## Implemented command families

| Family | Commands | Scope / partial compatibility |
| --- | --- | --- |
| Wave | Make, Duplicate, Rename, KillWaves, Redimension, SetScale, InsertPoints, DeletePoints, Concatenate, Sort, Extract | Positive 1D/2D real, complex or text working waves; Make defaults float32, /D float64, /T text. Referenced deletion rejected. Resize linked waves together. Concatenate: matching 1D dtype, scaling and units. Empty results rejected. Other Igor type flags/reference waves unsupported. |
| Expressions | Assignment, +=, -=, *=, /=, Variable, String, Print | Same-shape arrays/scalar broadcast; p/x on first dimension; arithmetic/comparison and named NumPy math functions; no Python objects, arbitrary calls, loops, IPL or dynamic evaluation. |
| Folder | NewDataFolder, SetDataFolder, KillDataFolder | Exact absolute/current/root paths, no guessing duplicate leaf names. Delete only empty non-current folders, never root. |
| Trace / axes | Display, AppendToGraph, RemoveFromGraph, ModifyGraph, SetAxis, Label, ReorderTraces | Existing native Igor style whitelist, including marker/fill/stroke/axis styles and auto bounds. /L,/R custom Y axes supported; full Igor flags/X-axis variants unsupported. ModifyGraph accepts native literal forms and bounded numeric scalar expressions, not arbitrary code. |
| Free axes | NewFreeAxis, ModifyFreeAxis, KillFreeAxis | Side and canonical freePos. Unused axes are retained semantically but drawn when referenced by a trace. Standard/referenced axes cannot be killed. |
| Errors | ErrorBars | Symmetric/asymmetric Y or symmetric X via wave=(low,high). One error direction per trace (a second assignment replaces it). No simultaneous XY, constant/percent errors or all Igor error styling flags. |
| Text | Legend, TextBox, AppendText, Tag | Existing Igor rich text; /N /F /A /X /Y basics; point-linked Tags without Tag arrows. No full Igor notebook/annotation language. |
| Image | AppendImage, RemoveImage, ModifyImage, ColorScale, ReorderImages | Existing native image colormap/range/log/hide/interpolation whitelist. ColorScale image selection, visibility, appended label. Full colorbar placement flags unsupported. |
| Draw | SetDrawEnv, SetDrawLayer, DrawLine, DrawRect, DrawOval, DrawText | Basic semantic drawing coordinates/style. Existing GUI supports further shape tools; no command DrawPoly/Bezier/IPL runtime. |
| Table / window | Edit, AppendToTable, RemoveFromTable, DoWindow/F, KillWindow, RenameWindow | Canonical graphs/tables, basic column references; window selection is editor selection, not OS activation. No notebooks/layouts/browser windows. |
| Analysis | WaveStats, Differentiate, Integrate, Smooth, Histogram, FFT, FindPeak, FindLevel, CurveFit | See numerical qualifications below; not full Igor analysis flag compatibility. |
| Editor | Undo, Redo, Help | Local Undo/Redo alone per input, not mixed with mutations. Print/help/stats queries do not dirty documents. |

Flags not listed by `Help commandName` are unsupported. Mutating commands execute
as a single atomic input, including semicolon batches. A late failure restores
all working state and active context. GUI edits display model/layout-aware
ModifyGraph, SetAxis, Label, annotation/image/color-scale and drawing operations.
Moved/edited/deleted drawings replace affected layers with DrawAction plus known
draw commands; appends log just new drawings. GUI Undo/Redo is displayed once.
The log is descriptive, never autoexecuted or used as surrogate state. Its native
Igor syntax is broader than this command interpreter's subset. MDS-only placement/
style resets and abbreviated large content are explicitly marked; values of waves
are not serialized into logs. Store scientific state in MDSG/Export Data.
Command history/output can be copied/cleared; new output is not saved. PXP history
is retained separately as read-only source provenance in MDSG.

Graph Editor Axes Properties expose `axRGB`, `alblRGB` (axis-label color),
`tickRGB` and `tlblRGB`. Independent major/minor colors are MDS-specific overrides;
a later native tickRGB resets both overrides. `tickEnab(axis)={low,high}` filters
major/minor ticks and labels by inclusive numeric coordinates, not an axis fraction
or axis limits. Default `{-Inf,Inf}` shows all; userTicks, log/reversed and named
axes are supported. Native zero/gridHair/lowTrip/highTrip/notation/ZisZ and positive
gfSize graph-default text sizing are supported. Nonzero standoff and gfSize 0/-1
remain explicitly unsupported; no misleading approximation is silently applied.

## Numerical qualifications

- Analysis accepts real numeric 1D waves; WaveStats reports finite samples (sample
  standard deviation, ddof=1). It does not create Igor V_* global variables.
- Differentiate uses NumPy gradient on scaled X, Integrate uses cumulative
  trapezoids with initial zero. No advanced Igor differentiation flags.
- Smooth writes a derived wave: boxcar/nearest endpoints, or /S polynomial-order
  Savitzky-Golay. It does not overwrite the source.
- Histogram writes counts and an explicit `_edges` wave; no implicit bin-center
  axis scaling. FFT is unnormalized full complex DFT with `_frequency` coordinates.
- FindPeak reports the global sampled maximum. FindLevel reports the first
  crossing's left sample (not interpolated). No multi-peak refinement.
- CurveFit reuses native Quick Fit models, generated calculated/X waves and a
  textual summary. No Igor procedure functions, all weighting/constraint flags,
  coefficient globals, covariance/notebook conventions or automatic graph append.
- Derived physical units are not generally inferred: verify and set them.
- Per command-created wave limit: 10 million elements / 256 MiB. Immutable arrays
  are shared by Undo snapshots; only changed wave arrays are copied.

## Python API and execution

`from mds import wave, graph, folder, document, selection, transaction` is available
only inside explicit Python runs (the implementation is procedure_api.py).
Procedure Window's **Python / mds Help…** and View's matching action open modeless,
searchable help. Tabs document startup, the actual module allowlist, all
public mds handles/calls/properties/arguments/results, tested scientific examples,
environment limits, conversion and troubleshooting. Enter in search never closes the help.

`Selection to Python` serializes selected core typed commands (Make, assignments,
Display/AppendToGraph, Label, SetAxis, Print) into these public API calls. It never
invokes a command parser from Python to mutate state or runs the conversion.
Other Selection-to-Python conversions are explicitly unsupported; review converted code.
This older selection converter remains API-based. The separate Igor/IPF converter
below uses a new, fixed-allowlist canonical command bridge.

- `wave(name)` resolves a canonical handle; `.values` is a read-only view, replace
  via assignment. `.name`, `.units`, `.shape`, `.scaling` expose working metadata.
  `wave.create(name,values,x=None,overwrite=False,units="")`, `.names()`, `.delete()`.
  `x=` requires matching uniformly spaced coordinates; nonuniform X is a separate wave.
- `graph.new(name)`, `graph.active()`, `graph(name)`; `.append(y,x=None,axis="left")`
  returns a trace handle. `.axis(name).set_range(low,high)`, `.axis(name).label`;
  `.trace(name).line_width` and `.color` (name/hex or RGB fractions).
- `folder.current`, `.create(path)`, `.set(path)`; `document` is the working
  EditableGraphDocument, `selection` the run's active context dictionary.
- `with transaction(title):` rolls back nested document changes on exceptions.
  All successful changes from one Run commit as **one** editor-local Undo entry.

Each Run starts a fresh Windows AppContainer process and receives canonical state,
not GUI artists or command history. Run Selection preserves traceback line offsets.
Stop kills that worker and discards document results. Exceptions or concurrent GUI
edits also discard results. No automatic execution or automatic IPL conversion.
Imported Igor source is read-only. Unrestricted Python execution is unavailable.
Python uses scientific module facades and restricted builtins/AST plus an audit
guard. Only numpy, selected scipy modules, math, statistics and mds are available.
Network capabilities are absent; native file/network denial preflight is required.
No unrestricted fallback is permitted. Child processes and clipboard/UI operations
are blocked by a Job; memory/CPU/wall/output are bounded. The first run copies a
private runtime. Standard AppContainer may access Windows-shared system resources;
this is not a VM, an absolute security guarantee, or LPAC. No shared desktop ACL
is changed. File/import/introspection APIs outside the allowlist are unavailable.

MDSG v4 persists scalar/string environments, folders and editable Python source;
v1-v3 remain readable. Viewer Send to Igor is removed; H5XP export excludes user procedures and
does not overwrite existing H5XP files. Original PXP bytes are never modified.

## Explicit Igor procedure / IPF conversion

Procedure's **Convert Igor / IPF…** opens source copies, never executes them.
Drop one/multiple IPF files into Graph Editor to open this review dialog.
Open one selected IPF (UTF-8/BOM, UTF-16 or CP932/legacy decoding), or choose an
imported PXP procedure. Read is limited to 4 MiB; analysis to 1 MiB UTF-8,
10000 statements, 256 routines, 32 parameters and bounded nesting. Includes are
not loaded. Analyze explicitly, choose an entry and finite scalar/text JSON
arguments, review line diagnostics/generated Python, then insert. Replacing
working Python requires confirmation; original source tabs are untouched.
Only a later explicit **Run All** executes the generated program.

The compiler handles Variable/G, NVAR, local Variable/String/Wave declarations/aliases, scalar and
wave assignments, for/if/elseif/else, break/return, supported routine calls and
selected functions: numpnts, WaveMin/Max, interp, round, floor/log and the base
expression functions; ItemsInList, StringFromList, ReplaceString, strlen,
str2num/num2str/numtype, WaveExists and GetDataFolder. `interp` requires strictly
monotonic finite 1D X and uses linear extrapolation outside its bounds; this differs
from GUI Wave Calculation's NaN-outside-range policy. Verify shapes and units.
Converted local variables are case-insensitive, per-call and not persisted as
document globals. Dynamic `$name`/`$(expression)` resolves a quoted object name,
never executable text. A shared 1000000-step budget and existing OS run limits apply.

Converted `LoadWave/G` uses native consecutive comma/tab/space delimiter collapse,
explicit NaN values and successive numeric blocks, with `/L` search/row/column
selection. GUI text import keeps its existing column-preserving behavior. A
narrow split-PRF reflection pattern emits a conversion diagnostic and validates
header/columns before an explicit extra-index reconciliation; generic `/G` does
not drop or reorder columns. Unknown/ambiguous layouts fail. Reconvert original
source to enable this compatibility adapter.
Missing local `Wave` aliases remain null (warning unless `/Z`); `WaveExists` is 0
and actual access fails. Creating a later target does not revive an alias until
explicit rebinding. No fake waves are created. Expression caches store only syntax,
not results; wave edits stay live. Existing OS/security/Stop limits remain.

`from mds import command, igor` exposes this limited bridge only inside explicit
isolated runs. `command(source)` accepts an explicit fixed allowlist of canonical
wave/folder/graph/table/drawing/analysis commands, not UI operations, Undo/Redo,
I/O, includes, Execute, OS/XOP calls or future commands added to Command Window.
Graph settings are also explicitly allowlisted. Indexed targets such as
`ModifyGraph rgb[0]=(0,0,0)` select a trace by its zero-based order. `/Z` suppresses
only missing targets on ModifyGraph/Label/SetAxis/KillWaves, not syntax, dependency
or unsupported-setting errors. `igor.scope` is a converter adapter, not a full
Igor runtime; prefer high-level public handles for handwritten Python.

Graph Editor runs mediate `Open/D` file choices and `NewPath` folder choices through
modeless, trusted GUI dialogs. Original message/filter strings are retained; no
worker-supplied path is opened automatically. A folder's immediate file list needs
explicit confirmation; subfolders are not read. `Input files…` also prepares a bounded text collection: 64 MiB decoded UTF-8,
1000 files, unique basenames. It is copied into a fixed per-run input sidecar, never
MDSG metadata or OS permissions. NewPath/IndexedFile see only the selected symbolic collection;
limited Open/FStatus/FBinRead/FBinWrite/fprintf/Close/LoadWave/DeleteFile use immutable
selected inputs or ephemeral memory buffers. Output is not saved to host files.
The run reports residual buffers; original files are never changed. Header/column
selection is limited and diagnosed, not a general Igor binary/text loader.
Byte-offset operations (FStatus/FBinRead/FBinWrite/fprintf) require ASCII bodies,
since decoded snapshots do not retain source byte encodings. Direct LoadWave/G
can read Unicode. Unicode filenames are supported; no incorrect byte offsets
are silently substituted with character positions.
NewPanel/GroupBox/SetVariable/Button retain source-relative positions, sizes,
titles, button colors, numeric steps and text globals. Panels automatically appear
after successful initialization. Apply changes globals as one Undo; source-titled
buttons apply values and explicitly run their callback with eventCode=2. Arbitrary
Igor controls/events and native OS chrome remain outside the subset.
Prompt/DoPrompt support numbers, strings and one-based/string popup selections;
Cancel preserves input and returns V_flag=1. DoAlert maps OK/Yes/No/Cancel to its
native V_flag choices. Open/D preserves refNum and returns empty S_fileName on Cancel.

Interaction uses strict bounded JSONL streams opened at trusted bootstrap, not
user-file or network APIs. Sequence/schema/size checks, Stop and stale-result
discard remain active. Each UI wait is capped at five minutes, with one hour
absolute and existing CPU/memory/output limits. No user module allowlist expansion.

Unsupported instructions block their routine and callers; independent supported
routines remain available. Recursion and general STRUCT/reference declarations
remain rejected. Display scheduling hints retain deferred rendering semantics
without repetitive INFO entries. New conversions honor source rtGlobals: 3 enables
strict bounds/diagnostics; 1/2 (Igor default 1) clips wave indices to endpoints.
Old generated code/handwritten igor.scope() stays strict unless reconverted.
No original source or loop expression is rewritten. Static compilation is not runtime validation.
Static conversion cannot establish runtime names, dimensions or scientific validity;
existing analysis limitations above still apply. No user-import module, direct host-file or
network capability has been added. Never treat a converted program as trusted.


## Analysis / Statistics workflows

Graph Editor now separates **Data**, **Analysis**, and **Statistics**.
`MDSStatistics` and `MDSProcess` are MDS extensions with labelled outputs; they do
not use native Igor result-wave layouts. The dialogs generate replayable commands
and record input roles, method and output. `/METH` is a zero-based choice from the
corresponding dialog; the order is listed in the Graph Editor manual.

```igor
MDSStatistics/KIND=ftest/ALPH=.05/TAIL=4/DEST=variance_test/T group_a,group_b
MDSStatistics/KIND=anova/METH=1/DEST=welch_test/T group_a,group_b
MDSStatistics/KIND=resample/N=2000/SEED=19/DEST=bootstrap group_a
MDSProcess/KIND=convolve/METH=0/DEST=response kernel,signal
MDSProcess/KIND=interpolate/METH=2/N=301/DEST=interpolated y,x
MDSProcess/KIND=stft/N=32/DEST=spectrogram signal
MDSProcess/KIND=poly2d/METH=1/DEST=fitted_image image
```

`/KIND` and available methods are described under Analysis and Statistics in the
dedicated guide. New outputs cannot overwrite input waves. Preview leaves the
original document unchanged. Native-subset Smooth/FFT/Histogram/Resample commands
remain distinct from these MDS extension workflows.
