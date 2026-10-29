"""Synthetic scientific/resource counterparts; never run private research IPFs."""
from __future__ import annotations

import sys
import zipfile

import numpy as np
import pytest

from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_document import save_graph_document
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.igor_procedure_conversion import analyze_procedures, build_python
from materials_data_studio.tools.igor_procedure_runtime import IgorAPI
from materials_data_studio.tools.igor_virtual_io import (
    INPUT_RESOURCES_KEY,
    VirtualTextIO,
    validate_resources,
)
from materials_data_studio.tools.procedure_api import install_api
from materials_data_studio.tools.procedure_policy import validate

MAP_SOURCE = '''Function CreateMap()
    NewPath/O/M="Select prepared profiles" collection
    String files=IndexedFile(collection,-1,".txt")
    Variable count=ItemsInList(files),i
    Make/T/O/N=(count) fileNames
    Make/D/O/N=(count) conditions
    for(i=0;i<count;i+=1)
        fileNames[i]=StringFromList(i,files)
        conditions[i]=str2num(StringFromList(1,ReplaceString(".txt",fileNames[i],""),"_"))
    endfor
    Sort conditions,conditions,fileNames
    Make/D/O/N=(3,count) map
    for(i=0;i<count;i+=1)
        String filename=fileNames[i]
        LoadWave/G/D/N=frame/P=collection filename
        Wave xdata=frame0,ydata=frame1
        map[][i]=interp(p,xdata,ydata)
        KillWaves/Z frame0,frame1
    endfor
    SetScale/P x,0,1,"deg",map
    SetScale/P y,1,1,"condition",map
    Display/N=MapGraph
    AppendImage/B=bottom/L=left map
    ModifyGraph standoff=0
    String colorTable="Rainbow"
    Variable reverseColor=1
    ModifyImage map ctab={*,*,$colorTable,reverseColor}
    ColorScale/C/N=Scale
End'''

VIRTUAL_FILE_SOURCE = '''Function ImportPrepared()
    Variable handle
    String filter="Text:"
    filter+="*.txt;"
    Open/D/R/F=filter/M="Select prepared text" handle
    String input=S_fileName
    ReadMeasurement(input)
End
Function ReadMeasurement(input)
    String input
    Variable handle
    Open/R handle as input
    FStatus handle
    Variable count=V_logEOF
    String contents=PadString("",count,32)
    FBinRead handle,contents
    Close handle
    Variable cut=strsearch(contents,"[Data]",0)+6
    String numeric=contents[cut,Inf]
    String temporary=ParseFilePath(1,input,"\\\\",1,0)+"derived-in-memory.txt"
    Open handle as temporary
    FBinWrite handle,numeric
    Close handle
    LoadWave/G/D/N/L={0,0,0,0,0} temporary
    String loaded=S_waveNames
    Rename $StringFromList(0,loaded,";"),temperature
    Rename $StringFromList(1,loaded,";"),field
    Rename $StringFromList(2,loaded,";"),moment
    DeleteFile temporary
End'''

MAGNETIC_SOURCE = '''Function AnalyzeMagnetic(weightMg,molarMass)
    Variable weightMg,molarMass
    Wave temperature,field,moment
    Make/D/O/N=(numpnts(moment)) susceptibility=(moment/field)*(molarMass/(weightMg/1000))
    Make/D/O/N=(numpnts(moment)) inverse=1/susceptibility
    Variable count=numpnts(temperature),turn=count-1,i
    for(i=0;i<count-1;i+=1)
        if(temperature[i+1]<temperature[i])
            turn=i
            break
        endif
    endfor
    Duplicate/O temperature,warmingT
    DeletePoints turn+1,Inf,warmingT
    Duplicate/O susceptibility,warmingChi
    DeletePoints turn+1,Inf,warmingChi
    Duplicate/O temperature,coolingT
    DeletePoints 0,turn+1,coolingT
    Duplicate/O susceptibility,coolingChi
    DeletePoints 0,turn+1,coolingChi
    DoWindow/K Susceptibility
    Display/N=Susceptibility warmingChi vs warmingT
    AppendToGraph coolingChi vs coolingT
    ModifyGraph mirror=1,tick=2,standoff=0,mode=4,marker[0]=8,marker[1]=19
    Label left,"Molar susceptibility"
    Label bottom,"Temperature"
    Legend/C/N=Legend/F=0/B=1/A=MC
    TileWindows/O=1/C
End'''

