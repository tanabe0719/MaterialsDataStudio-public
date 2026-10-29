"""Synthetic dialog semantics; private research macros are never embedded here."""
from __future__ import annotations

import sys

import numpy as np
import pytest

from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_waves import empty_document
from materials_data_studio.tools.igor_procedure_conversion import analyze_procedures, build_python
from materials_data_studio.tools.igor_procedure_runtime import IgorAPI
from materials_data_studio.tools.procedure_api import install_api


@pytest.fixture
def api():
    return IgorAPI(DocumentServices(empty_document()))


def test_simple_numeric_text_and_popup_prompt_preserves_igor_values(api):
    scope = api.scope()
    for kind, name, source in [("variable", "amount", "2"), ("string", "label", '"old"'),
                                ("variable", "mode", "1"), ("string", "kind", '"A"')]:
        scope.declare(kind, name, source)
    scope.command('Prompt amount,"Amount"; Prompt label,"Caption"; Prompt mode,"Mode",popup,"A;B;"; Prompt kind,"Kind",popup,"A;B;"')
    seen = []
    def answer(kind, payload):
        seen.append((kind, payload))
        return {"status": "ok", "values": {"amount": 4.5, "label": "New caption", "mode": 1, "kind": 1}}
    api._interaction = answer
    scope.command('DoPrompt/HELP="Synthetic help" "Parameters",amount,label,mode,kind')
    assert seen[0][0] == "prompt" and seen[0][1]["fields"][2]["value"] == 0
    assert scope.evaluate("amount") == 4.5 and scope.evaluate("label") == "New caption"
    assert scope.evaluate("mode") == 2 and scope.evaluate("kind") == "B"
    assert scope.evaluate("V_flag") == 0


def test_prompt_cancel_changes_no_input_value(api):
    scope = api.scope()
    scope.declare("variable", "value", "2")
    scope.command('Prompt value,"Value"')
    api._interaction = lambda *_: {"status": "cancelled"}
    scope.command('DoPrompt "Parameters",value')
    assert scope.evaluate("value") == 2 and scope.evaluate("V_flag") == 1


@pytest.mark.parametrize("kind,index,expected", [(0, 0, 1), (1, 1, 2), (2, 2, 3)])
def test_alert_buttons_return_igor_flag(api, kind, index, expected):
    api._interaction = lambda *_: {"status": "ok", "button": index}
    scope = api.scope()
    scope.command(f'DoAlert/T="Title" {kind},"Message"')
    assert scope.evaluate("V_flag") == expected


def test_cancelled_file_selection_preserves_reference_and_sets_empty_filename(api):
    scope = api.scope()
    scope.declare("variable", "handle", "123")
    api._interaction = lambda *_: {"status": "cancelled"}
    scope.command('Open/D/R/M="Choose input" handle')
    assert scope.evaluate("handle") == 123 and scope.evaluate("S_fileName") == ""


def test_interactive_folder_scopes_only_explicitly_selected_files(api):
    first = {"name": "first.txt", "path": "C:/generated/first.txt", "text": "1 2\n"}
    second = {"name": "second.txt", "path": "C:/generated/second.txt", "text": "3 4\n"}
    api._io.accept_resources([first])
    api._interaction = lambda *_: {"status": "ok", "resources": [second], "selected": "C:/generated"}
    scope = api.scope()
    scope.command('NewPath/M="Pick folder" chosen')
    assert scope.evaluate('IndexedFile(chosen,-1,".txt")') == "second.txt;"
    assert api._io.text(first["path"]) == "1 2\n"  # Existing open inputs remain readable.


def test_generated_prompt_code_requires_explicit_run(api, monkeypatch):
    source = 'Function DialogDemo()\nVariable level=2\nPrompt level,"Level"\nDoPrompt "Parameters",level\nMake/D/N=2 output\noutput=level\nDoAlert 0,"Done"\nEnd'
    report = analyze_procedures(source)
    assert report.routines[0].supported and not report.routines[0].diagnostics
    generated = build_python(report, "DialogDemo", [])
    services = api.services
    mds = install_api(services)
    monkeypatch.setitem(sys.modules, "mds", mds)
    mds.igor._interaction = lambda kind, _payload: ({"status": "ok", "values": {"level": 7}}
        if kind == "prompt" else {"status": "ok", "button": 0})
    assert not services.document.render_source().waves
    exec(generated, {})
    np.testing.assert_array_equal(services.wave("output").data, [7, 7])


@pytest.mark.parametrize("mode,expected", [(1, [9., 0., 7.]), (2, [9., 0., 7.])])
def test_legacy_clamps_both_negative_and_past_end_wave_indices(api, mode, expected):
    scope = api.scope(rt_globals=mode)
    scope.command("Make/D/N=3 values")
    scope.assign("values[-1]", "9")
    scope.assign("values[3]", "7")
    assert scope.evaluate("values[-10]") == 9 and scope.evaluate("values[20]") == 7
    np.testing.assert_array_equal(api.services.wave("values").data, expected)


def test_old_handwritten_scopes_remain_strict(api):
    scope = api.scope()
    scope.command("Make/D/N=3 values")
    with pytest.raises(IndexError):
        scope.assign("values[-1]", "9")
    with pytest.raises(IndexError):
        scope.evaluate("values[-1]")


def test_failed_command_batch_rolls_back_prompt_definitions(api):
    scope = api.scope()
    scope.declare("variable", "level", "2")
    with pytest.raises(ValueError):
        scope.command('Prompt level,"Late dialog"; Execute "forbidden"')
    assert not scope.prompts


@pytest.mark.parametrize("suffix", ["", ' as ""'])
def test_read_without_filename_opens_dialog_and_cancel_keeps_reference(api, suffix):
    scope = api.scope()
    scope.declare("variable", "handle", "22")
    api._interaction = lambda *_: {"status": "cancelled"}
    scope.command('Open/R/M="Choose a file" handle' + suffix)
    assert scope.evaluate("handle") == 22 and scope.evaluate("S_fileName") == ""


def test_prompt_normalizes_numpy_number_before_strict_json_transport(api):
    scope = api.scope({"level": np.int64(2)})
    scope.command('Prompt level,"Level"')
    values = []
    def cancelled(_kind, payload):
        values.append(payload["fields"][0]["value"])
        return {"status": "cancelled"}
    api._interaction = cancelled
    scope.command('DoPrompt "Input",level')
    assert type(values[0]) is float and values[0] == 2
