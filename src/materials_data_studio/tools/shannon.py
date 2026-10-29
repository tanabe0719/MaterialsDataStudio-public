"""Read local-environment Shannon radii from pymatgen's element dataset."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ShannonRadius:
    """One oxidation-state, coordination, and spin-specific radius entry."""

    oxidation_state: int
    coordination: str
    spin: str
    ionic_radius_a: float
    crystal_radius_a: float


def roman_numeral_value(value: str) -> int:
    """Convert pymatgen's Roman-numeral coordination label to a sort value."""
    numerals = {"I": 1, "V": 5, "X": 10}
    total = 0
    previous = 0
    for character in reversed(value.rstrip("SQ")):
        current = numerals.get(character, 0)
        total += -current if current < previous else current
        previous = max(previous, current)
    return total


def shannon_radii_for_element(symbol: str) -> list[ShannonRadius]:
    """Return all Shannon-radius rows for an element, sourced from pymatgen."""
    from pymatgen.core import Element

    raw: Any = Element(symbol).data.get("Shannon radii", {})
    rows: list[ShannonRadius] = []
    if not isinstance(raw, dict):
        return rows
    for oxidation_text, coordinations in raw.items():
        if not isinstance(coordinations, dict):
            continue
        for coordination, spin_states in coordinations.items():
            if not isinstance(spin_states, dict):
                continue
            for spin, radii in spin_states.items():
                if not isinstance(radii, dict):
                    continue
                ionic = radii.get("ionic_radius")
                crystal = radii.get("crystal_radius")
                if not isinstance(ionic, (int, float)) or not isinstance(
                    crystal, (int, float)
                ):
                    continue
                rows.append(
                    ShannonRadius(
                        oxidation_state=int(oxidation_text),
                        coordination=str(coordination),
                        spin=str(spin) or "—",
                        ionic_radius_a=float(ionic),
                        crystal_radius_a=float(crystal),
                    )
                )
    return sorted(
        rows,
        key=lambda row: (
            row.oxidation_state,
            roman_numeral_value(row.coordination),
            row.coordination,
            row.spin,
        ),
    )