PANEL_SOURCE = '''Function Controls()
    NewDataFolder/O/S root:settings
    Variable/G sampleMass=8,molecularMass=120
    SetDataFolder root:
    DoWindow/F ControlsPanel
    if(V_flag!=0)
        return 0
    endif
    NewPanel/W=(80,80,440,280)/N=ControlsPanel as "Measurement settings"
    GroupBox sample,title="Sample",pos={10,10},size={320,100}
    SetVariable mass,title="Weight / mg",value=root:settings:sampleMass,pos={20,30},size={200,20}
    SetVariable composition,title="Molar mass",value=root:settings:molecularMass,pos={20,60},size={200,20}
    Button analyze,title="Analyze",proc=AnalyzeClick,pos={20,140},size={200,30},bcolor=(100,100,100)
End
Function AnalyzeClick(event):ButtonControl
    STRUCT WMButtonAction &event
    if(event.eventCode!=2)
        return 0
    endif
    NVAR mass=root:settings:sampleMass
    NVAR molarMass=root:settings:molecularMass
    Make/D/O/N=3 result
    result=mass/molarMass+p
End'''

PROFILE_SOURCE = '''Function Profile()
    Variable handle
    Open/D/R handle
    String selected=S_fileName
    Open/R handle as selected
    FStatus handle
    String contents=PadString("",V_logEOF,32)
    FBinRead handle,contents
    Close handle
    Variable splitPoint=StrSearch(contents,"[Curve]",0)
    String header=contents[0,splitPoint-1]
    String points=contents[splitPoint+7,Inf]
    Open/A handle as "curve-memory"
    Variable offset
    for(offset=0;offset<strlen(points);offset+=7)
        FPrintf handle,points[offset,offset+6]
    endfor
    Close/A
    LoadWave/G/N/D "curve-memory"
    String names=S_waveNames
    Rename $StringFromList(0,names),angle
    Rename $StringFromList(1,names),observed
    Rename $StringFromList(2,names),calculated
    Make/D/O/N=(numpnts(observed)) residual=observed-calculated
    Display/N=ProfileGraph residual,calculated,observed vs angle
    ModifyGraph offset(residual)={0,-WaveMax(observed)-1},mode(observed)=3
    AppendToGraph observed vs angle
    ModifyGraph mode(observed#1)=3,marker(observed#1)=8
    ModifyGraph tick=2,mirror=1,ZisZ(left)=1,tickEnab(left)={0,Inf},gfSize=12
    Legend/C/N=ProfileLegend/F=0/B=1/A=MC
    DeleteFile "curve-memory"
End'''

REFLECTION_SOURCE = '''Function ReflectionMarkers()
    LoadWave/G/D/N "reflections.txt"
    String loaded=S_waveNames
    Rename $StringFromList(0,loaded),hIndex
    Rename $StringFromList(1,loaded),kIndex
    Rename $StringFromList(2,loaded),lIndex
    Rename $StringFromList(3,loaded),phaseIndex
    Rename $StringFromList(4,loaded),peakAngle
    Make/D/N=3 observed
    observed=20+p
    Display/N=ReflectionsGraph observed
    Variable count=numpnts(peakAngle),group,i,n,j
    for(group=1;group<=2;group+=1)
        n=0
        for(i=0;i<count;i+=1)
            if(phaseIndex[i]==group)
                n+=1
            endif
        endfor
        String positions="phase"+num2str(group)
        String markerName="markers"+num2str(group)
        Make/D/N=(n) $positions
        Make/D/N=(n) $markerName
        Wave destination=$positions
        j=0
        for(i=0;i<count;i+=1)
            if(phaseIndex[i]==group)
                destination[j]=peakAngle[i]
                j+=1
            endif
        endfor
        AppendToGraph $markerName vs $positions
        ModifyGraph mode($markerName)=3,marker($markerName)=10,offset($markerName)={-0.5,-WaveMax(observed)}
    endfor
End'''


@pytest.fixture
def services():
    return DocumentServices(empty_document())


def execute(source, services, monkeypatch, *, entry, arguments=None, inputs=None):
    report = analyze_procedures(source)
    assert all(r.supported for r in report.routines), [r.diagnostics for r in report.routines]
    code = build_python(report, entry, arguments or [])
    validate(code)
    if inputs is not None:
        services.environment[INPUT_RESOURCES_KEY] = inputs
    monkeypatch.setitem(sys.modules, "mds", None)
    api = install_api(services)
    exec(compile(code, "Synthetic workflow", "exec"), {})
    api.igor.finish()
    return api


def resource(name, text):
    return {"name": name, "path": "C:\\prepared\\" + name, "text": text}


