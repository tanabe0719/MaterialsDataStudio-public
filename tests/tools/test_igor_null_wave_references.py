"""Absent WAVE references stay null; binding never fabricates scientific data."""
from __future__ import annotations

import sys
from typing import Any

import numpy as np
import pytest

from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_waves import empty_document, make_wave
from materials_data_studio.tools.igor_procedure_conversion import analyze_procedures, build_python
from materials_data_studio.tools.igor_procedure_runtime import IgorAPI
from materials_data_studio.tools.procedure_api import install_api


@pytest.fixture
def services() -> DocumentServices:
    return DocumentServices(empty_document())


@pytest.mark.parametrize("rt_globals", [1, 2, 3])
def test_missing_nonparameter_wave_binds_null_with_visible_warning(
    services: DocumentServices, capsys: Any, rt_globals: int,
) -> None:
    scope = IgorAPI(services).scope(rt_globals=rt_globals)
    before = services.document.current
    scope.alias("missingRef", "unavailable")
    warning = capsys.readouterr().out
    assert "Warning: WAVE reference is null" in warning
    assert "missingRef" in warning
    assert scope.evaluate("WaveExists(missingRef)") == 0
    assert scope.evaluate('WaveExists("MISSINGREF")') == 0
    assert services.document.current is before
    assert not services.document.render_source().waves


def test_wave_z_missing_reference_is_quiet_but_still_null(
    services: DocumentServices, capsys: Any,
) -> None:
    scope = IgorAPI(services).scope()
    scope.alias("optionalRef", "unavailable", optional=True)
    assert capsys.readouterr().out == ""
    assert scope.evaluate("WaveExists(optionalRef)") == 0
    assert not services.document.render_source().waves


@pytest.mark.parametrize("expression", [
    "missingRef", "missingRef[0]", "numpnts(missingRef)", "missingRef(0)",
])
def test_actual_evaluation_of_null_reference_fails_without_data_changes(
    services: DocumentServices, expression: str,
) -> None:
    scope = IgorAPI(services).scope()
    scope.alias("missingRef", "unavailable", optional=True)
    before = services.document.current
    with pytest.raises(ValueError):
        scope.evaluate(expression)
    assert services.document.current is before
    assert not services.document.render_source().waves
    assert scope.evaluate("WaveExists(missingRef)") == 0


@pytest.mark.parametrize("target,operator", [
    ("missingRef", "="), ("missingRef[0]", "="), ("missingRef", "+="),
])
def test_assignment_through_null_reference_cannot_create_scalar_or_wave(
    services: DocumentServices, target: str, operator: str,
) -> None:
    scope = IgorAPI(services).scope()
    scope.alias("missingRef", "unavailable", optional=True)
    before = services.document.current
    with pytest.raises(ValueError):
        scope.assign(target, "5", operator)
    assert services.document.current is before
    assert not services.document.render_source().waves
    assert not scope.variables
    assert not services.environment.get("variables")
    assert not services.environment.get("strings")


def test_display_through_null_reference_rolls_back_graph_and_context(
    services: DocumentServices,
) -> None:
    scope = IgorAPI(services).scope()
    scope.alias("missingRef", "unavailable", optional=True)
    before = services.document.current
    context = dict(services.context)
    with pytest.raises(ValueError):
        scope.command("Display/N=MissingGraph missingRef")
    assert services.document.current is before
    assert services.context == context
    assert not services.document.current.graphs
    assert not services.document.render_source().waves
    assert scope.evaluate("WaveExists(missingRef)") == 0


def test_null_reference_does_not_resolve_later_created_target_until_rebound(
    services: DocumentServices,
) -> None:
    scope = IgorAPI(services).scope()
    scope.alias("missingRef", "unavailable", optional=True)
    wave = make_wave("unavailable", [2., 4.])
    services.write([wave])
    assert scope.evaluate("WaveExists(unavailable)") == 1
    assert scope.evaluate("WaveExists(missingRef)") == 0
    with pytest.raises(ValueError):
        scope.evaluate("missingRef[0]")
    with pytest.raises(ValueError):
        scope.assign("missingRef[0]", "99")
    with pytest.raises(ValueError):
        scope.command("Display/N=MissingGraph missingRef")
    assert services.wave("unavailable") is wave
    np.testing.assert_array_equal(wave.data, [2., 4.])

    scope.alias("missingRef", "unavailable")
    assert scope.evaluate("WaveExists(missingRef)") == 1
    np.testing.assert_array_equal(scope.evaluate("missingRef"), [2., 4.])
    scope.command("Display/N=ReboundGraph missingRef")
    assert services.document.graph("ReboundGraph").traces[0].y_wave == "root:unavailable"


def test_copying_null_reference_does_not_bind_later_created_target(
    services: DocumentServices,
) -> None:
    scope = IgorAPI(services).scope()
    scope.alias("nullRef", "unavailable", optional=True)
    wave = make_wave("unavailable", [1., 2.])
    services.write([wave])
    scope.alias("copiedRef", "nullRef", optional=True)
    assert scope.evaluate("WaveExists(nullRef)") == 0
    assert scope.evaluate("WaveExists(copiedRef)") == 0
    with pytest.raises(ValueError):
        scope.evaluate("copiedRef[0]")
    assert services.wave("unavailable") is wave
    scope.alias("copiedRef", "unavailable")
    assert scope.evaluate("WaveExists(copiedRef)") == 1
    assert scope.evaluate("WaveExists(nullRef)") == 0


