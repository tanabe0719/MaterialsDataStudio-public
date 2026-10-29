"""Atomic Igor-like interactive operations on canonical document services."""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np

from materials_data_studio.tools.command_language import (
    Argument,
    Assignment,
    Expression,
    Operation,
    Reference,
    Setting,
    evaluate,
    expression,
    parse,
    split,
)
from materials_data_studio.tools.command_services import CommandResult, DocumentServices
from materials_data_studio.tools.graph_waves import delete_wave, make_wave, rename_wave, resize_wave
from materials_data_studio.tools.pxp_model import (
    PxpAnnotation,
    PxpAnnotationStyle,
    PxpAxis,
    PxpDrawing,
    PxpImage,
    PxpTable,
)
from materials_data_studio.tools.pxp_recreation import _modify_graph, _modify_image

COMMAND_HELP = {
    "MDSStatistics": "MDSStatistics /KIND=summary|ftest|chi|variances|anova|anova2|correlation|multiple|normality|circular|contingency|resample|ks|rank|average [/METH=index /ALPH=.05 /TAIL=1|2|4 /DEST=output /N=2000 /SEED=0 /T] waves (MDS labelled reports, not Igor result-wave layouts)",
    "MDSProcess": "MDSProcess /KIND=convolve|correlate|interpolate|filter|ifft|periodogram|lomb|multitaper|dwt|cwt|wigner|stft|poly2d|gauss2d [/METH=index /DEST=output /N=201 /NODC] waves (new output; input roles and methods documented in Analysis)",
    "SetImageCoordinates": "MDS extension: SetImageCoordinates [/W=graph] image,xEdgeWave|*,yEdgeWave|*",
    "NewLayout": "NewLayout [/N=name] creates a 612 x 792 point page",
    "AppendLayoutObject": "AppendLayoutObject [/W=layout /R=(left,top,right,bottom)] graph name",
    "AppendMatrixContour": "AppendMatrixContour [/W=graph] matrix",
    "ModifyContour": "ModifyContour [/W=graph] matrix,manLevels={first,increment,count},fill=0|1",

    "ImageThreshold": "ImageThreshold [/T=threshold /M=0|1|7 /I /N /R=roi[,mode] /P=layer /DEST=mask /O /C /Q /W=intervalWave] image",
    "StatsTTest": "StatsTTest [/MEAN=mean /PAIR /TAIL=1|2|4 /ALPH=.05 /CI /DFM=0|1|2 /DEST=result /T /Q] wave[,wave]",
    "Rotate": "Rotate integerPointShift,wave[,wave] (updates X offset)",
    "Unwrap": "Unwrap positiveModulus,wave[,wave] (1D float; NaNs separate runs)",
    "Make": "Make [/D /I /L /B /W /U /C /R /T /O /N=(rows[,columns,layers,chunks])] waveName [,waveName] (1D–4D, bounded)",
    "Duplicate": "Duplicate [/O] source,destination",
    "Rename": "Rename source,newName", "KillWaves": "KillWaves waveName [,waveName] (referenced waves are rejected)",
    "Redimension": "Redimension [/D /N=(rows[,columns])] waveName",
    "SetScale": 'SetScale [/P /I] x|y|d,start,deltaOrEnd,"units",waveName',
    "InsertPoints": "InsertPoints first,count,waveName [,waveName]",
    "DeletePoints": "DeletePoints first,count,waveName [,waveName]",
    "Concatenate": "Concatenate [/O /NP[=dimension] /DL] {wave1,wave2},destination (default promotes equal shapes; existing destination is appended)",
    "SplitWave": 'SplitWave [/N=base /NAME="a;b;" /DDF=folder /SDIM=dimensions /SCL=0|1 /NOTE /O] source',
    "Resample": "Resample [/UP=1 /DOWN=1 /RATE=frequency /SAME=wave /DIM=0 /N=21 /WINF=Hanning /E=2 /COEF=wave] wave [,wave]",
    "Note": 'Note [/K] wave[,"text"] (append; /K clears before replacing)',
    "SetDimLabel": "SetDimLabel dimension,index,label,wave [,wave] (index -1 names the dimension)",
    "Sort": "Sort keyWave,keyWave [,associatedWave]",
    "Extract": "Extract [/O] source,destination,source>0",
    "Variable": "Variable name=value [,name=value]", "String": 'String name="text"',
    "Print": "Print expression [,expression]", "NewDataFolder": "NewDataFolder [/O /S] root:folder",
    "SetDataFolder": "SetDataFolder root:folder:", "KillDataFolder": "KillDataFolder root:emptyFolder (non-empty folders are rejected)",
    "RenameDataFolder": "RenameDataFolder root:old,newName (references follow transactionally)",
    "MoveDataFolder": "MoveDataFolder root:source,root:destinationParent:",
    "MoveWave": "MoveWave wave,root:destinationParent:",
    "Display": "Display [/N=graphName /L=axis /R=axis] yWave [,yWave] [vs xWave]",
    "AppendToGraph": "AppendToGraph [/W=graph /L=axis /R=axis] yWave [,yWave] [vs xWave]",
    "RemoveFromGraph": "RemoveFromGraph [/W=graph] traceName [,traceName]",
    "ModifyGraph": "ModifyGraph [/W=graph] option[(traceOrAxis)]=value [,option=value]\nTrace: mode,marker,msize,lsize,lstyle,rgb,mrkFillRGB,mrkStrokeRGB,mrkThick,hide,offset,muloffset\nAxis: tick,mirror,axThick,btLen,stLen,btThick,stThick,minor,log,grid,fSize,font,axRGB,alblRGB,tickRGB,tlblRGB,manTick,manMinor,noLabel,tickUnit,lblPos,lblLatPos,freePos,axisEnab\nGeometry: width,height,margin,gbRGB,swapXY",
    "SetAxis": "SetAxis [/W=graph /A] axis min,max (use * for auto)",
    "Label": 'Label [/W=graph] axis "label or Igor formatted text"',
    "Legend": 'Legend [/W=graph /N=name /F=frame /A=anchor /X=x /Y=y /K] ["text"]',
    "TextBox": 'TextBox [/W=graph /N=name /F=frame /A=anchor /X=x /Y=y /K] "text"',
    "AppendText": 'AppendText [/W=graph /N=name] "text"',
    "Tag": 'Tag [/W=graph /N=name] trace,point,"text" (point-linked text; no Tag Arrow)',
    "ErrorBars": "ErrorBars [/W=graph] trace Y|X,wave=(lowWave,highWave)",
    "Cursor": "Cursor [/W=graph /P /C=(r,g,b) /K] A..J,trace,pointOrX (persistent trace cursor)",
    "ModifyTable": "ModifyTable [/W=table] digits(wave)=7,format(wave)=0..10,width(wave)=90,font=\"Arial\",size=12,showUnits=1",
    "ReorderTraces": "ReorderTraces [/W=graph] beforeTrace,{trace1,trace2}",
    "NewFreeAxis": "NewFreeAxis [/W=graph /L /R] axisName",
    "ModifyFreeAxis": "ModifyFreeAxis [/W=graph] freePos(axis)={fraction,kwFraction}",
    "KillFreeAxis": "KillFreeAxis [/W=graph] unusedAxisName",
    "AppendImage": "AppendImage [/W=graph] imageWave",
    "RemoveImage": "RemoveImage [/W=graph] imageWave",
    "ModifyImage": "ModifyImage [/W=graph] imageWave ctab={min,max,colorTable,reverse},log=0|1",
    "ColorScale": 'ColorScale [/W=graph /N=name /K] image=imageWave (AppendText edits label)',
    "ReorderImages": "ReorderImages [/W=graph] beforeImage,{image1,image2}",
    "SetDrawEnv": "SetDrawEnv [/W=graph] xcoord=prel,ycoord=prel,linefgc=(r,g,b),linethick=1",
    "SetDrawLayer": "SetDrawLayer [/W=graph] UserFront|UserBack|Overlay",
    "DrawLine": "DrawLine [/W=graph] x0,y0,x1,y1", "DrawRect": "DrawRect [/W=graph] x0,y0,x1,y1",
    "DrawOval": "DrawOval [/W=graph] x0,y0,x1,y1", "DrawText": 'DrawText [/W=graph] x,y,"text"',
    "Edit": "Edit [/N=tableName] waveName [,waveName]",
    "AppendToTable": "AppendToTable [/W=table] waveName [,waveName]",
    "RemoveFromTable": "RemoveFromTable [/W=table] waveName [,waveName]",
    "DoWindow": "DoWindow/F graphOrTable", "KillWindow": "KillWindow graphOrTable (undoable canonical removal)",
    "RenameWindow": "RenameWindow oldName,newName", "WaveStats": "WaveStats waveName",
    "Differentiate": "Differentiate [/D=destination] sourceWave",
    "Integrate": "Integrate [/D=destination] sourceWave",
    "Smooth": "Smooth [/B[=passes] /S=2|4 /E=0..3] countOrWindow,wave [,wave] (in-place; default binomial passes)",
    "SmoothDerived": "SmoothDerived [/S=polynomialOrder] windowSize,sourceWave (MDS uniform/SG derived result; not Igor Smooth)",
    "Histogram": "Histogram [/B={start,width,count} /DEST=destination /C /CUM /P /DP /W=weights] sourceWave[,destination]; bins are half open; /P uses all finite observations",
    "HistogramDerived": "HistogramDerived /B=binCount source,destination (MDS auto bins + edges)",
    "FFT": "FFT [/DEST=destination /Z /OUT=1..5] source (Igor positive-exponent FFT; default replaces working source)",
    "FFTDerived": "FFTDerived [/D=destination] source (legacy MDS full negative-exponent DFT)",
    "FindPeak": "FindPeak sourceWave", "FindLevel": "FindLevel [/Q /P /EDGE=0|1|2 /R=(startX,endX) or /R=[first,last]] sourceWave,level",
    "CurveFit": "CurveFit [/Q /M=2 /L=autoLength] line|poly nTerms,yWave [/X=xWave /W=weights /I[=0|1] /D[=dest] /R[=residual] /M=mask]; /C and held coefficients are explicitly unsupported. Line covariance is an MDS-only MDS_Covar.",
    "CurveFitDerived": "CurveFitDerived [/D=destination] line|polyN|gauss|exp,yWave[,xWave] (MDS Quick Fit)",
    "AppendBoxPlot": "AppendBoxPlot [/W=graph /TN=name /VERT /CATL /L=axis /R=axis] wave[,wave] [vs xWave]",
    "AppendViolinPlot": "AppendViolinPlot [/W=graph /TN=name /VERT /CATL /L=axis /R=axis] wave[,wave] [vs xWave]",
    "ModifyViolinPlot": "ModifyViolinPlot trace=name,bandwidth=0,bandwidthMethod=0..2,kernel=1..6,maxDensity=0,plotSide=0..2,showData=1,showMean=0,showMedian=0",
    "ModifyBoxPlot": "ModifyBoxPlot [/W=graph] trace=name,notched=0|1,quartileMethod=0..3,whiskerMethod=0..7,showData=All|None|Outliers|FarOutliers,showMean=0|1,jitter=value",
    "Help": "Help [commandName]", "Undo": "Undo", "Redo": "Redo",
}