def test_profiles_to_scaled_image_uses_selected_files_only(services, monkeypatch):
    inputs = [resource("scan_2.txt", "metadata 10\nx y\n0 3\n1 6\n2 9\n"),
              resource("scan_1.txt", "metadata 20\nx y\n0 2\n1 4\n2 6\n")]
    execute(MAP_SOURCE, services, monkeypatch, entry="CreateMap", inputs=inputs)
    np.testing.assert_array_equal(services.wave("conditions").data, [1, 2])
    np.testing.assert_allclose(services.wave("map").data, [[2, 3], [4, 6], [6, 9]])
    assert services.wave("map").dimension_scaling == ((0., 1.), (1., 1.))
    assert services.wave("map").dimension_units == ("deg", "condition")
    image = services.graph("MapGraph").images[0]
    assert image.color_table == "Rainbow" and image.reverse and image.colorbar_visible
    assert "igor_input_resources" not in services.environment


def test_raw_read_virtual_split_load_and_mass_sign_units(services, monkeypatch):
    text = "Instrument version,2026\r\nMass,8\r\n[Data]\r\nTemperature,Field,Moment\r\n10,100,1\r\n20,100,-2\r\n30,-100,3\r\n20,-100,-4\r\n10,100,5\r\n"
    execute(VIRTUAL_FILE_SOURCE, services, monkeypatch, entry="ImportPrepared", inputs=[resource("measurement.txt", text)])
    execute(MAGNETIC_SOURCE, services, monkeypatch, entry="AnalyzeMagnetic", arguments=[8, 120])
    expected = np.array([1., -2., 3., -4., 5.]) / np.array([100., 100., -100., -100., 100.]) * (120 / .008)
    np.testing.assert_allclose(services.wave("susceptibility").data, expected)
    np.testing.assert_allclose(services.wave("inverse").data, 1 / expected)
    np.testing.assert_array_equal(services.wave("warmingT").data, [10, 20, 30])
    np.testing.assert_array_equal(services.wave("coolingT").data, [20, 10])
    assert len(services.graph("Susceptibility").traces) == 2
    assert services.graph("Susceptibility").annotations[0].style.transparent


def test_profile_split_chunked_formatted_write_and_numeric_columns(services, monkeypatch):
    text = "Synthetic metadata\r\n1 2 3\r\n[Curve]\r\nangle observed calculated\r\n0 8 7\r\n1 11 9\r\n2 6 8\r\n"
    execute(PROFILE_SOURCE, services, monkeypatch, entry="Profile", inputs=[resource("profile.txt", text)])
    np.testing.assert_array_equal(services.wave("angle").data, [0, 1, 2])
    np.testing.assert_array_equal(services.wave("observed").data, [8, 11, 6])
    np.testing.assert_array_equal(services.wave("calculated").data, [7, 9, 8])
    np.testing.assert_array_equal(services.wave("residual").data, [1, 2, -2])
    graph = services.graph("ProfileGraph")
    assert graph.traces[0].offset == (0, -12)
    assert graph.traces[-1].name == "observed#1" and graph.traces[-1].marker == 8
    assert next(a for a in graph.axes if a.name == "left").style.tick_enable == (0, float("inf"))


def test_reflection_hkl_columns_phase_masks_and_marker_offsets(services, monkeypatch):
    text = "Synthetic header\nh k l phase angle\n1 0 0 1 5\n1 1 0 2 6\n0 0 2 1 9\n"
    execute(REFLECTION_SOURCE, services, monkeypatch, entry="ReflectionMarkers", inputs=[resource("reflections.txt", text)])
    np.testing.assert_array_equal(services.wave("hIndex").data, [1, 1, 0])
    np.testing.assert_array_equal(services.wave("kIndex").data, [0, 1, 0])
    np.testing.assert_array_equal(services.wave("lIndex").data, [0, 0, 2])
    np.testing.assert_array_equal(services.wave("phase1").data, [5, 9])
    np.testing.assert_array_equal(services.wave("phase2").data, [6])
    traces = services.graph("ReflectionsGraph").traces
    assert len(traces) == 3
    assert all(trace.offset == (-.5, -22) and trace.mode == 3 for trace in traces[1:])


