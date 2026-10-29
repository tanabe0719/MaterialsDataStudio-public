"""Synthetic conversion/dispatch tests; no private procedures or research data."""
from __future__ import annotations

import sys

import numpy as np
import pytest

from materials_data_studio.tools.command_engine import CommandEngine
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_document import EditableGraphDocument
from materials_data_studio.tools.igor_procedure_conversion import (
    analyze_procedures,
    build_python,
    logical_statements,
)
from materials_data_studio.tools.igor_procedure_runtime import IgorAPI, validate_command
from materials_data_studio.tools.procedure_api import install_api
from materials_data_studio.tools.procedure_policy import validate
from materials_data_studio.tools.pxp_model import PxpDocument


@pytest.fixture
def services():
    source = PxpDocument("generated", "synthetic.mdsg", "", 0, "10", "synthetic", "", status="FULL")
    return DocumentServices(EditableGraphDocument.from_pxp(source))


def run_converted(source, services, monkeypatch, arguments=None, entry=None):
    report = analyze_procedures(source)
    routine = next(r for r in report.routines if r.name == entry) if entry else report.routines[0]
    assert routine.supported, routine.diagnostics
    python = build_python(report, routine.name, arguments or [])
    validate(python)
    monkeypatch.setitem(sys.modules, "mds", None)
    install_api(services)
    exec(compile(python, "Synthetic converted procedure", "exec"), {})
    return report, python


def test_convert_style_macro_indexed_targets_and_optional_axes(services, monkeypatch):
    api = IgorAPI(services)
    api.command("Make/D/N=5 sample; Display/N=Graph sample")
    source = r'''
Proc FormatPlot()
    PauseUpdate; Silent 1
    ModifyGraph/Z rgb[0]=(12000,24000,48000),marker[0]=8,marker[3]=10
    ModifyGraph tick=2,mirror=1,axThick=1.2,fSize=13
    Label/Z right,"missing axis"
    Label left,"Signal!"
    SetAxis/Z top,0,20
EndMacro
'''
    run_converted(source, services, monkeypatch)
    graph = services.graph("Graph")
    assert graph.traces[0].marker == 8
    assert graph.traces[0].color_igor == (12000, 24000, 48000)
    assert next(a for a in graph.axes if a.name == "left").label_raw == "Signal!"
    assert graph.style.axis_defaults.tick == 2


def test_functions_loops_aliases_dynamic_names_and_defaults(services, monkeypatch):
    source = '''
Function ScaleValues(target, Factor)
    String target
    Variable factor=2
    Wave values=$target
    values=values*factor
    return WaveMax(values)
End
Function CreateData(count)
    Variable count=5
    Make/D/N=(count) data
    Wave data
    Variable i,total=0
    for(i=0;i<count;i++)
        if(i<2)
            data[i]=i+1
        else
            data[i]=i*2
        endif
        total+=data[i]
    endfor
    ScaleValues("data",3)
    Display/N=Result data
    return total
End
'''
    report, _ = run_converted(source, services, monkeypatch, entry="CreateData")
    assert report.routines[0].defaults == {"Factor": 2}
    np.testing.assert_array_equal(services.wave("data").data, [3, 6, 12, 18, 24])


def test_wave_parameters_preserve_references_across_aliased_calls_and_commands(services, monkeypatch):
    source = '''Function Scale(w, factor)
Wave w
Variable factor
w *= factor
return WaveMax(w)
End
Function Relay(w)
Wave w
return Scale(w,3)
End
Function Create()
Make/D/N=3 original
original=p+1
Wave alias=original
Relay(alias)
Print Scale(alias,2)
End'''
    run_converted(source, services, monkeypatch, entry="Create")
    np.testing.assert_array_equal(services.wave("original").data, [6, 12, 18])
    assert services.environment.get("variables", {}) == {}