# Prefer the common append operation when a short prefix matches several commands.
COMMAND_HELP = {"AppendToGraph": COMMAND_HELP.pop("AppendToGraph"), **COMMAND_HELP}

FLAGS = {
    "mdsstatistics": "KIND METH ALPH TAIL DEST N SEED T", "mdsprocess": "KIND METH DEST N NODC",
    "newlayout": "N", "appendlayoutobject": "W R",
    "imagethreshold": "T M I N R P DEST O C Q W", "statsttest": "MEAN PAIR TAIL ALPH CI DFM DEST T Q",
    "make": "N D I L B W U C R T O", "duplicate": "O", "redimension": "N D S I L B W U C R", "setscale": "P I",
    "concatenate": "O NP DL", "splitwave": "N NAME DDF SDIM SCL NOTE O", "resample": "UP DOWN RATE SAME DIM N WINF E COEF",
    "note": "K", "appendboxplot": "W TN VERT CATL L R B T", "extract": "O", "newdatafolder": "O S",
    "appendviolinplot": "W TN VERT CATL L R B T",
    "cursor": "W P K C", "errorbars": "W CLIP L T X Y RGB", "modifytable": "W",
    "display": "N L R B T HOST W", "appendtograph": "W L R B T", "setaxis": "W A Z",
    "modifygraph": "W Z", "label": "W Z", "killwaves": "Z",
    "legend": "W N F A X Y K C B", "textbox": "W N F A X Y K C B",
    "tag": "W N L X Y A F", "appendtext": "W N", "newfreeaxis": "W L R B T", "colorscale": "W N K C A X Y F B",
    "edit": "N", "dowindow": "F K", "appendimage": "W B L", "differentiate": "D", "integrate": "D", "fft": "DEST Z OUT REAL MAG MAGS",
    "smooth": "S B E", "smoothderived": "S", "histogram": "B DEST C CUM P DP W", "curvefit": "Q X W I D R M MASK L",
    "fftderived": "D", "histogramderived": "B", "curvefitderived": "D",
    "findlevel": "Q P EDGE R", "wavestats": "Q P R",
}