def test_parameter_panel_manifests_globals_and_explicit_button_event(services, monkeypatch):
    execute(PANEL_SOURCE, services, monkeypatch, entry="Controls")
    panel = services.environment["igor_parameter_panels"][0]
    assert panel["name"] == "ControlsPanel" and len(panel["fields"]) == 2
    assert panel["buttons"][0]["python_callable"] == "mdsRoutine2"
    assert not services.document.render_source().waves  # registering controls never runs a callback
    execute(PANEL_SOURCE, services, monkeypatch, entry="AnalyzeClick")
    np.testing.assert_allclose(services.wave("result").data, [8/120, 1+8/120, 2+8/120])
    report = analyze_procedures(PANEL_SOURCE)
    with pytest.raises(ValueError, match="eventCode"):
        build_python(report, "AnalyzeClick", [{"eventCode": 3}])


def test_no_preparation_no_host_read_write_and_memory_only_output(services, tmp_path, capsys):
    sentinel = tmp_path / "host.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    api = IgorAPI(services)
    scope = api.scope({"path": str(sentinel)})
    scope.declare("variable", "handle", "0")
    with pytest.raises(PermissionError, match="not prepared"):
        scope.command("Open/R handle as path")
    scope.command("Open handle as path")  # writes a memory buffer, not this host path
    scope.command('FBinWrite handle,"virtual-only"; Close handle')
    api.finish()
    assert "not host files" in capsys.readouterr().out
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert not any("virtual-only" in str(value) for value in services.environment.values())
    with pytest.raises(PermissionError):
        scope.command('LoadWave/G "not-selected.txt"')


def test_resource_body_removed_before_result_save(services, monkeypatch, tmp_path):
    marker = "PRIVATE SYNTHETIC RAW RESOURCE MARKER"
    api = execute("Function Demo()\nMake/N=2 retained\nEnd", services, monkeypatch, entry="Demo", inputs=[resource("input.txt", marker)])
    with pytest.raises(ValueError):
        validate("from mds import igor\nprint(igor._io)")
    assert not hasattr(api.igor, "io")
    path = tmp_path / "result.mdsg"
    save_graph_document(path, services.document)
    with zipfile.ZipFile(path) as archive:
        assert marker not in archive.read("document.json").decode("utf-8")
    assert INPUT_RESOURCES_KEY not in services.document.current.environment


@pytest.mark.parametrize("bad", [None, {}, [{"name": "x", "path": "x"}],
    [resource("same.txt", "a"), resource("same.txt", "b")],
    [resource("path/to.txt", "a")], [resource("bad.txt", "\0")]])
def test_resource_registry_rejects_malformed_or_ambiguous_inputs(bad):
    with pytest.raises(ValueError):
        validate_resources(bad)


def test_resource_and_virtual_memory_budget_is_joint(services, monkeypatch):
    from materials_data_studio.tools import igor_virtual_io
    monkeypatch.setattr(igor_virtual_io, "MAX_RESOURCE_BYTES", 16)
    with pytest.raises(ValueError, match="limit"):
        validate_resources([resource("large.txt", "x" * 17)])
    io = VirtualTextIO([resource("input.txt", "12345678")])
    io.write("output", "12345678")
    with pytest.raises(ValueError, match="limit"):
        io.write("output", "123456789")


def test_prepared_inputs_readonly_and_virtual_mutations_are_atomic(services):
    services.environment[INPUT_RESOURCES_KEY] = [resource("input.txt", "0 1\n1 2\n")]
    api = IgorAPI(services)
    scope = api.scope()
    scope.declare("variable", "handle", "0")
    with pytest.raises(PermissionError, match="read-only"):
        scope.command('Open handle as "input.txt"')
    with pytest.raises(PermissionError, match="input"):
        scope.command('DeleteFile "C:\\\\prepared\\\\input.txt"')
    before = services.document.current
    with pytest.raises(ValueError):
        scope.command('Open handle as "temporary"; FBinWrite handle,"some text"; Make/N=-1 invalid')
    assert services.document.current is before
    with pytest.raises(PermissionError):
        scope.command('LoadWave/G "temporary"')


def test_failed_panel_command_cannot_leak_through_shared_environment(services):
    api = IgorAPI(services)
    before = services.document.current
    with pytest.raises(ValueError):
        api.command('NewPanel/N=Temporary as "Uncommitted controls"; Execute "forbidden"')
    assert services.document.current is before
    assert not services.environment.get("igor_parameter_panels")
    api.command('NewPanel/N=Existing as "Keep existing controls"')
    before = services.document.current
    panels = list(services.environment["igor_parameter_panels"])
    with pytest.raises(ValueError):
        api.command('GroupBox uncommitted,title="Not committed"; Execute "forbidden"')
    assert services.document.current is before
    assert services.environment["igor_parameter_panels"] == panels
    assert not panels[0]["groups"]
    with pytest.raises(AttributeError):
        api.steps = 0
    with pytest.raises(ValueError):
        validate("from mds import igor\nigor._steps=0")