def test_two_dimensional_assignments_interpolation_and_text_waves(services, monkeypatch):
    source = '''
Function MakeMap()
    Make/D/N=3 xs,ys
    xs=p
    ys=p*2
    Make/D/N=(3,2) image
    Variable i
    for(i=0;i<2;i+=1)
        image[][i]=interp(p,xs,ys)+i
    endfor
    Make/T/N=2 names
    names[0]="readable first name"
    names[1]="Signal!"
    return image[1][1]
End
'''
    run_converted(source, services, monkeypatch)
    np.testing.assert_array_equal(services.wave("image").data, [[0, 1], [2, 3], [4, 5]])
    assert services.wave("names").data.tolist() == ["readable first name", "Signal!"]


def test_source_analysis_and_conversion_never_run_imports(services, monkeypatch):
    def forbidden(*_args):
        raise AssertionError("Analyzing/previewing must never run source")
    monkeypatch.setattr(CommandEngine, "execute", forbidden)
    before = services.document.current
    report = analyze_procedures("Function Demo()\nMake/N=3 newwave\nEnd")
    assert report.routines[0].supported
    assert "scope.command" in build_python(report, "Demo", [])
    assert services.document.current is before


@pytest.mark.parametrize("unsupported", [
    'Execute "anything"', 'Open/R/UNSAFE ref', 'LoadWave/T "data.dat"',
    'NewPanel/UNSAFE/N=Panel', 'Button button', 'ExternalXOP wave',
    'Variable x=__import__("os")', 'Variable x=wave.__class__',
    'while(1)', 'Variable/UNSAFE shared=1',
])
def test_unsupported_blocks_routine_and_callers_only(unsupported):
    report = analyze_procedures(f'''Function Unsafe()
{unsupported}
End
Function Caller()
Unsafe()
End
Function Safe()
Print 2
End''')
    assert [r.supported for r in report.routines] == [False, False, True]
    assert report.routines[0].diagnostics[0].line == 2
    with pytest.raises(ValueError):
        build_python(report, "Caller", [])
    assert "mdsRoutine1()" in build_python(report, "Safe", [])


@pytest.mark.parametrize("source", [
    "Function Test()\nif(1)\nelse\nelse\nendif\nEnd",
    "Function Test()\nif(1)\nelse\nelseif(1)\nendif\nEnd",
    "Function Test()\nfor(i=0;i<3;i++)\nendif\nEnd",
    "Function Test()\nbreak\nEnd",
    "Function Test()\nif(1)\nEnd",
])
def test_malformed_control_flow_is_not_reported_supported(source):
    assert not analyze_procedures(source).routines[0].supported


def test_recursive_routines_and_case_insensitive_duplicates_are_blocked():
    report = analyze_procedures("Function A()\nB()\nEnd\nFunction B()\nA()\nEnd")
    assert all(not r.supported for r in report.routines)
    report = analyze_procedures("Function Demo()\nEnd\nFunction DEMO()\nEnd")
    assert all(not r.supported for r in report.routines)


def test_large_dependency_dag_is_analyzed_without_path_explosion():
    source = "\n".join(f"Function Routine{i}()\n" + "\n".join(f"Routine{j}()" for j in (i+1, i+2) if j < 40) + "\nEnd" for i in range(40))
    assert all(r.supported for r in analyze_procedures(source).routines)


@pytest.mark.parametrize("command", [
    "Make/D=invalid/N=2 probe", "Make/N=2/N=3 probe", "Make/N probe",
    "Make/N=3 probe; Execute bad", "Make/N=3 probe; LoadWave data",
    "ModifyGraph/Z unknownOption=1", "ModifyGraph/Z axThick=-3",
])
def test_command_bridge_fail_closed_and_atomic(services, command):
    api = IgorAPI(services)
    before = services.document.current
    with pytest.raises(ValueError):
        api.command(command)
    assert services.document.current is before


