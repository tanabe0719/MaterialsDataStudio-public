"""Synthetic, operation-count regressions for large converted procedures."""
from __future__ import annotations

import ast
from dataclasses import replace

import numpy as np
import pytest

from materials_data_studio.tools import igor_procedure_runtime as runtime
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_waves import empty_document, make_wave
from materials_data_studio.tools.pxp_model import PxpWave


@pytest.fixture
def scope():
    runtime._cached_expression.cache_clear()
    runtime._cached_validation.cache_clear()
    return runtime.IgorAPI(DocumentServices(empty_document())).scope()


def test_cached_syntax_reads_live_variables_aliases_and_functions(scope, monkeypatch):
    calls = 0
    original = runtime.expression

    def counted(source):
        nonlocal calls
        calls += 1
        return original(source)

    monkeypatch.setattr(runtime, "expression", counted)
    scope.declare("variable", "index", "1")
    assert scope.evaluate("index+1") == 2
    prepared = runtime.prepare_expression("index+1")
    tree = ast.dump(prepared.node, include_attributes=True)
    initial = calls
    for index in range(100):
        scope.variables["index"] = index
        assert scope.evaluate("index+1") == index + 1
    assert calls == initial
    assert ast.dump(prepared.node, include_attributes=True) == tree

    scope.services.write([make_wave("first", [1.]), make_wave("second", [2.])])
    scope.alias("selected", "first")
    assert scope.evaluate("selected[0]") == 1
    scope.alias("selected", "second")
    assert scope.evaluate("selected[0]") == 2

    scope.functions["custom"] = lambda: 3
    assert scope.evaluate("custom()") == 3
    scope.functions["custom"] = lambda: 4
    assert scope.evaluate("custom()") == 4
    del scope.functions["custom"]
    with pytest.raises(ValueError, match="Unsupported function"):
        scope.evaluate("custom()")


def test_cache_does_not_retain_large_literals_or_cross_scope_results(scope):
    literal = '"' + "a" * (runtime._CACHED_EXPRESSION_LENGTH + 1) + '"'
    before = runtime._cached_expression.cache_info()
    validated = runtime._cached_validation.cache_info()
    assert scope.evaluate(literal) == literal[1:-1]
    assert runtime._cached_expression.cache_info() == before
    assert runtime._cached_validation.cache_info() == validated

    other = scope.owner.scope()
    scope.functions["localfunction"] = lambda: 10
    other.functions["localfunction"] = lambda: 20
    assert scope.evaluate("localfunction()") == 10
    assert other.evaluate("localfunction()") == 20


@pytest.mark.parametrize("shape,target,expected", [
    ((100_000,), "data[12345]", 12345 + 2 * 12345),
    ((200, 300), "data[123][234]", 123 + 2 * 123 + 234 + 3 * 234),
])
def test_scalar_point_specials_do_not_allocate_full_coordinate_arrays(scope, monkeypatch, shape, target, expected):
    wave = make_wave("data", np.zeros(shape))
    wave = replace(wave, dimension_scaling=((0., 2.), (0., 3.))[:len(shape)])
    scope.services.write([wave])
    original = wave.data

    def forbidden(*args, **kwargs):
        raise AssertionError("Scalar indexed assignment must not build np.arange")

    monkeypatch.setattr(runtime.np, "arange", forbidden)
    scope.assign(target, "p+x" if len(shape) == 1 else "p+x+q+y")
    result = scope.services.wave("data").data
    assert result[(12345,) if len(shape) == 1 else (123, 234)] == expected
    assert not result.flags.writeable
    assert not np.shares_memory(result, original)
    assert not original.any()


@pytest.mark.parametrize("origin,delta", [(0., 1.), (12.5, .2), (12.5, -.2), (1e15, 4.)])
@pytest.mark.parametrize("special", [False, True])
def test_regular_scalar_coordinate_matches_generic_interpolation(scope, monkeypatch, origin, delta, special):
    values = np.sin(np.arange(20)).astype(np.float32)
    if special:
        values[[2, 5, 9]] = [np.nan, np.inf, -np.inf]
    wave = replace(make_wave("data", values), dimension_scaling=((origin, delta),))
    scope.services.write([wave])
    coordinates = wave.coordinate(0)
    queries = [*coordinates, *(coordinates[:-1] + np.diff(coordinates) * .37),
               min(coordinates) - abs(delta), max(coordinates) + abs(delta), np.nan, np.inf, -np.inf]
    with np.errstate(all="ignore"):
        expected = [runtime._interpolate(query, coordinates, values) for query in queries]

    def forbidden(*args, **kwargs):
        raise AssertionError("Regular scalar interpolation must not build full coordinates")

    monkeypatch.setattr(PxpWave, "coordinate", forbidden)
    for query, result in zip(queries, expected, strict=True):
        with np.errstate(all="ignore"):
            actual = scope.wave_coordinate("data", query)
        np.testing.assert_array_equal(actual, result)
        assert isinstance(actual, np.ndarray) and actual.ndim == 0


@pytest.mark.parametrize("origin,delta", [(0., 0.), (np.inf, 1.), (1e20, 1.)])
def test_exotic_scaling_retains_generic_validation(scope, origin, delta):
    scope.services.write([replace(make_wave("data", [1., 2., 3.]), dimension_scaling=((origin, delta),))])
    with pytest.raises(ValueError, match=r"finite X|strictly monotonic"):
        scope.wave_coordinate("data", 1.)


def test_array_coordinate_queries_keep_generic_behavior(scope, monkeypatch):
    wave = make_wave("data", [1., 3., 6.])
    scope.services.write([wave])
    calls = 0
    original = PxpWave.coordinate

    def counted(self, dimension):
        nonlocal calls
        calls += 1
        return original(self, dimension)

    monkeypatch.setattr(PxpWave, "coordinate", counted)
    np.testing.assert_array_equal(scope.wave_coordinate("data", np.array([-.5, .5, 3.])), [0., 2., 9.])
    assert calls == 1


def test_failed_scalar_assignment_preserves_immutable_wave(scope):
    wave = make_wave("data", np.zeros(100_000))
    scope.services.write([wave])
    with pytest.raises(ValueError, match="change numeric type"):
        scope.assign("data[10]", '"not numeric"')
    assert scope.services.wave("data") is wave
    assert not wave.data.any()