def test_self_copy_of_null_reference_does_not_resurrect_it(
    services: DocumentServices,
) -> None:
    scope = IgorAPI(services).scope()
    scope.alias("nullRef", "unavailable", optional=True)
    wave = make_wave("unavailable", [1., 2.])
    services.write([wave])
    scope.alias("nullRef", "nullRef", optional=True)
    assert scope.evaluate("WaveExists(nullRef)") == 0
    with pytest.raises(ValueError):
        scope.assign("nullRef[0]", "99")
    assert services.wave("unavailable") is wave
    scope.alias("nullRef", "unavailable")
    assert scope.evaluate("WaveExists(nullRef)") == 1


@pytest.mark.parametrize("optional", [False, True])
def test_missing_required_wave_parameter_remains_fatal(
    services: DocumentServices, capsys: Any, optional: bool,
) -> None:
    scope = IgorAPI(services).scope({"inputWave": "unavailable"})
    with pytest.raises(ValueError):
        scope.alias("inputWave", "inputWave", parameter=True, optional=optional)
    assert not services.document.render_source().waves
    assert not scope.aliases and not scope.null_aliases
    assert capsys.readouterr().out == ""


def test_present_required_wave_parameter_preserves_real_reference(
    services: DocumentServices,
) -> None:
    services.write([make_wave("available", [2., 4.])])
    scope = IgorAPI(services).scope({"inputWave": "available"})
    scope.alias("inputWave", "inputWave", parameter=True)
    assert scope.evaluate("WaveExists(inputWave)") == 1
    scope.assign("inputWave", "2", "*=")
    np.testing.assert_array_equal(services.wave("available").data, [4., 8.])
    assert len(services.document.render_source().waves) == 1


def test_null_reference_cannot_be_passed_as_required_wave_argument(
    services: DocumentServices,
) -> None:
    calls: list[str] = []

    def accept_wave(path: str) -> int:
        calls.append(path)
        return 7

    scope = IgorAPI(services).scope(functions={"acceptWave": accept_wave},
                                    wave_parameters={"acceptwave": (0,)})
    scope.alias("nullRef", "unavailable", optional=True)
    services.write([make_wave("unavailable", [1., 2.])])
    with pytest.raises(ValueError):
        scope.evaluate("acceptWave(nullRef)")
    assert not calls
    scope.alias("nullRef", "unavailable")
    assert scope.evaluate("acceptWave(nullRef)") == 7
    assert calls == ["root:unavailable"]


def test_dynamic_wave_reference_rebind_uses_current_name(
    services: DocumentServices,
) -> None:
    scope = IgorAPI(services).scope()
    scope.declare("string", "targetName", '"unavailable"')
    scope.alias("dynamicRef", "$targetName", optional=True)
    assert scope.evaluate("WaveExists(dynamicRef)") == 0
    services.write([make_wave("available", [3., 6.])])
    scope.assign("targetName", '"available"')
    scope.alias("dynamicRef", "$targetName")
    assert scope.evaluate("WaveExists(dynamicRef)") == 1
    np.testing.assert_array_equal(scope.evaluate("dynamicRef"), [3., 6.])


def test_command_failure_restores_null_reference_membership_and_aliases(
    services: DocumentServices,
) -> None:
    services.write([make_wave("available", [1., 2.])])
    scope = IgorAPI(services).scope()
    scope.alias("boundRef", "available")
    scope.alias("nullRef", "unavailable", optional=True)
    aliases = dict(scope.aliases)
    nulls = set(scope.null_aliases)
    before = services.document.current

    def change_bindings() -> int:
        scope.alias("boundRef", "anotherUnavailable", optional=True)
        scope.alias("nullRef", "available")
        return 3

    scope.functions["changebindings"] = change_bindings
    with pytest.raises(ValueError):
        scope.command("Make/N=(changeBindings()) staged; UnsupportedOperation")
    assert services.document.current is before
    assert scope.aliases == aliases
    assert scope.null_aliases == nulls
    assert scope.evaluate("WaveExists(boundRef)") == 1
    assert scope.evaluate("WaveExists(nullRef)") == 0
    assert [wave.igor_path for wave in services.document.render_source().waves] == ["root:available"]


def test_converted_missing_wave_declarations_continue_until_reference_use(
    services: DocumentServices, monkeypatch: Any, capsys: Any,
) -> None:
    source = '''Function NullBindings()
    Wave missingRef=unavailable
    Wave/Z optionalRef=alsoUnavailable
    Make/D/N=1 existence
    existence[0]=WaveExists(missingRef)+WaveExists(optionalRef)
End'''
    report = analyze_procedures(source)
    assert report.routines[0].supported, report.routines[0].diagnostics
    code = build_python(report, "NullBindings", [])
    monkeypatch.setitem(sys.modules, "mds", None)
    install_api(services)
    exec(compile(code, "Synthetic null wave bindings", "exec"), {})
    assert capsys.readouterr().out.count("Warning: WAVE reference is null") == 1
    np.testing.assert_array_equal(services.wave("existence").data, [0.])
    assert len(services.document.render_source().waves) == 1