def test_optional_targets_do_not_suppress_invalid_properties_or_dependencies(services):
    api = IgorAPI(services)
    api.command("Make/D/N=3 data; Display data")
    before = services.document.current
    with pytest.raises(ValueError):
        api.command("KillWaves/Z data")
    assert services.document.current is before
    api.command("KillWaves/Z missing; ModifyGraph/Z marker[99]=8")
    with pytest.raises(ValueError):
        api.command("ModifyGraph marker[99]=8")
    with pytest.raises(ValueError):
        validate_command("ModifyGraph/Z notAProperty(missing)=0")


def test_dynamic_name_cannot_inject_commands(services):
    api = IgorAPI(services)
    scope = api.scope({"name": "data; KillWaves victim"})
    scope.command("Make/D/N=3 $name")
    assert services.wave("data; KillWaves victim").shape == (3,)
    with pytest.raises(ValueError):
        api.scope({"name": "data' ; Make victim"}).command("Make/N=2 $name")
    assert len(services.document.render_source().waves) == 1


def test_local_types_missing_aliases_and_qualified_globals_fail_closed(services):
    scope = IgorAPI(services).scope()
    scope.declare("variable", "factor", "2")
    with pytest.raises(ValueError):
        scope.assign("factor", '\"text\"')
    scope.alias("optional", "missing", optional=True)
    assert scope.evaluate("WaveExists(optional)") == 0
    with pytest.raises(ValueError):
        scope.assign("optional", "2")
    with pytest.raises(ValueError):
        scope.assign("root:gain", "2")


def test_nested_calls_share_active_graph_context(services, monkeypatch):
    source = '''
Function Inner()
Display/N=Second data
End
Function Outer()
Make/N=3 data
Display/N=First data
Inner()
ModifyGraph tick=2
End'''
    run_converted(source, services, monkeypatch, entry="Outer")
    assert services.graph("First").style.axis_defaults.tick is None
    assert services.graph("Second").style.axis_defaults.tick == 2
    assert services.context["graph"] == "Second"


def test_supported_analysis_warnings_are_visible_not_errors(services, capsys):
    api = IgorAPI(services)
    api.command("Make/D/N=4 data; data=p*2; Differentiate/D=derivative data")
    np.testing.assert_array_equal(services.wave("derivative").data, [2, 2, 2, 2])
    assert "Warning:" in capsys.readouterr().out


def test_step_budget_is_shared_across_scopes(services):
    from materials_data_studio.tools.igor_procedure_runtime import MAX_RUNTIME_STEPS
    api = IgorAPI(services)
    first, second = api.scope(), api.scope()
    api._steps = MAX_RUNTIME_STEPS - 1
    assert first.evaluate("1") == 1
    with pytest.raises(RuntimeError, match="step budget"):
        second.evaluate("1")


def test_raw_dispatch_is_not_exposed_to_procedure_code(services):
    scope = IgorAPI(services).scope()
    assert not hasattr(scope, "engine")
    with pytest.raises(ValueError, match="Attribute is not allowed"):
        validate('from mds import igor\nigor.scope()._engine.execute("Print 1")')


def test_comments_continuations_bang_literals_and_argument_validation(services):
    statements = logical_statements('Print "a;b! // text"; // comment\nPrint 1+\\\n2\n/* ignored */ Print 4')
    assert [s[1] for s in statements] == ['Print "a;b! // text"', 'Print 1+ 2', 'Print 4']
    scope = IgorAPI(services).scope()
    assert scope.evaluate('\"Signal!\"') == "Signal!"
    assert scope.evaluate("!0")
    optional = analyze_procedures("Function Demo()\nWave/z optional=missing\nEnd").routines[0]
    assert optional.supported and optional.statements[0].extra[1] == "True"
    report = analyze_procedures("Function Demo(factor)\nVariable factor=2\nPrint factor\nEnd")
    assert build_python(report, "Demo", []).endswith("mdsRoutine1(2)\n")
    for args in ([float("nan")], [{}], [1, 2]):
        with pytest.raises(ValueError):
            build_python(report, "Demo", args)