def test_strict_inclusive_wave_loop_warns_without_changing_source(services, monkeypatch):
    source = "#pragma rtGlobals=3\nFunction Legacy()\nMake/D/N=3 values\nVariable n=numpnts(values),i\nfor(i=0;i<=n;i+=1)\nPrint values[i]\nendfor\nEnd"
    report = analyze_procedures(source)
    assert report.source == source
    assert any(d.severity == "warning" and "one-past-end" in d.message for d in report.routines[0].diagnostics)
    # Direct numpnts bounds are diagnosed, and runtime always rejects the invalid index.
    direct = analyze_procedures(source.replace("i<=n", "i<=numpnts(values)"))
    assert any(d.severity == "warning" and "one-past-end" in d.message for d in direct.routines[0].diagnostics)
    with pytest.raises(ValueError, match="IndexError"):
        execute(source, services, monkeypatch, entry="Legacy")
    corrected = source.replace("i<=n", "i<n")
    execute(corrected, DocumentServices(empty_document()), monkeypatch, entry="Legacy")


def test_dynamic_function_names_and_unsupported_virtual_expression_fail_closed(services):
    api = IgorAPI(services)
    scope = api.scope()
    scope.command("Make/N=3 first")
    scope.command('Rename $StringFromList(0,"first;"),renamed')
    assert services.wave("renamed").shape == (3,)
    report = analyze_procedures('Function Bad()\nLoadWave/G UnsupportedFunction()\nEnd')
    assert not report.routines[0].supported
    with pytest.raises(ValueError):
        validate("from mds import igor\nprint(igor.scope()._engine)")


def test_registry_is_metadata_not_implicit_filesystem_discovery():
    io = VirtualTextIO([resource("only.txt", "0 1\n")])
    io.paths.add("prepared")
    assert io.indexed_file("prepared", -1, ".txt") == "only.txt;"
    assert io.indexed_file("prepared", 1, ".txt") == ""
    assert io.indexed_file("prepared", -1, ".csv") == ""
    with pytest.raises(ValueError):
        io.indexed_file("unknown", 0, ".txt")


def test_string_function_default_return_retains_igor_type(services, monkeypatch):
    source = 'Function/S EmptyText()\nEnd\nFunction Demo()\nString text=EmptyText()+"Signal!"\nMake/T/N=1 result\nresult[0]=text\nEnd'
    execute(source, services, monkeypatch, entry="Demo")
    assert services.wave("result").data.tolist() == ["Signal!"]


@pytest.mark.parametrize("operation", ["FStatus", "FBinRead", "FBinWrite", "FPrintf"])
def test_byte_compatibility_rejects_non_ascii_instead_of_wrong_offsets(services, operation):
    services.environment[INPUT_RESOURCES_KEY] = [resource("unicode.txt", "日本語の見出し\n0 1\n1 2\n")]
    scope = IgorAPI(services).scope()
    scope.declare("variable", "handle", "0")
    scope.declare("string", "destination", 'PadString("",32,32)')
    scope.command('Open/R handle as "unicode.txt"' if operation in {"FStatus", "FBinRead"} else 'Open handle as "memory-output"')
    command = ("FStatus handle" if operation == "FStatus" else "FBinRead handle,destination" if operation == "FBinRead" else operation + ' handle,"日本語"')
    before = services.document.current
    with pytest.raises(ValueError, match="requires ASCII"):
        scope.command(command)
    assert services.document.current is before


def test_general_numeric_load_keeps_unicode_header_support(services):
    services.environment[INPUT_RESOURCES_KEY] = [resource("日本語のファイル.txt", "日本語の見出し\n温度,強度\n1,3\n2,4\n")]
    api = IgorAPI(services)
    api.command('LoadWave/G/D/N "日本語のファイル.txt"')
    np.testing.assert_array_equal(services.wave("wave0").data, [1, 2])
    np.testing.assert_array_equal(services.wave("wave1").data, [3, 4])


def test_global_scalar_cannot_shadow_existing_wave(services):
    api = IgorAPI(services)
    api.command("Make/D/N=3 mass")
    before = services.document.current
    scope = api.scope()
    with pytest.raises(ValueError, match="cannot shadow"):
        scope.declare("variable", "mass", "2", global_=True)
    with pytest.raises(ValueError, match="cannot shadow"):
        scope.set_global("MASS", "text")
    assert services.document.current is before
    assert not services.environment.get("variables") and not services.environment.get("strings")
    np.testing.assert_array_equal(services.wave("mass").data, [0, 0, 0])
