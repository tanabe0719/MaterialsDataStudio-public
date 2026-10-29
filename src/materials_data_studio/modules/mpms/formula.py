"""Chemical-formula parsing and molar-mass calculation."""

from __future__ import annotations

import re

from materials_data_studio.modules.mpms.atomic_weights import ATOMIC_WEIGHTS

_NUMBER = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)")


def _read_number(formula: str, index: int) -> tuple[float, int]:
    """Read an optional decimal coefficient."""
    match = _NUMBER.match(formula, index)
    if match is None:
        return 1.0, index
    value = float(match.group())
    if value <= 0:
        raise ValueError("Formula coefficients must be positive.")
    return value, match.end()


def _merge(target: dict[str, float], source: dict[str, float], factor: float = 1.0) -> None:
    for element, amount in source.items():
        target[element] = target.get(element, 0.0) + amount * factor


def _parse_group(
    formula: str, index: int = 0, *, nested: bool = False
) -> tuple[dict[str, float], int]:
    counts: dict[str, float] = {}
    while index < len(formula):
        char = formula[index]
        if char == "(":
            sub_counts, index = _parse_group(formula, index + 1, nested=True)
            factor, index = _read_number(formula, index)
            _merge(counts, sub_counts, factor)
        elif char == ")":
            if not nested:
                raise ValueError("Formula contains unmatched parentheses.")
            return counts, index + 1
        elif char.isupper():
            element = char
            index += 1
            if index < len(formula) and formula[index].islower():
                element += formula[index]
                index += 1
            if element not in ATOMIC_WEIGHTS:
                raise ValueError(f"Unknown element symbol: {element}")
            amount, index = _read_number(formula, index)
            counts[element] = counts.get(element, 0.0) + amount
        else:
            raise ValueError(f"Invalid formula syntax near '{formula[index:]}'.")
    if nested:
        raise ValueError("Formula contains unmatched parentheses.")
    return counts, index


def parse_formula(formula: str) -> dict[str, float]:
    """Parse elements, decimals, nested groups, and dot-separated adducts."""
    cleaned = formula.replace(" ", "")
    if not cleaned:
        raise ValueError("Formula is empty.")
    total: dict[str, float] = {}
    for component in re.split(r"[·•]", cleaned):
        if not component:
            raise ValueError("Formula contains an empty dot-separated component.")
        factor, start = _read_number(component, 0)
        if start == 0:
            factor = 1.0
        counts, index = _parse_group(component, start)
        if index != len(component):
            raise ValueError(f"Invalid formula syntax near '{component[index:]}'.")
        _merge(total, counts, factor)
    return total


def calculate_molar_mass(formula: str) -> float:
    """Calculate molar mass in grams per mole."""
    counts = parse_formula(formula)
    return float(sum(ATOMIC_WEIGHTS[element] * amount for element, amount in counts.items()))


calculate_formula_mass = calculate_molar_mass

