"""GUI-independent, JSON-safe state for bounded phase search."""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np
from scipy.signal import find_peaks, peak_widths

MAX_CANDIDATES = 200
MAX_CALCULATIONS = 30
MAX_BYTES = 4 * 1024 * 1024


@lru_cache(maxsize=4096)
def _reduced_composition(formula: str):
    from pymatgen.core import Composition
    return Composition(formula).reduced_composition


def elements(text: str) -> tuple[str, ...]:
    """Parse explicit element symbols, rejecting misspellings rather than guessing."""
    import gemmi
    tokens = tuple(dict.fromkeys(re.split(r"[\s,;–−-]+", text.strip()))) if text.strip() else ()
    if any(not re.fullmatch(r"[A-Z][a-z]?", t) or gemmi.Element(t).atomic_number < 1 for t in tokens):
        raise ValueError("Use valid element symbols separated by spaces (e.g. Na Cl O).")
    return tokens


@dataclass
class SearchQuery:
    required: str = ""
    allowed: str = ""
    excluded: str = ""
    formula: str = ""
    cod_ids: str = ""
    space_group: str = ""
    crystal_system: str = ""
    volume_min: float = 0.0
    volume_max: float = 0.0

    def validate(self) -> None:
        required, allowed, excluded = map(set, (elements(self.required), elements(self.allowed), elements(self.excluded)))
        if max(len(required), len(allowed), len(excluded)) > 32:
            raise ValueError("At most 32 elements per constraint are supported.")
        if required & excluded or (allowed and not required <= allowed):
            raise ValueError("Required elements conflict with allowed/excluded elements.")
        if self.cod_ids and (not re.fullmatch(r"\d{7}(?:[ ,;\n]+\d{7})*", self.cod_ids.strip()) or len(re.findall(r"\d{7}", self.cod_ids)) > MAX_CALCULATIONS):
            raise ValueError("Enter at most 30 exact seven-digit COD IDs; wildcards are not supported.")
        if len(self.formula) > 200 or len(self.space_group) > 100:
            raise ValueError("Search text is too long.")
        if not all(math.isfinite(v) and v >= 0 for v in (self.volume_min, self.volume_max)) or (self.volume_max and self.volume_min > self.volume_max):
            raise ValueError("Invalid unit-cell volume bounds.")

    def accepts(self, candidate: Candidate) -> bool:
        if self.cod_ids and candidate.source == "COD" and candidate.identity not in re.findall(r"\d{7}", self.cod_ids):
            return False
        present = set(candidate.elements)
        if not set(elements(self.required)) <= present or set(elements(self.excluded)) & present:
            return False
        allowed = set(elements(self.allowed))
        if allowed and (not present or not present <= allowed):
            return False
        if self.formula:
            try:
                if _reduced_composition(candidate.formula) != _reduced_composition(self.formula):
                    return False
            except (ValueError, TypeError):
                return False
        if self.space_group:
            requested = self.space_group.replace(" ", "").lower()
            if requested not in {candidate.space_group.replace(" ", "").lower(), str(candidate.space_group_number)}:
                return False
        if self.crystal_system and candidate.crystal_system.lower() != self.crystal_system.lower():
            return False
        return not ((self.volume_min and candidate.volume < self.volume_min) or (self.volume_max and (candidate.volume <= 0 or candidate.volume > self.volume_max)))


@dataclass
class CalculationSettings:
    wavelength: float = 1.540598
    lower: float = 5.0
    upper: float = 90.0
    tolerance: float = 0.15
    resolution: float = 0.08
    zero_shift: float = 0.0
    radiation: str = "Cu Kα1"
    alpha2_ratio: float = 0.5

    def validate(self) -> None:
        if not all(math.isfinite(v) for v in (self.wavelength, self.lower, self.upper, self.tolerance, self.resolution, self.zero_shift, self.alpha2_ratio)):
            raise ValueError("Calculation settings must be finite.")
        if not (0.05 <= self.wavelength <= 5 and 0 < self.lower < self.upper < 180):
            raise ValueError("Use wavelength 0.05–5 Å and 0 < lower < upper < 180°.")
        if not (0.001 <= self.tolerance <= 1 and 0 <= self.resolution <= 0.5 and abs(self.zero_shift) <= 0.5 and 0 <= self.alpha2_ratio <= 1):
            raise ValueError("Tolerance 0.001–1°, resolution 0–0.5°, zero shift ±0.5°.")