class CommandEngine:
    def __init__(self, services: DocumentServices) -> None:
        self.services = services
        self.outputs: list[str] = []
        self.annotation = ""
        self.colorbar = ""
        self.draw_env: tuple[Setting, ...] = ()
        self.draw_layer = "UserFront"

    def number(self, value: Any) -> Any:
        if isinstance(value, Argument):
            value = value.expression
        if not isinstance(value, Expression):
            raise ValueError("Expected a numeric/string expression")
        return evaluate(value, self.services.resolve,
                        functions=getattr(self.services, "expression_functions", None),
                        wave_parameters=getattr(self.services, "expression_wave_parameters", None),
                        wave_reference=getattr(self.services, "expression_wave_reference", None))

    def text(self, value: Any) -> str:
        return value.path if isinstance(value, Reference) else value.text if isinstance(value, Argument) else str(value)

    def names(self, value: Argument) -> list[str]:
        if value.expression is None:
            raise ValueError("Expected {wave1,wave2}")
        from ast import Name, Set, Tuple
        node = value.expression.node
        nodes = node.elts if isinstance(node, (Set, Tuple)) else [node]
        result = []
        for item in nodes:
            if not isinstance(item, Name):
                raise ValueError("Use literal wave names in a wave list")
            result.append(value.expression.references.get(item.id, item.id))
        return result

    def execute(self, source: str) -> CommandResult:
        original = self.services.document.current
        context = self.services.context.copy()
        transient = (self.annotation, self.colorbar, self.draw_env, self.draw_layer)
        self.services.document.current = original.clone()
        self.outputs, self.services.warnings = [], []
        try:
            if len(source) > 1_000_000:
                raise ValueError("Command source exceeds 1 MiB")
            commands = parse(source)
            if len(commands) > 1000:
                raise ValueError("A command call is limited to 1000 statements")
            for command in commands:
                self.apply(command)
            after = self.services.document.current
            before_waves = self.services.document.source.waves if original.waves is None else original.waves
            after_waves = self.services.document.render_source().waves
            old = {w.id: w for w in before_waves}
            affected = [w.igor_path for w in after_waves if old.get(w.id) is not w]
            new_ids = {w.id for w in after_waves}
            affected.extend(w.igor_path for w in before_waves if w.id not in new_ids)
            prior_graphs = {g.name: g for g in original.graphs}
            affected.extend(g.name for g in after.graphs if g != prior_graphs.get(g.name))
            if original.environment != after.environment:
                affected.append("environment")
            return CommandResult(True, value=self.outputs.copy() or None, message="\n".join(self.outputs), warnings=self.services.warnings.copy(), affected_objects=affected, status="Partial" if self.services.warnings else "Supported")
        except Exception as exc:
            self.services.document.current = original
            self.services.context = context
            self.annotation, self.colorbar, self.draw_env, self.draw_layer = transient
            return CommandResult(False, message=f"{type(exc).__name__}: {exc}", status="Unsupported" if isinstance(exc, NotImplementedError) else "Error")

    def apply(self, command: Operation | Assignment) -> None:
        s = self.services
        if isinstance(command, Assignment):
            path = s.path(command.target.path)
            existing = next((w for w in s.document.render_source().waves if w.igor_path == path), None)
            special = {}
            if existing is not None and not command.declaration:
                from materials_data_studio.tools.igor_context import coordinates
                special = coordinates(existing)
            value = evaluate(command.value, s.resolve, special,
                             getattr(s, "expression_functions", None),
                             getattr(s, "expression_wave_parameters", None),
                             getattr(s, "expression_wave_reference", None))
            if command.declaration and existing is not None:
                raise ValueError("A scalar cannot shadow an existing wave")
            if command.declaration == "string" and not isinstance(value, str):
                raise ValueError("String declaration requires scalar text")
            if command.declaration == "variable" and (isinstance(value, str) or np.ndim(value) != 0):
                raise ValueError("Variable declaration requires a scalar number")
            if path in s.environment.get("strings", {}) and not isinstance(value, str):
                raise ValueError("Cannot assign a number/array to a String")
            if path in s.environment.get("variables", {}) and (isinstance(value, str) or np.ndim(value) != 0):
                raise ValueError("Cannot assign text/array to a Variable")
            if command.operator != "=":
                import ast

                from materials_data_studio.tools.command_language import OPERATORS
                operation = {"+=": ast.Add, "-=": ast.Sub, "*=": ast.Mult, "/=": ast.Div}[command.operator]
                left = s.resolve(command.target.path)
                if isinstance(left, np.ndarray) and left.dtype.kind in "biufc":
                    left = left.astype(np.complex128 if left.dtype.kind == "c" else np.float64)
                value = OPERATORS[operation](left, value)
            if existing is not None and not command.declaration:
                value = np.broadcast_to(value, existing.shape)
                if existing.data.dtype.kind not in "US":
                    from materials_data_studio.tools.igor_context import assignment_values
                    value = assignment_values(value, existing.data.dtype)
                s.write([make_wave(path, value, existing)], overwrite=True)
            elif np.ndim(value) > 0:
                s.create_wave(path, value)
            else:
                if command.declaration == "string" and not isinstance(value, str):
                    raise ValueError("String declaration requires text")
                if command.declaration == "variable" and isinstance(value, str):
                    raise ValueError("Variable declaration requires a number")
                if not isinstance(value, str) and not math.isfinite(float(value)):
                    raise ValueError("Scalar variables must be finite")
                section = "strings" if isinstance(value, str) else "variables"
                s.environment.setdefault(section, {})[path] = value.item() if isinstance(value, np.generic) else value
                results = s.environment.get("operation_results", {})
                for key in list(results):
                    if key.casefold() == path.casefold():
                        del results[key]
            return
        name, args = command.name, command.arguments
        if name in {"duplicate", "rename", "renamewindow"} and len(command.arguments) != 2:
            raise ValueError(f"{name} requires two names separated by a space or comma")
        if name not in {key.lower() for key in COMMAND_HELP} | {"?"}:
            raise NotImplementedError(f"Unknown/unsupported command: {name}. Igor Function/Macro/Execute are not run.")
        flags = {flag.name: flag for flag in command.flags}
        if len(flags) != len(command.flags):
            raise ValueError("Duplicate operation flags are unsupported")
        allowed = FLAGS.get(name, "W" if name in GRAPH_COMMANDS | TABLE_COMMANDS else "").split()
        if set(flags) - set(allowed):
            raise ValueError("Unsupported flag(s): " + ", ".join(set(flags) - set(allowed)))
        def f(key: str, default: Any = None) -> Any:
            from materials_data_studio.tools.command_language import reference
            value = flags[key].value if key in flags else None
            if value is None:
                return default
            return reference(value).path if key in {"W", "N", "L", "R", "A", "B", "T"} else value
        def fn(key: str, default: Any) -> Any:
            return self.number(flags[key].expression) if key in flags else default
        def whole(key: str, default: int) -> int:
            value = fn(key, default)
            if np.ndim(value) or not np.isfinite(value) or int(value) != value:
                raise ValueError(f"/{key} requires an integer")
            return int(value)
        if "W" in flags and name != "display" and name in GRAPH_COMMANDS | TABLE_COMMANDS:
            target = f("W")
            if name in TABLE_COMMANDS:
                s.context["table"] = target
            else:
                s.graph(target)
                s.context["graph"] = target
        if name in {"help", "?"}:
            query = self.text(args[0]).lower() if args else ""
            self.outputs.extend(value for key, value in COMMAND_HELP.items() if not query or key.lower() == query)
        elif name == "print":
            self.outputs.append(" ".join(str(self.number(v)) for v in args))
        elif name == "make":
            from materials_data_studio.tools.igor_context import checked_shape, wave_dtype
            dtype = wave_dtype(set(flags))
            shape = checked_shape(fn("N", 128), dtype)
            for arg in args:
                s.create_wave(self.text(arg), np.zeros(tuple(map(int, shape)), dtype=dtype), overwrite="O" in flags)
        elif name == "duplicate":
            wave = s.wave(self.text(args[0]))
            s.create_wave(self.text(args[1]), wave.data, overwrite="O" in flags, template=replace(wave, id=""))
        elif name == "rename":
            s.document.current = rename_wave(s.document, s.wave(self.text(args[0])).igor_path, s.path(self.text(args[1])))
        elif name == "killwaves":
            for arg in args:
                try:
                    target = s.wave(self.text(arg)).igor_path
                except ValueError:
                    if "Z" in flags:
                        continue
                    raise
                # /Z suppresses only an absent wave, never a dependency error.
                s.document.current = delete_wave(s.document, target)
        elif name == "setscale":
            dimension, start, end = self.text(args[0]).lower(), float(self.number(args[1])), float(self.number(args[2]))
            if len(args) < 4:
                raise ValueError("SetScale requires a dimension, bounds and wave")
            scale_args: list[Any] = list(args)
            has_unit = len(scale_args) >= 5 and self.text(scale_args[3]).startswith('"')
            unit = str(self.number(scale_args[3])) if has_unit else None
            for arg in args[4:] if has_unit else args[3:]:
                wave = s.wave(self.text(arg))
                if dimension == "d":
                    changed = replace(wave, data_units=wave.data_units if unit is None else unit)
                else:
                    index = "xyzt".index(dimension)
                    if index >= wave.data.ndim:
                        raise ValueError("Scaling dimension is not present")
                    count = max(wave.shape[index] - int("I" in flags), 1)
                    delta = end if "P" in flags else (end - start) / count
                    if not math.isfinite(delta) or delta == 0 or not math.isfinite(start):
                        raise ValueError("Scaling start/delta must be finite and delta nonzero")
                    scaling, units = list(wave.dimension_scaling), list(wave.dimension_units)
                    scaling[index], units[index] = (start, delta), units[index] if unit is None else unit
                    changed = replace(wave, dimension_scaling=tuple(scaling), dimension_units=tuple(units))
                s.write([changed], overwrite=True)
        elif name in {"redimension", "insertpoints", "deletepoints"}:
            waves = args if name == "redimension" else args[2:]
            changes = []
            for arg in waves:
                wave = s.wave(self.text(arg))
                if name == "redimension":
                    from materials_data_studio.tools.igor_context import (
                        assignment_values,
                        checked_shape,
                        wave_dtype,
                    )
                    dtype = wave_dtype(set(flags), wave.data.dtype)
                    shape = checked_shape(fn("N", wave.shape), dtype)
                    if math.prod(shape) < wave.data.size:
                        s.warnings.append("Redimension truncates working data; Undo restores it.")
                    data = np.zeros(shape, dtype=dtype, order="F")
                    count = min(data.size, wave.data.size)
                    values = wave.data.real if "R" in flags and wave.data.dtype.kind == "c" else wave.data
                    selected = values.ravel(order="F")[:count]
                    data.ravel(order="F")[:count] = assignment_values(selected, dtype) if dtype.kind not in "US" else selected
                    changes.append(make_wave(wave.igor_path, data, wave))
                else:
                    first, count = self.number(args[0]), self.number(args[1])
                    if name == "deletepoints" and np.isposinf(count):
                        count = wave.shape[0] - int(first)
                    if not np.isfinite(first) or not np.isfinite(count) or int(first) != first or int(count) != count:
                        raise ValueError("Point index/count must be integers")
                    if name == "deletepoints" and count == 0 and first == wave.shape[0]:
                        continue
                    changes.append(resize_wave(wave, int(count), int(first), name == "deletepoints"))
            s.write(changes, overwrite=True)
        elif name == "extract":
            wave = s.wave(self.text(args[0]))
            mask = np.asarray(self.number(args[2]))
            if mask.shape != wave.shape or mask.dtype.kind != "b":
                raise ValueError("Extract requires a boolean mask with the source shape")
            s.create_wave(self.text(args[1]), wave.data[mask], overwrite="O" in flags, template=replace(wave, id=""))
        elif name == "concatenate":
            from materials_data_studio.tools.igor_wave_ops import concatenate_waves
            waves = [s.wave(ref) for ref in self.names(args[0])]
            dest = s.path(self.text(args[1]))
            axis = whole("NP", 0) if "NP" in flags and flags["NP"].value is not None else None
            if dest in {w.igor_path for w in waves}:
                raise ValueError("Concatenate destination cannot be a source")
            existing = s.document.render_source().wave(dest)
            if existing is not None and "O" not in flags:
                waves = [existing, *waves]
            result = concatenate_waves(waves, dest, dimension=axis, promote="NP" not in flags, labels="DL" in flags)
            s.write([result], overwrite=existing is not None)
        elif name == "splitwave":
            from materials_data_studio.tools.command_language import reference
            from materials_data_studio.tools.igor_wave_ops import split_wave
            wave = s.wave(self.text(args[0]))
            dim = whole("SDIM", wave.data.ndim - 1)
            if not 1 <= dim < wave.data.ndim:
                raise ValueError("SplitWave requires a multidimensional wave and lower output dimensionality")
            count = int(np.prod(wave.shape[dim:]))
            folder = s.path(reference(f("DDF")).path) if "DDF" in flags else s.current_folder
            if "NAME" in flags:
                names = str(self.number(flags["NAME"].expression)).rstrip(";").split(";")
                if len(names) < count or any(":" in n or not n for n in names):
                    raise ValueError("SplitWave /NAME requires a simple name for each output")
                names = names[:count]
            else:
                base = reference(f("N", wave.name)).path
                names = [base + str(i) for i in range(count)]
            paths = [folder + ":" + n for n in names]
            if wave.igor_path in paths:
                raise ValueError("SplitWave cannot overwrite its source")
            scaling = whole("SCL", 0)
            if scaling not in {0, 1}:
                raise ValueError("SplitWave /SCL must be 0 or 1")
            outputs = split_wave(wave, paths, dimensions=dim, copy_scaling=bool(scaling), copy_note="NOTE" in flags)
            s.write(outputs, overwrite="O" in flags)
            folders = set(s.document.render_source().folders)
            folders.update(":".join(folder.split(":")[:i]) for i in range(1, len(folder.split(":")) + 1))
            s.environment["folders"] = sorted(folders)
            s.environment.setdefault("strings", {})[s.path("S_waveNames")] = ";".join(names) + ";"
        elif name == "note":
            wave = s.wave(self.text(args[0]))
            note = "" if "K" in flags else wave.wave_note
            if len(args) > 1:
                value = self.number(args[1])
                if not isinstance(value, str):
                    raise ValueError("Wave note must be text")
                note += ("\r" if note and not note.endswith("\r") else "") + value
            if len(note.encode("utf-8")) > 1024 * 1024:
                raise ValueError("Wave note is limited to 1 MiB")
            s.write([replace(wave, wave_note=note)], overwrite=True)
        elif name == "setdimlabel":
            from materials_data_studio.tools.command_language import reference
            from materials_data_studio.tools.igor_wave_ops import set_dimension_label
            dim, index = self.number(args[0]), self.number(args[1])
            if int(dim) != dim or int(index) != index:
                raise ValueError("Dimension and label index must be integers")
            raw_label = self.text(args[2])
            label = str(self.number(expression(raw_label[1:]))) if raw_label.startswith('$') else reference(raw_label).path
            s.write([set_dimension_label(s.wave(self.text(a)), int(dim), int(index), label) for a in args[3:]], overwrite=True)
        elif name == "resample":
            from materials_data_studio.tools.command_language import reference
            from materials_data_studio.tools.igor_wave_ops import resample_wave
            if ("RATE" in flags and "SAME" in flags) or ({"RATE", "SAME"} & set(flags) and {"UP", "DOWN"} & set(flags)):
                raise ValueError("Choose UP/DOWN, RATE or SAME")
            if "COEF" in flags and flags["COEF"].value is None:
                raise NotImplementedError("Resample /COEF filter-output mode is not supported; use /COEF=wave")
            dim = whole("DIM", 0)
            options = dict(up=fn("UP", 1), down=fn("DOWN", 1), dimension=dim,
                           samples=fn("N", 21), window=reference(f("WINF", "Hanning")).path,
                           end=whole("E", 2), rate=fn("RATE", None),
                           same_delta=s.wave(reference(f("SAME")).path).dimension_scaling[dim][1] if "SAME" in flags else None,
                           coefficients=s.wave(reference(f("COEF")).path).data if "COEF" in flags else None)
            sources = [s.wave(self.text(a)) for a in args]
            if "COEF" in flags and s.wave(reference(f("COEF")).path).igor_path in {w.igor_path for w in sources}:
                raise ValueError("Coefficient wave cannot be a resampling destination")
            outputs = [resample_wave(w, **options) for w in sources]
            s.write(outputs, overwrite=True)
            if {"RATE", "SAME"} & set(flags):
                from fractions import Fraction
                ratio = Fraction(sources[-1].dimension_scaling[dim][1] / outputs[-1].dimension_scaling[dim][1]).limit_denominator(10000)
                s.set_results({"V_numerator": ratio.numerator, "V_denominator": ratio.denominator})
        elif name == "sort":
            key = s.wave(self.text(args[0]))
            if key.data.ndim != 1:
                raise ValueError("Sort requires 1D waves")
            order = np.argsort(key.data, kind="stable")
            targets = args[1:] or args[:1]
            waves = [s.wave(self.text(arg)) for arg in targets]
            if any(w.shape != key.shape for w in waves):
                raise ValueError("Associated sort waves must match the key length")
            s.write([make_wave(w.igor_path, w.data[order], w) for w in waves], overwrite=True)
        elif name in {"newdatafolder", "setdatafolder", "killdatafolder"}:
            path = s.path(self.text(args[0]))
            folders = set(s.document.render_source().folders)
            if name == "newdatafolder":
                if path in folders and "O" not in flags:
                    raise ValueError("Folder already exists; use /O explicitly")
                parent = path.rsplit(":", 1)[0]
                if parent not in folders:
                    raise ValueError("Create the parent folder first")
                folders.add(path)
                if "S" in flags:
                    s.environment["current_folder"] = path
            elif name == "setdatafolder":
                if path not in folders:
                    raise ValueError("Unknown data folder")
                s.environment["current_folder"] = path
            else:
                occupied = [*folders, *(w.igor_path for w in s.document.render_source().waves), *s.environment.get("variables", {}), *s.environment.get("strings", {}), *s.environment.get("operation_results", {})]
                if path == "root" or path == s.current_folder or any(p.startswith(path + ":") for p in occupied):
                    raise ValueError("Only empty, non-current data folders may be deleted")
                folders.remove(path)
            s.environment["folders"] = sorted(folders)
        elif name in {"renamedatafolder", "movedatafolder", "movewave"}:
            if len(args) != 2:
                raise ValueError("Two explicit operands are required")
            old, target = s.path(self.text(args[0])), s.path(self.text(args[1]))
            if name == "movewave":
                if target not in s.document.render_source().folders:
                    raise ValueError("Destination folder does not exist")
                wave = s.wave(old)
                s.document.current = rename_wave(s.document, old, target + ":" + wave.name)
            else:
                if name == "renamedatafolder":
                    new_name = self.text(args[1]).rstrip(":")
                    if ":" in new_name:
                        raise ValueError("RenameDataFolder requires a leaf name")
                    target = old.rsplit(":", 1)[0] + ":" + new_name
                else:
                    target += ":" + old.rsplit(":", 1)[-1]
                s.move_folder(old, target)
        elif name in {"mdsstatistics", "mdsprocess"}:
            from materials_data_studio.tools.graph_analysis_commands import execute_analysis
            execute_analysis(self, command, f, fn, whole)
        elif name in {"imagethreshold", "statsttest", "rotate", "unwrap"}:
            from materials_data_studio.tools.igor_discovery_analysis import (
                rotate_wave,
                t_test,
                threshold_image,
                unwrap_wave,
            )
            if name in {"rotate", "unwrap"}:
                value = self.number(args[0])
                outputs = [(rotate_wave if name == "rotate" else unwrap_wave)(s.wave(self.text(a)), value) for a in args[1:]]
                if not outputs:
                    raise ValueError("Select at least one wave")
                s.write(outputs, overwrite=True)
            elif name == "imagethreshold":
                from materials_data_studio.tools.command_language import reference
                source = s.wave(self.text(args[0]))
                roi, outside = None, 1
                if "R" in flags:
                    parts = split((flags["R"].value or "").strip("{}"))
                    roi = s.wave(reference(parts[0]).path)
                    outside = int(float(parts[1])) if len(parts) > 1 else 1
                dest = source.igor_path if "O" in flags else s.path(f("DEST", "M_ImageThresh"))
                if ("DEST" in flags and dest == source.igor_path) or ("O" in flags and ({"DEST", "P"} & set(flags))):
                    raise ValueError("Invalid source/destination or layer overwrite combination")
                wave, level, corr = threshold_image(source, threshold=fn("T", None), method=whole("M", 0),
                    invert="I" in flags, background_nan="N" in flags, roi=roi, outside=outside,
                    intervals=s.wave(reference(f("W")).path).data if "W" in flags else None,
                    layer=whole("P", -1), name=dest)
                s.write([wave], overwrite=True)
                results = {"V_threshold": level}
                if "C" in flags:
                    results["V_correlation"] = corr
                s.set_results(results)
                if "Q" not in flags:
                    self.outputs.append(str(results))
            else:
                from materials_data_studio.tools.igor_wave_ops import set_dimension_label
                if not 1 <= len(args) <= 2:
                    raise ValueError("StatsTTest takes one or two waves")
                result = t_test(s.wave(self.text(args[0])), s.wave(self.text(args[1])) if len(args) == 2 else None,
                    mean=fn("MEAN", None), paired="PAIR" in flags, tail=whole("TAIL", 4),
                    alpha=fn("ALPH", .05), df_method=whole("DFM", 0), confidence="CI" in flags)
                dest = s.path(f("DEST", "W_StatsTTest"))
                if dest in {s.wave(self.text(a)).igor_path for a in args}:
                    raise ValueError("T-test output cannot overwrite input")
                wave = make_wave(dest, list(result.values()))
                for index, label in enumerate(result):
                    wave = set_dimension_label(wave, 0, index, label)
                s.write([replace(wave, wave_note="StatsTTest: " + str({"inputs": [self.text(a) for a in args], "paired": "PAIR" in flags, "mean": fn("MEAN", None), "alpha": fn("ALPH", .05), "tail": whole("TAIL", 4)}))], overwrite=True)
                s.set_results({"V_flag": 0})
                if "T" in flags:
                    state = s.document.current
                    name = dest.rsplit(":", 1)[-1] + "Table"
                    state.tables = (*(t for t in s.document.render_source().table_definitions if t.name != name), PxpTable(name, (dest,)))
                if "Q" not in flags:
                    self.outputs.append(str(result))
        elif name in GRAPH_COMMANDS:
            self.graph_command(command, f, fn)
        elif name in TABLE_COMMANDS | {"dowindow", "killwindow", "renamewindow"}:
            self.window_command(command, f)
        elif name in ANALYSIS_COMMANDS:
            self.analysis(command, f, fn)
        else:
            raise NotImplementedError(f"{name} is an editor action, not a procedure runtime command")

    def graph_command(self, command: Operation, flag: Any, number_flag: Any) -> None:
        s, name, args = self.services, command.name, command.arguments
        if name in {"newlayout", "appendlayoutobject", "appendmatrixcontour", "modifycontour"}:
            from materials_data_studio.tools.igor_canvas import canvas_command
            canvas_command(self, command, flag, number_flag)
            return
        if name == "cursor":
            from materials_data_studio.tools.igor_graph_extensions import apply_cursor
            apply_cursor(self, command, flag)
            return
        if name in {"appendboxplot", "modifyboxplot", "appendviolinplot", "modifyviolinplot"}:
            from materials_data_studio.tools.igor_box import append_box, modify_box
            from materials_data_studio.tools.igor_violin import append_violin, modify_violin
            graph = s.graph()
            if name in {"appendboxplot", "appendviolinplot"}:
                refs, x = args
                graph = (append_box if name == "appendboxplot" else append_violin)(graph, tuple(s.wave(r.path).igor_path for r in refs), s.wave(x.path).igor_path if x else None,
                                   name=flag("TN", ""), axis=flag("R", "right") if any(f.name == "R" for f in command.flags) else flag("L", "left"),
                                   x_axis=flag("T", "top") if any(f.name == "T" for f in command.flags) else flag("B", "bottom"),
                                   vertical=any(f.name == "VERT" and f.value != "0" for f in command.flags),
                                   categories=any(f.name == "CATL" and f.value != "0" for f in command.flags))
            else:
                traces = list(graph.traces)
                (modify_box if name == "modifyboxplot" else modify_violin)(traces, ",".join(self.text(a) for a in args))
                graph = replace(graph, traces=tuple(traces))
            s.put_graph(graph)
            return
        if name == "display":
            created = s.new_graph(flag("N", "Graph0"))
            if any(f.name == "HOST" for f in command.flags):
                host = s.graph(flag("HOST"))
                if host.host or host.window_kind != "Graph":
                    raise ValueError("Subwindows support one graph host level")
                rect = tuple(map(float, number_flag("W", (.55, .05, .95, .45))))
                width, height = host.style.window_size or (640, 480)
                if max(rect) <= 1:
                    rect = tuple(v * (width if i % 2 == 0 else height) for i, v in enumerate(rect))
                if len(rect) != 4 or not np.isfinite(rect).all() or not 0 <= rect[0] < rect[2] <= width or not 0 <= rect[1] < rect[3] <= height:
                    raise ValueError("Subwindow rectangle must be within its host")
                s.put_graph(replace(created, host=host.name, host_rect=rect))
            elif any(f.name == "W" for f in command.flags):
                left, top, right, bottom = map(float, number_flag("W", (0,0,640,480)))
                if right <= left or bottom <= top:
                    raise ValueError("Invalid graph window rectangle")
                s.put_graph(replace(created, style=replace(created.style, window_size=(right-left, bottom-top))))
        graph = s.graph()
        optional = any(f.name == "Z" for f in command.flags)
        if name in {"display", "appendtograph"}:
            if sum(f.name in {"L", "R"} for f in command.flags) > 1 or sum(f.name in {"B", "T"} for f in command.flags) > 1:
                raise ValueError("Specify only one Y-axis flag and one X-axis flag")
            for y in args[0]:
                axis = flag("R", "right") if any(f.name == "R" for f in command.flags) else flag("L", "left")
                x_axis = flag("T", "top") if any(f.name == "T" for f in command.flags) else flag("B", "bottom")
                s.append(y.path, args[1].path if args[1] else None, axis,
                         "right" if any(f.name == "R" for f in command.flags) else "left",
                         x_axis, "top" if any(f.name == "T" for f in command.flags) else "bottom")
                if y.point_range is not None:
                    updated = s.graph()
                    if y.point_range[1] >= s.wave(y.path).shape[0]:
                        raise ValueError("Trace point range exceeds wave length")
                    s.put_graph(replace(updated, traces=(*updated.traces[:-1], replace(updated.traces[-1], point_range=y.point_range))))
            return
        if name == "label":
            if optional and not any(a.name == args[0].path for a in graph.axes):
                return
            s.set_axis(args[0].path, label=str(self.number(args[1])))
            return
        if name == "setaxis":
            if optional and not any(a.name == args[0].path for a in graph.axes):
                return
            limits = (None, None) if any(f.name == "A" for f in command.flags) else tuple(None if v is None else float(self.number(v)) for v in args[1:])
            if len(limits) != 2 or any(v is not None and not math.isfinite(v) for v in limits):
                raise ValueError("Two finite limits or * are required")
            s.set_axis(args[0].path, limits=limits)
            return
        if name in {"modifygraph", "modifyfreeaxis"}:
            trace_options = {"mode", "marker", "msize", "lsize", "rgb", "mrkfillrgb", "mrkstrokergb", "mrkthick", "opaque", "usemrkstrokergb", "lstyle", "loptions", "offset", "muloffset", "linejoin", "hide", "hidetrace", "zcolor", "logzcolor", "zmrksize", "zmrknum"}
            ignored = {"zeroline", "usecomma", "axisontop", "usetsep"}
            resolved_options = []
            for option in args:
                if option.index is not None:
                    if option.name.lower() not in trace_options:
                        raise ValueError("Indexed ModifyGraph options must target traces")
                    if option.index >= len(graph.traces):
                        if optional:
                            continue
                        raise ValueError("Unknown trace index")
                    option = replace(option, target=graph.traces[option.index].name, index=None)
                if option.target and option.name.lower() != "margin":
                    exists = (any(t.name == option.target for t in graph.traces)
                              if option.name.lower() in trace_options
                              else any(a.name == option.target for a in graph.axes))
                    if not exists and optional:
                        continue
                resolved_options.append(option)
            args = tuple(resolved_options)
            for option in args:
                if option.name.lower() in ignored:
                    raise NotImplementedError(f"Unsupported graph option: {option.name}")
                if option.target and option.name.lower() in trace_options and not any(t.name == option.target for t in graph.traces):
                    raise ValueError(f"Unknown trace: {option.target}")
                if option.target and option.name.lower() not in trace_options and option.name.lower() != "margin" and not any(a.name == option.target for a in graph.axes):
                    raise ValueError(f"Unknown axis: {option.target}")
            traces, axes, settings, issues = list(graph.traces), list(graph.axes), {"style": graph.style, "swap_xy": graph.swap_xy}, []
            axis_options = {"tick", "mirror", "axthick", "btlen", "stlen", "btthick", "stthick", "ftlen", "ftthick", "minor", "log", "grid", "fsize", "fstyle", "font", "gfont", "lblmargin", "axoffset", "lblpos", "lblposmode", "lbllatpos", "lblrot", "tklblrot", "nolabel", "tickunit", "zaptz", "reverse", "nticks", "sep", "axrgb", "alblrgb", "tickrgb", "tlblrgb", "gridrgb", "mantick", "manminor", "userticks", "standoff", "zero", "gridhair", "lowtrip", "hightrip", "notation", "zisz", "tickenab", "dateinfo"}
            layout = s.document.current.layout.setdefault(graph.name, {})
            for option in args:
                key = option.name.lower()
                selected = [t.name for t in traces if option.target is None or t.name == option.target]
                if key in {"hide", "hidetrace"}:
                    visible = not bool(int(self.number(option.expression)))
                    for target in selected:
                        layout.setdefault("view", {}).setdefault("traces", {}).setdefault(target, {})["visible"] = visible
                    continue
                value = option.value
                if value.startswith("{") and key in {"offset", "muloffset", "axisenab", "mantick", "manminor", "tickenab", "tickzap", "dateinfo"}:
                    evaluated = [self.number(expression(item)) for item in split(value.strip("{} "))]
                    if any(np.ndim(item) for item in evaluated):
                        raise ValueError("Style tuples require scalar numeric components")
                    value = "{" + ",".join(map(str, evaluated)) + "}"
                if option.expression is not None and key not in {"width", "height", "freepos", "userticks"} and not value.startswith("{"):
                    evaluated = self.number(option.expression)
                    value = "(" + ",".join(map(str, evaluated)) + ")" if isinstance(evaluated, tuple) else str(evaluated)
                _modify_graph(((option.name, option.target, value),), traces, axes, settings, issues, s.current_folder)
                if option.target is None and key in axis_options:
                    for axis in list(axes):
                        _modify_graph(((option.name, axis.name, value),), traces, axes, settings, issues)
                field = {"rgb": "color", "mrkfillrgb": "marker_fill", "mrkstrokergb": "marker_edge"}.get(key)
                if field:
                    for target in selected:
                        layout.get("view", {}).get("traces", {}).get(target, {}).pop(field, None)
                if key in {"fsize", "font", "gfont"}:
                    from materials_data_studio.tools.graph_render import explicit_font
                    axes = [replace(a, label_raw=explicit_font(a.label_raw, size=1 if key == "fsize" else None, family="" if key != "fsize" else None)) if option.target is None or a.name == option.target else a for a in axes]
                if key in {"lblpos", "lbllatpos", "lblposmode", "axoffset"}:
                    for axis in axes:
                        if option.target is None or axis.name == option.target:
                            layout.get("labels", {}).pop(axis.name, None)
            if issues:
                raise ValueError("; ".join(i.message for i in issues))
            graph = replace(graph, traces=tuple(traces), axes=tuple(axes), style=settings["style"], swap_xy=settings["swap_xy"])
        elif name == "removefromgraph":
            names = [self.text(a) for a in args]
            if not all(any(t.name == n or t.y_wave == n for t in graph.traces) for n in names):
                raise ValueError("Unknown trace")
            graph = replace(graph, traces=tuple(t for t in graph.traces if t.name not in names and t.y_wave not in names))
            graph = replace(graph, annotations=tuple(a for a in graph.annotations if not a.trace or any(t.name == a.trace for t in graph.traces)), cursors=tuple(c for c in graph.cursors if any(t.name == c.trace for t in graph.traces)))
        elif name == "errorbars":
            target, direction, low, high = args[:4]
            if not any(t.name == target.path for t in graph.traces):
                raise ValueError("Unknown error-bar trace")
            if any(np.any(np.asarray(s.wave(ref.path).data, dtype=float) < 0) for ref in (*args[2:4], *args[4:])):
                raise ValueError("Error amplitudes must be nonnegative")
            if direction == "XY" and len(args) != 6:
                raise ValueError("XY error bars require X and Y wave pairs")
            updates: dict[str, Any] = dict(error_width=float(number_flag("L", .9)), error_cap=float(number_flag("Y", number_flag("X", 2))),
                           error_cap_thickness=float(number_flag("T", 1)), error_clip=bool(number_flag("CLIP", 1)))
            if not all(math.isfinite(updates[k]) and updates[k] >= 0 for k in ("error_width", "error_cap", "error_cap_thickness")):
                raise ValueError("Error bar dimensions must be nonnegative")
            if any(f.name == "RGB" for f in command.flags):
                channels = number_flag("RGB", (0,0,0))
                if len(channels) != 3 or any(not math.isfinite(c) or int(c) != c or not 0 <= c <= 65535 for c in channels):
                    raise ValueError("Error bar RGB requires three integer channels in 0..65535")
                updates["error_color"] = tuple(map(int, channels))
            cap_values = {f.name for f in command.flags}
            if {"X", "Y"} <= cap_values and number_flag("X", 2) != number_flag("Y", 2):
                raise ValueError("This subset uses one shared cap size; different /X and /Y are unsupported")
            if number_flag("CLIP", 1) not in {0,1}:
                raise ValueError("Error bar CLIP must be 0 or 1")
            if direction in {"X", "XY"}:
                updates.update(x_error_low=s.wave(low.path).igor_path, x_error_high=s.wave(high.path).igor_path)
            if direction in {"Y", "XY"}:
                ly, hy = args[4:] if direction == "XY" else (low, high)
                updates.update(error_axis="Y", error_low=s.wave(ly.path).igor_path, error_high=s.wave(hy.path).igor_path)
            graph = replace(graph, traces=tuple(replace(t, **updates) if t.name == target.path else t for t in graph.traces))
        elif name in {"textbox", "legend", "appendtext", "tag"}:
            annotations = list(graph.annotations)
            target = flag("N", self.annotation if name == "appendtext" else "legend0" if name == "legend" else f"text{len(annotations)}")
            if name == "appendtext" and self.colorbar:
                graph = replace(graph, images=tuple(replace(i, colorbar_label_raw=i.colorbar_label_raw + str(self.number(args[0]))) if i.wave == self.colorbar else i for i in graph.images))
            else:
                old = next((a for a in annotations if a.name == target), None)
                if any(f.name == "K" for f in command.flags):
                    annotations = [a for a in annotations if a.name != target]
                    text = ""
                else:
                    text = str(self.number(args[-1])) if args else "\r".join(f"\\s({t.name}) {t.name}" for t in graph.traces)
                    if name == "appendtext":
                        if old is None:
                            raise ValueError("No selected annotation for AppendText")
                        text = old.raw + text
                    style = old.style if old else PxpAnnotationStyle(frame=0, transparent=True, anchor="LT", font_size=0, inherit_graph_font=True)
                    style = replace(style, anchor=flag("A", style.anchor), frame=int(number_flag("F", style.frame)), x_percent=float(number_flag("X", style.x_percent)), y_percent=float(number_flag("Y", style.y_percent)))
                    if any(f.name == "B" for f in command.flags):
                        background = number_flag("B", 0)
                        if isinstance(background, tuple):
                            if len(background) != 3 or any(int(v) != v or not 0 <= v <= 65535 for v in background):
                                raise ValueError("Annotation background requires three RGB components in [0,65535]")
                            style = replace(style, background_color=tuple(map(int, background)), background_mode=0, transparent=False)
                        elif int(background) != background or background not in {0, 1, 2, 3}:
                            raise ValueError("Annotation background mode must be 0,1,2,3 or an RGB tuple")
                        else:
                            style = replace(style, background_mode=int(background), transparent=background == 1)
                    annotation = PxpAnnotation(target, text, style)
                    if name == "tag":
                        trace, index = self.text(args[0]), int(self.number(args[1]))
                        found = next((t for t in graph.traces if t.name == trace), None)
                        if found is None or index < 0 or index >= s.wave(found.y_wave).shape[0]:
                            raise ValueError("Tag requires an existing trace and valid point index")
                        arrow = int(number_flag("L", 0))
                        if arrow not in range(5):
                            raise ValueError("Tag /L supports 0..4")
                        annotation = replace(annotation, trace=trace, point=index, arrow=arrow)
                    annotations = [a for a in annotations if a.name != target] + [annotation]
                graph = replace(graph, annotations=tuple(annotations), legend_raw=text if name == "legend" else graph.legend_raw)
            self.annotation, self.colorbar = target, ""
        elif name in {"newfreeaxis", "killfreeaxis"}:
            target = self.text(args[0])
            if name == "newfreeaxis":
                if any(a.name == target for a in graph.axes):
                    raise ValueError("Axis already exists")
                side_flags = [f.name for f in command.flags if f.name in {"L", "R", "B", "T"}]
                if len(side_flags) > 1:
                    raise ValueError("Specify one free-axis side")
                side = {"L": "left", "R": "right", "B": "bottom", "T": "top"}[side_flags[0] if side_flags else "L"]
                graph = replace(graph, axes=(*graph.axes, PxpAxis(target, side=side)))
            else:
                if target in {"left", "bottom", "right", "top"} or any(target in {t.axis, t.x_axis} for t in graph.traces):
                    raise ValueError("Cannot delete a standard or referenced axis")
                graph = replace(graph, axes=tuple(a for a in graph.axes if a.name != target))
        elif name in {"appendimage", "removeimage"}:
            wave = s.wave(self.text(args[0]))
            if name == "appendimage":
                if flag("B", "bottom") != "bottom" or flag("L", "left") != "left":
                    raise NotImplementedError("Images currently support the bottom/left axis pair only")
                coordinates = tuple(None if self.text(a) == "*" else s.wave(self.text(a)).igor_path for a in args[1:])
                graph = replace(graph, images=(*graph.images, PxpImage(wave.igor_path, "Grays", x_wave=coordinates[0] if coordinates else None, y_wave=coordinates[1] if coordinates else None)))
            else:
                graph = replace(graph, images=tuple(i for i in graph.images if i.wave != wave.igor_path))
        elif name == "setimagecoordinates":
            if len(args) != 3:
                raise ValueError("SetImageCoordinates takes image,xEdges,yEdges")
            path = s.wave(self.text(args[0])).igor_path
            coordinates = [None if self.text(a) == "*" else s.wave(self.text(a)).igor_path for a in args[1:]]
            if not any(i.wave == path for i in graph.images):
                raise ValueError("Image is not in the graph")
            graph = replace(graph, images=tuple(replace(i, x_wave=coordinates[0], y_wave=coordinates[1]) if i.wave == path else i for i in graph.images))
        elif name == "modifyimage":
            ref, settings = args
            images, issues = list(graph.images), []
            path = s.wave(ref.path).igor_path
            if not any(i.wave == path or s.document.render_source().wave(i.wave) is s.wave(path) for i in images):
                raise ValueError("Image is not in the active graph")
            images = [replace(i, wave=path) if s.document.render_source().wave(i.wave) is s.wave(path) else i for i in images]
            rendered = []
            for option in settings:
                value = option.value
                if option.name.lower() == "ctab":
                    pieces = split(value.strip("{} "))
                    if len(pieces) != 4:
                        raise ValueError("ctab requires lower,upper,colorTable,reverse")
                    for index in (0, 1, 3):
                        if pieces[index] not in {"*", ""}:
                            pieces[index] = str(self.number(expression(pieces[index])))
                    value = "{" + ",".join(pieces) + "}"
                elif option.expression is not None:
                    value = str(self.number(option.expression))
                rendered.append((option.name, value))
            _modify_image((path, tuple(rendered)), images, issues)
            if issues:
                raise ValueError("; ".join(i.message for i in issues))
            graph = replace(graph, images=tuple(images))
        elif name == "colorscale":
            raw = self.text(args[0]) if args else ""
            if raw.startswith("trace="):
                target = raw.removeprefix("trace=")
                trace = next((t for t in graph.traces if t.name == target), None)
                if trace is None or trace.z_color is None or trace.z_color[3].casefold() == "directrgb":
                    raise ValueError("ColorScale trace requires a scalar color wave")
                s.put_graph(replace(graph, traces=tuple(replace(t, color_scale=not any(f.name == "K" for f in command.flags)) if t.name == target else t for t in graph.traces)))
                return
            ref = raw.removeprefix("image=")
            path = s.wave(ref).igor_path if ref else graph.images[0].wave
            graph = replace(graph, images=tuple(replace(i, wave=s.wave(i.wave).igor_path) for i in graph.images))
            if not any(i.wave == path for i in graph.images):
                raise ValueError("ColorScale image is not in the active graph")
            graph = replace(graph, images=tuple(replace(i, colorbar_visible=not any(f.name == "K" for f in command.flags)) if i.wave == path else i for i in graph.images))
            from materials_data_studio.tools.igor_graph_extensions import colorscale_options
            options = []
            for arg in args[1:]:
                key, equal, value = self.text(arg).partition("=")
                if not equal:
                    options.append(("axisLabel", self.number(arg)))
                else:
                    options.append((key, self.number(expression(value))))
            from materials_data_studio.tools.pxp_recreation import _annotation
            native_flags = " ".join("/" + f.name + ("=" + str(f.value) if f.value is not None else "") for f in command.flags)
            graph = replace(graph, images=tuple(colorscale_options(replace(i, colorbar_style=_annotation(native_flags, i.colorbar_style)), options) if i.wave == path else i for i in graph.images))
            self.colorbar = path
        elif name in {"reordertraces", "reorderimages"}:
            target, ordered = self.text(args[0]), self.names(args[1])
            objects = list(graph.traces if name == "reordertraces" else graph.images)
            def object_key(obj: Any) -> str:
                return obj.name if name == "reordertraces" else obj.wave
            found = {object_key(obj): obj for obj in objects}
            if len(set(ordered)) != len(ordered) or any(k not in found for k in ordered) or target not in found or target in ordered:
                raise ValueError("Invalid reorder targets")
            remaining = [obj for obj in objects if object_key(obj) not in ordered]
            index = next(i for i, obj in enumerate(remaining) if object_key(obj) == target)
            remaining[index:index] = [found[k] for k in ordered]
            graph = replace(graph, **{"traces" if name == "reordertraces" else "images": tuple(remaining)})
        elif name == "setdrawenv":
            self.draw_env = args
            return
        elif name == "setdrawlayer":
            self.draw_layer = self.text(args[0])
            return
        elif name in {"drawline", "drawrect", "drawoval", "drawtext"}:
            coordinates = tuple(float(self.number(a)) for a in (args[:2] if name == "drawtext" else args))
            fields = {"xcoord": "prel", "ycoord": "prel", "layer": self.draw_layer}
            mapping = {"linethick": "line_width", "linefgc": "color", "fillfgc": "fill_color", "arrow": "arrow", "fsize": "font_size", "font": "font_family", "fillpat": "fill_mode"}
            for setting in self.draw_env:
                if setting.name in {"xcoord", "ycoord"}:
                    fields[setting.name] = setting.value
                elif setting.name in mapping:
                    fields[mapping[setting.name]] = self.number(setting.expression)
                else:
                    raise ValueError(f"Unsupported drawing option: {setting.name}")
            drawing = PxpDrawing({"drawline": "DrawLine", "drawrect": "DrawRect", "drawoval": "DrawOval", "drawtext": "DrawText"}[name], coordinates, str(self.number(args[2])) if name == "drawtext" else "", **fields)
            graph = replace(graph, drawings=(*graph.drawings, drawing))
        s.put_graph(graph)

    def window_command(self, command: Operation, flag: Any) -> None:
        s, name, args = self.services, command.name, command.arguments
        tables = list(s.document.render_source().table_definitions)
        from materials_data_studio.tools.igor_graph_extensions import table_reference
        if name == "modifytable":
            from materials_data_studio.tools.igor_graph_extensions import modify_table
            target = s.context.get("table", tables[-1].name if tables else "")
            table = next((t for t in tables if t.name == target), None)
            if table is None:
                raise ValueError("No active table")
            updated = modify_table(table, args, s, self.number)
            s.document.current.tables = tuple(updated if t is table else t for t in tables)
            return
        if name == "edit":
            table_name = flag("N", "Table0")
            if any(t.name == table_name for t in tables):
                raise ValueError("Table already exists")
            tables.append(PxpTable(table_name, tuple(table_reference(self.text(a), s) for a in args)))
            s.context["table"] = table_name
            s.context["selected_kind"] = "table"
        elif name in {"appendtotable", "removefromtable"}:
            index = next((i for i, t in enumerate(tables) if t.name == s.context.get("table")), None)
            if index is None:
                raise ValueError("No active table")
            paths = tuple(table_reference(self.text(a), s) for a in args)
            table = tables[index]
            tables[index] = replace(table, columns=table.columns + paths if name == "appendtotable" else tuple(c for c in table.columns if c not in paths))
        else:
            target = self.text(args[0])
            graph = next((g for g in s.document.current.graphs if g.name == target), None)
            table = next((t for t in tables if t.name == target), None)
            if name == "dowindow":
                if len(command.flags) != 1 or command.flags[0].name not in {"F", "K"}:
                    raise ValueError("DoWindow requires exactly /F or /K")
                s.environment.setdefault("variables", {})["root:V_flag"] = int(graph is not None or table is not None)
                if graph is None and table is None:
                    return
                if command.flags[0].name == "K":
                    name = "killwindow"
            if graph is None and table is None:
                raise ValueError("Unknown graph/table window")
            if name == "dowindow":
                s.context["graph" if graph else "table"] = target
                s.context["selected_kind"] = "graph" if graph else "table"
                return
            if name == "killwindow":
                if any(g.host == target or any(item.graph == target for item in g.layout_items) for g in s.document.current.graphs):
                    raise ValueError("Window is referenced by a layout or subwindow; remove those references first")
                s.document.current.graphs = tuple(g for g in s.document.current.graphs if g.name != target)
                s.document.current.layout.pop(target, None)
                tables = [t for t in tables if t.name != target]
                kind = "graph" if graph else "table"
                if s.context.get(kind) == target:
                    s.context.pop(kind, None)
                    s.context.pop("selected_kind", None)
            else:
                new = self.text(args[1])
                if any(g.name == new for g in s.document.current.graphs) or any(t.name == new for t in tables):
                    raise ValueError("Window name already exists")
                s.document.current.graphs = tuple(replace(g, name=new if g.name == target else g.name, host=new if g.host == target else g.host, layout_items=tuple(replace(item, graph=new) if item.graph == target else item for item in g.layout_items)) for g in s.document.current.graphs)
                tables = [replace(t, name=new) if t.name == target else t for t in tables]
                if target in s.document.current.layout:
                    s.document.current.layout[new] = s.document.current.layout.pop(target)
                s.context["graph" if graph else "table"] = new
                s.context["selected_kind"] = "graph" if graph else "table"
        s.document.current.tables = tuple(tables)

    def analysis(self, command: Operation, flag: Any, number_flag: Any) -> None:
        if command.name in {"fft", "histogram", "curvefit"}:
            self.transform_analysis(command)
            return
        if command.name in {"smooth", "findlevel", "wavestats"}:
            self.native_analysis(command)
            return
        from scipy.integrate import cumulative_trapezoid
        from scipy.ndimage import uniform_filter1d
        from scipy.signal import savgol_filter
        s, name, args = self.services, command.name.removesuffix("derived") if command.name != "smoothderived" else command.name, command.arguments
        wave_arg = args[1] if name in {"smoothderived", "curvefit"} else args[0]
        wave = s.wave(self.text(wave_arg))
        if wave.data.ndim != 1 or wave.data.dtype.kind not in "biuf" or wave.data.size < 2:
            raise ValueError("Analysis requires a real numeric 1D wave with at least two points")
        if wave.data.size > 10_000_000:
            raise ValueError("Interactive derived analysis is limited to 10 million points")
        data = np.asarray(wave.data, dtype=float)
        x = wave.coordinate(0)
        destination = flag("D", wave.name + "_" + ("smooth" if name == "smoothderived" else name))
        if name == "differentiate":
            values = np.gradient(data, x)
        elif name == "integrate":
            values = cumulative_trapezoid(data, x, initial=0)
        elif name == "smoothderived":
            window = int(self.number(args[0]))
            if window < 1 or window > len(data):
                raise ValueError("Invalid smoothing window")
            values = savgol_filter(data, window, int(number_flag("S", 2))) if any(f.name == "S" for f in command.flags) else uniform_filter1d(data, window, mode="nearest")
        elif name == "histogram":
            bins = number_flag("B", 10)
            if int(bins) != bins or not 1 <= bins <= 1_000_000:
                raise ValueError("Histogram bin count must be an integer in [1,1000000]")
            values, edges = np.histogram(data[np.isfinite(data)], bins=int(bins))
            destination = self.text(args[1])
            s.create_wave(destination + "_edges", edges)
            s.create_wave(destination, values)
            return
        elif name == "fft":
            values = np.fft.fft(data)
            s.create_wave(destination + "_frequency", np.fft.fftfreq(len(data), wave.dimension_scaling[0][1]))
            s.warnings.append("FFT is unnormalized complex DFT; the frequency wave is in reciprocal X units.")
        elif name == "findpeak":
            index = int(np.nanargmax(data))
            self.outputs.append(str(dict(point=index, x=float(x[index]), y=float(data[index]))))
            return
        elif name == "curvefit":
            from materials_data_studio.tools.graph_fit import quick_fit
            model = self.text(args[0])
            if model.startswith("poly") and model[4:].isdigit():
                model = "poly:" + model[4:]
            result = quick_fit(s.wave(self.text(args[2])).data if len(args) > 2 else x, data, model)
            s.create_wave(destination + "_x", result.x)
            s.create_wave(destination, result.calculated)
            self.outputs.append(result.summary())
            return
        else:
            raise NotImplementedError(name)
        created = s.create_wave(destination, values)
        if name != "fft":
            s.write([replace(created, dimension_scaling=wave.dimension_scaling, dimension_units=wave.dimension_units)], overwrite=True)
        s.warnings.append("Derived data units are not inferred; verify physical units.")

    def transform_analysis(self, command: Operation) -> None:
        from materials_data_studio.tools.igor_transforms import forward_fft, histogram, linear_fit
        s, args = self.services, command.arguments
        flags = {f.name: f for f in command.flags}
        for key in {"DEST", "OUT", "X", "W", "M", "MASK", "L"} & flags.keys():
            if flags[key].value is None:
                raise ValueError(f"/{key} requires a value")
        for key in {"Q", "Z", "REAL", "MAG", "MAGS", "CUM", "C", "P", "DP"} & flags.keys():
            if flags[key].value is not None:
                raise ValueError(f"/{key} does not take a value")
        if len({"REAL", "MAG", "MAGS"} & flags.keys()) > 1:
            raise ValueError("FFT output convenience flags are mutually exclusive")
        def value(key: str, default: Any = None) -> Any:
            item = flags.get(key)
            return self.number(item.expression) if item and item.expression is not None else default
        def target(key: str, default: str) -> str:
            item = flags.get(key)
            return item.value if item and item.value else default
        if command.name == "fft":
            if len(args) != 1:
                raise ValueError("FFT requires one source wave")
            wave = s.wave(self.text(args[0]))
            output = value("OUT", 2 if "REAL" in flags else 3 if "MAG" in flags else 4 if "MAGS" in flags else 1)
            values, start, delta = forward_fft(wave.data, wave.dimension_scaling[0][1], rotate="Z" not in flags, output=output)
            destination = target("DEST", "W_FFT" if set(flags) - {"Z"} else wave.igor_path)
            if "DEST" in flags and s.path(destination) == wave.igor_path:
                raise ValueError("/DEST must differ from FFT source")
            if s.path(destination) == wave.igor_path:
                created = make_wave(wave.igor_path, values, wave)
            else:
                created = make_wave(s.path(destination), values)
            units = {"s": "Hz", "Hz": "s", "m": "m^-1", "m^-1": "m"}.get(wave.dimension_units[0], "")
            s.write([replace(created, dimension_scaling=((start, delta),), dimension_units=(units,), data_units=wave.data_units)], overwrite=True)
        elif command.name == "histogram":
            if not 1 <= len(args) <= 2:
                raise ValueError("Histogram requires a source and explicit bins/destination")
            wave = s.wave(self.text(args[0]))
            destination = target("DEST", self.text(args[1]) if len(args) == 2 else "W_Histogram")
            bins = value("B")
            if not isinstance(bins, tuple) or len(bins) != 3:
                if bins != 2:
                    raise ValueError("Use /B={start,width,count} or /B=2 with an existing scaled destination; MDS /B=count is HistogramDerived")
                existing = s.wave(destination)
                bins = (*existing.dimension_scaling[0], existing.shape[0])
            start, width, count = bins
            weights = s.wave(target("W", "")).data if "W" in flags else None
            values = histogram(wave.data, start, width, count, probability="P" in flags,
                               cumulative="CUM" in flags, weights=weights, double="DP" in flags)
            created = make_wave(s.path(destination), values)
            s.write([replace(created, dimension_scaling=((start + (width / 2 if set(flags) & {"P", "C"} else 0), width),),
                             dimension_units=(wave.data_units,))], overwrite=True)
        else:
            if len(args) not in {2, 3}:
                raise ValueError("CurveFit requires model,yWave and optional X wave")
            model = self.text(args[0]).lower()
            degree = int(model.split()[1])-1 if model.startswith("poly ") else 1 if model == "line" else int(model[4:]) if model.startswith("poly") and model[4:].isdigit() else 0
            if not degree:
                raise ValueError("CurveFit native subset: line/poly1..10; other MDS models use CurveFitDerived")
            wave = s.wave(self.text(args[1]))
            if wave.data.dtype.kind not in "iuf":
                raise ValueError("CurveFit requires real data")
            x_wave = s.wave(target("X", self.text(args[2]) if len(args) == 3 else "")) if "X" in flags or len(args) == 3 else None
            if x_wave is not None and x_wave.data.dtype.kind not in "iuf":
                raise ValueError("CurveFit X wave must be real numeric")
            x = np.asarray(x_wave.data if x_wave is not None else wave.coordinate(0), dtype=np.float64)
            observed = np.asarray(wave.data, dtype=np.float64)
            weights = s.wave(target("W", "")).data if "W" in flags else None
            mode = value("I", 1 if "I" in flags else 0)
            if mode not in {0, 1} or ("M" in flags and value("M") != 2):
                raise ValueError("Supported CurveFit flags: /I=0|1 and /M=2 covariance")
            selected = s.wave(target("MASK", "")).data if "MASK" in flags else None
            coef, sigma, cov, residual, chi = linear_fit(x, observed, sigma=weights, direct_weights=mode == 0, degree=degree, selected=selected)
            s.create_wave("W_coef", coef, overwrite=True)
            s.create_wave("W_sigma", sigma, overwrite=True)
            if "M" in flags:
                s.create_wave("MDS_Covar" if model == "line" else "M_Covar", cov, overwrite=True)
                if model == "line":
                    s.warnings.append("Native line fits do not create M_Covar; the additional MDS covariance is named MDS_Covar.")
            if "D" in flags:
                destination = target("D", "fit_" + wave.name)
                if flags["D"].value is None:
                    count = value("L", 2 if model == "line" else 200)
                    if int(count) != count or not 2 <= count <= 1_000_000:
                        raise ValueError("Auto fit output length must be 2..1000000")
                    usable = np.isfinite(residual)
                    grid = np.linspace(float(np.min(x[usable])), float(np.max(x[usable])), int(count))
                    values = np.polynomial.polynomial.polyval(grid, coef)
                    created = s.create_wave(destination, values, overwrite=True)
                    s.write([replace(created, dimension_scaling=((float(grid[0]), float(grid[1]-grid[0])),),
                                     dimension_units=wave.dimension_units)], overwrite=True)
                    graph = next((g for g in s.document.current.graphs if g.name == s.context.get("graph")), None)
                    source_trace = next((t for t in graph.traces if t.y_wave == wave.igor_path), None) if graph else None
                    if graph and source_trace:
                        axis = next((a for a in graph.axes if a.name == source_trace.x_axis), None)
                        if (axis and axis.style.log) or graph.style.axis_defaults.log:
                            s.warnings.append("Fit output uses uniform X sampling; native logarithmic auto-fit sampling/fitX waves are not reproduced.")
                    if graph and source_trace and not any(t.y_wave == created.igor_path for t in graph.traces):
                        s.append(created.igor_path, axis=source_trace.axis, x_axis=source_trace.x_axis)
                else:
                    created = s.create_wave(destination, np.polynomial.polynomial.polyval(x, coef), overwrite=True)
                    s.write([replace(created, dimension_scaling=wave.dimension_scaling, dimension_units=wave.dimension_units)], overwrite=True)
            if "R" in flags:
                created = s.create_wave(target("R", "Res_" + wave.name), residual, overwrite=True)
                s.write([replace(created, dimension_scaling=wave.dimension_scaling, dimension_units=wave.dimension_units)], overwrite=True)
                if flags["R"].value is None and s.context.get("graph"):
                    s.warnings.append("Residual wave generated; add it to a graph explicitly. Native automatic residual-axis layout is not reproduced.")
            s.set_results({"V_chisq": chi, "V_npnts": float(np.count_nonzero(np.isfinite(residual))),
                           "V_numCoefficients": float(len(coef)), "V_FitError": 0.0})
            if model == "line":
                finite = np.isfinite(residual)
                s.set_results({"V_rab": float(np.corrcoef(x[finite], observed[finite])[0,1])})
            if "Q" not in flags:
                self.outputs.append(f"CurveFit {model}: W_coef={coef}, chi-square={chi:.8g}")

    def native_analysis(self, command: Operation) -> None:
        from materials_data_studio.tools.igor_analysis import (
            find_level,
            selected_points,
            smooth,
            wave_stats,
        )
        s, args, name = self.services, command.arguments, command.name
        flags = {f.name: f for f in command.flags}
        for key, item in flags.items():
            if key in {"Q", "P"} and item.value is not None:
                raise ValueError(f"/{key} does not take a value")
            if key in {"S", "E", "EDGE", "R"} and item.value is None:
                raise ValueError(f"/{key} requires a value")
        def number(key: str, default: Any = None) -> Any:
            item = flags.get(key)
            return self.number(item.expression) if item and item.expression is not None else default
        if name == "smooth":
            if len(args) < 2:
                raise ValueError("Smooth requires count/window and wave name(s)")
            count = float(self.number(args[0]))
            if count >= 50 and "B" not in flags and "S" not in flags:
                s.warnings.append("Smooth uses exact binomial passes; Igor may use a fast approximation at 50 or more passes.")
            for target in args[1:]:
                wave = s.wave(self.text(target))
                values = smooth(wave.data,count,box=number("B",1 if "B" in flags else None),
                                order=number("S"),end=number("E",0))
                # Keep scientific metadata, dtype and wave identity; immutable
                # source arrays are never overwritten in place.
                s.write([make_wave(wave.igor_path, values.astype(wave.data.dtype), wave)],overwrite=True)
            return
        expected = 2 if name == "findlevel" else 1
        if len(args) != expected:
            raise ValueError(f"{name} requires {expected} operand(s)")
        wave = s.wave(self.text(args[0]))
        if wave.data.ndim != 1 or wave.data.dtype.kind not in "biufc" or not wave.data.size:
            raise ValueError("This analysis subset requires a nonempty numeric 1D wave")
        if wave.data.size > 10_000_000:
            raise ValueError("Interactive analysis is limited to 10 million points")
        points = selected_points(wave, flags["R"].value if "R" in flags else None, self.number)
        if name == "findlevel":
            if np.iscomplexobj(wave.data):
                raise ValueError("FindLevel requires a real wave")
            result = find_level(wave,points,float(self.number(args[1])),number("EDGE",0),"P" in flags)
            if result["V_flag"] and "Q" not in flags:
                raise ValueError("No level crossing; use FindLevel/Q to retain V_flag=1 and continue")
        else:
            result = wave_stats(wave,points)
        s.set_results(result)
        if "Q" not in flags:
            self.outputs.append(str(result))


GRAPH_COMMANDS = {"setimagecoordinates","newlayout", "appendlayoutobject", "appendmatrixcontour", "modifycontour","display", "appendtograph", "removefromgraph", "modifygraph", "setaxis", "label", "legend", "textbox", "appendtext", "tag", "errorbars", "reordertraces", "newfreeaxis", "modifyfreeaxis", "killfreeaxis", "appendimage", "removeimage", "modifyimage", "colorscale", "reorderimages", "setdrawenv", "setdrawlayer", "drawline", "drawrect", "drawoval", "drawtext"}
GRAPH_COMMANDS |= {"appendboxplot", "modifyboxplot", "appendviolinplot", "modifyviolinplot", "cursor"}
TABLE_COMMANDS = {"edit", "appendtotable", "removefromtable", "modifytable"}
ANALYSIS_COMMANDS = {"wavestats", "differentiate", "integrate", "smooth", "smoothderived", "histogram", "fft", "findpeak", "findlevel", "curvefit", "fftderived", "histogramderived", "curvefitderived"}
