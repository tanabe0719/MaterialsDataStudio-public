"""Prepared score-only evaluation for repeated lattice-scale objectives."""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import linear_sum_assignment


class PreparedPeakScore:
    """Keep the public matcher's mathematics, boundaries and grouping semantics."""

    def __init__(self, peaks, settings):
        settings.validate()
        if not all(math.isfinite(float(p["position"])) and math.isfinite(float(p["intensity"])) for p in peaks):
            raise ValueError("Observed peaks must be finite.")
        observed = [p for p in peaks if p.get("included", True) and settings.lower <= float(p["position"]) <= settings.upper]
        if not observed or len(observed) > 500:
            raise ValueError("Select 1–500 observed peaks within the calculation range.")
        self.settings = settings
        self.x = np.array([p["position"] for p in observed], dtype=float)
        self.intensity = np.maximum([p["intensity"] for p in observed], 0.)
        self.valid_intensity = bool(np.max(self.intensity) > 0)
        if self.valid_intensity:
            self.intensity /= np.max(self.intensity)

    def score(self, positions: np.ndarray, intensities: np.ndarray) -> float:
        """Inputs are generated from a reference validated once before scale search."""
        if len(positions) != len(intensities) or len(positions) > 20000:
            raise ValueError("Invalid or excessive calculated reflections.")
        s = self.settings
        shifted = positions + s.zero_shift
        mask = (shifted >= s.lower) & (shifted <= s.upper) & (intensities > 0)
        shifted, intensities = shifted[mask], intensities[mask]
        # Match sorted((position, intensity, original_index)), including equal positions.
        if len(shifted) > 1 and not np.all(shifted[1:] > shifted[:-1]):
            order = np.lexsort((intensities, shifted))
            shifted, intensities = shifted[order], intensities[order]
        if not len(shifted):
            return 0.
        # Isolated reflections need no Python grouping loop. Grouped/duplicate
        # cases retain the original sequential weighted-sum arithmetic exactly.
        if len(shifted) == 1 or np.all(np.diff(shifted) > s.resolution):
            centers, group_intensities = shifted, intensities.copy()
        else:
            groups = []
            for p, intensity in zip(shifted.tolist(), intensities.tolist(), strict=True):
                if groups and p - groups[-1][2] <= s.resolution:
                    group = groups[-1]
                    total = group[1] + intensity
                    group[0] = (group[0] * group[1] + p * intensity) / total
                    group[1] = total
                else:
                    groups.append([p, intensity, p])
            if not groups:
                return 0.
            if len(groups) > 4000:
                raise ValueError("Too many resolved reflections; narrow the angular range.")
            centers = np.array([g[0] for g in groups])
            group_intensities = np.array([g[1] for g in groups])
        if len(centers) > 4000:
            raise ValueError("Too many resolved reflections; narrow the angular range.")
        if not self.valid_intensity:
            raise ValueError("Observed intensities must be finite with a positive maximum.")
        oi = self.intensity
        pi = group_intensities
        pi /= np.max(pi)
        delta = self.x[:, None] - centers[None, :]
        valid = np.abs(delta) <= s.tolerance
        agreement = np.exp(-0.5 * (delta / s.tolerance) ** 2)
        weights = np.sqrt(oi[:, None] * pi[None, :]) * agreement * valid
        rows, cols = linear_sum_assignment(weights, maximize=True)
        pairs = [(int(r), int(c)) for r, c in zip(rows, cols, strict=True) if valid[r, c] and weights[r, c] > 0]
        matched_o, matched_p = {r for r, _ in pairs}, {c for _, c in pairs}
        pair_weights = [math.sqrt(oi[r] * pi[c]) for r, c in pairs]
        position = float(np.average([agreement[r, c] for r, c in pairs], weights=pair_weights)) if pairs else 0.
        coverage = float(sum(oi[r] for r in matched_o) / np.sum(oi))
        predicted_coverage = float(sum(pi[c] for c in matched_p) / np.sum(pi))
        a, b = np.array([oi[r] for r, _ in pairs]), np.array([pi[c] for _, c in pairs])
        cosine = float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b))) if pairs else 0.
        intensity = cosine * math.sqrt(coverage * predicted_coverage)
        return float(100 * (0.35 * position * predicted_coverage + 0.30 * coverage + 0.20 * predicted_coverage + 0.15 * intensity))