@dataclass
class Candidate:
    identity: str
    source: str
    formula: str = ""
    elements: list[str] = field(default_factory=list)
    space_group: str = ""
    space_group_number: int = 0
    crystal_system: str = ""
    lattice: list[float] = field(default_factory=list)
    volume: float = 0.0
    title: str = ""
    doi: str = ""
    url: str = ""
    filename: str = ""
    content_hash: str = ""
    retrieved_at: str = ""
    revision: str = ""
    aliases: list[str] = field(default_factory=list)
    site_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_peaks(x: np.ndarray, y: np.ndarray, *, prominence: float = 0.02, limit: int = 500) -> list[dict[str, Any]]:
    """Detect positive local maxima from the caller's current processed intensity."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.ndim != 1 or y.shape != x.shape or x.size < 3 or x.size > 2_000_000:
        raise ValueError("Peak detection requires 3–2,000,000 paired samples.")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or np.any(np.diff(x) <= 0):
        raise ValueError("Peak detection requires finite, increasing X and finite intensity.")
    if not 0 < prominence <= 1:
        raise ValueError("Relative prominence must be in (0, 1].")
    indices, properties = find_peaks(y, prominence=max(float(np.ptp(y)) * prominence, np.finfo(float).eps))
    widths = peak_widths(y, indices)[0] if len(indices) else []
    selected = sorted(np.argsort(properties["prominences"])[-limit:], key=lambda i: int(indices[i]))
    return [{"position": float(x[indices[i]]), "intensity": float(max(properties["prominences"][i], 0)),
             "width": float(widths[i] * np.median(np.diff(x))), "included": True, "origin": "detected"} for i in selected]


def match_peaks(peaks: list[dict[str, Any]], positions: list[float], intensities: list[float], settings: CalculationSettings) -> dict[str, Any]:
    """Resolution grouping followed by bounded maximum-weight one-to-one matching."""
    from scipy.optimize import linear_sum_assignment
    settings.validate()
    if not all(math.isfinite(float(p["position"])) and math.isfinite(float(p["intensity"])) for p in peaks):
        raise ValueError("Observed peaks must be finite.")
    if not all(math.isfinite(float(v)) for v in [*positions, *intensities]):
        raise ValueError("Calculated reflections must be finite.")
    observed = [(i, p) for i, p in enumerate(peaks) if p.get("included", True) and settings.lower <= float(p["position"]) <= settings.upper]
    if not observed or len(observed) > 500:
        raise ValueError("Select 1–500 observed peaks within the calculation range.")
    if len(positions) != len(intensities) or len(positions) > 20000:
        raise ValueError("Invalid or excessive calculated reflections.")
    predicted = sorted((float(p) + settings.zero_shift, float(i), n) for n, (p, i) in enumerate(zip(positions, intensities, strict=True))
                       if settings.lower <= float(p) + settings.zero_shift <= settings.upper and i > 0)
    groups: list[dict[str, Any]] = []
    for p, intensity, n in predicted:
        if groups and p - groups[-1]["start"] <= settings.resolution:
            group = groups[-1]
            total = group["intensity"] + intensity
            group["position"] = (group["position"] * group["intensity"] + p * intensity) / total
            group["intensity"] = total
            group["members"].append(n)
        else:
            groups.append({"position": p, "intensity": intensity, "members": [n], "start": p})
    if not groups:
        return {"score": 0.0, "components": {}, "assignments": [], "unmatched": [i for i, _ in observed], "missing": [], "groups": []}
    if len(groups) > 4000:
        raise ValueError("Too many resolved reflections; narrow the angular range.")
    ox = np.array([p["position"] for _, p in observed], dtype=float)
    oi = np.maximum([p["intensity"] for _, p in observed], 0.0)
    pi = np.array([g["intensity"] for g in groups])
    if not np.all(np.isfinite(oi)) or np.max(oi) <= 0:
        raise ValueError("Observed intensities must be finite with a positive maximum.")
    oi /= np.max(oi)
    pi /= np.max(pi)
    delta = ox[:, None] - np.array([g["position"] for g in groups])[None, :]
    valid = np.abs(delta) <= settings.tolerance
    agreement = np.exp(-0.5 * (delta / settings.tolerance) ** 2)
    weights = np.sqrt(oi[:, None] * pi[None, :]) * agreement * valid
    rows, cols = linear_sum_assignment(weights, maximize=True)
    pairs = [(int(r), int(c)) for r, c in zip(rows, cols, strict=True) if valid[r, c] and weights[r, c] > 0]
    matched_o, matched_p = {r for r, _ in pairs}, {c for _, c in pairs}
    pair_weights = [math.sqrt(oi[r] * pi[c]) for r, c in pairs]
    position = float(np.average([agreement[r, c] for r, c in pairs], weights=pair_weights)) if pairs else 0.0
    coverage = float(sum(oi[r] for r in matched_o) / np.sum(oi))
    predicted_coverage = float(sum(pi[c] for c in matched_p) / np.sum(pi))
    a, b = np.array([oi[r] for r, _ in pairs]), np.array([pi[c] for _, c in pairs])
    cosine = float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b))) if pairs else 0.0
    # Intensity agreement only contributes in proportion to supported reflections.
    intensity = cosine * math.sqrt(coverage * predicted_coverage)
    score = 100 * (0.35 * position * predicted_coverage + 0.30 * coverage + 0.20 * predicted_coverage + 0.15 * intensity)
    return {"score": float(score), "components": {"position": position * predicted_coverage, "observed_coverage": coverage,
            "predicted_coverage": predicted_coverage, "intensity": intensity},
            "assignments": [{"observed": observed[r][0], "predicted": c, "delta": float(delta[r, c])} for r, c in pairs],
            "unmatched": [observed[r][0] for r in range(len(observed)) if r not in matched_o],
            "missing": [c for c in range(len(groups)) if c not in matched_p], "groups": groups}
