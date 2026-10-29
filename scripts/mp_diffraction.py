"""Bounded batch evaluation of pymatgen's standard powder XRD model.

Uses pymatgen's reciprocal-lattice API, scattering coefficients, tolerances and
hkl-family convention. No symmetry refinement or alternative scattering model.
The developer builder compares this path against XRDCalculator before deployment.
"""
from __future__ import annotations

import math

import numpy as np

ENGINE = "mds-mp-batch-xrd-1"


def batch_pattern(structure, wavelength=1.540598, bounds=(1, 150)):
    from pymatgen.analysis.diffraction.core import AbstractDiffractionPatternCalculator as Model
    from pymatgen.analysis.diffraction.core import DiffractionPattern, get_unique_families
    from pymatgen.analysis.diffraction.xrd import ATOMIC_SCATTERING_PARAMS
    minimum, maximum = [2 * math.sin(math.radians(t / 2)) / wavelength for t in bounds]
    lattice = structure.lattice.reciprocal_lattice_crystallographic
    estimated = structure.volume * 4 * math.pi * maximum**3 / 3
    if estimated > 1_000_000:
        raise ValueError("Reciprocal-space calculation exceeds the one-million-vector resource limit")
    points = lattice.get_points_in_sphere([[0, 0, 0]], [0, 0, 0], maximum)
    points = [p for p in points if p[1] >= minimum]
    if not points:
        raise ValueError("No reciprocal points in the calculation range")
    hkls = np.rint(np.array([p[0] for p in points])).astype(int)
    lengths = np.array([p[1] for p in points])
    order = np.lexsort((-hkls[:, 2], -hkls[:, 1], -hkls[:, 0], lengths))
    hkls, lengths = hkls[order], lengths[order]
    theta = np.arcsin(np.clip(wavelength * lengths / 2, 0, 1))
    positions = np.rad2deg(2 * theta)
    coords, occupancies, species_indices, symbols, numbers = [], [], [], [], []
    for site in structure:
        for species, occupancy in site.species.items():
            if species.symbol not in symbols:
                symbols.append(species.symbol)
                numbers.append(species.Z)
            species_indices.append(symbols.index(species.symbol))
            coords.append(site.frac_coords)
            occupancies.append(occupancy)
    coefficients = np.array([ATOMIC_SCATTERING_PARAMS[s] for s in symbols])
    coordinates = np.array(coords)
    weights = np.array(occupancies)
    indices = np.array(species_indices)
    zs = np.array(numbers)
    intensities = np.zeros(len(hkls))
    batch = max(8, min(256, 1_000_000 // max(1, len(coords))))
    for start in range(0, len(hkls), batch):
        end = min(len(hkls), start + batch)
        s2 = (lengths[start:end] / 2)**2
        scattering = zs[None, :] - 41.78214 * s2[:, None] * np.sum(
            coefficients[None, :, :, 0] * np.exp(-coefficients[None, :, :, 1] * s2[:, None, None]), axis=2)
        # Broadcasting three coordinates avoids many tiny multithreaded BLAS jobs.
        phase = sum(hkls[start:end, i, None] * coordinates[None, :, i] for i in range(3))
        factors = np.sum(np.exp(2j * np.pi * phase) * weights[None, :] * scattering[:, indices], axis=1)
        angles = theta[start:end]
        lorentz = (1 + np.cos(2 * angles)**2) / (np.sin(angles)**2 * np.cos(angles))
        intensities[start:end] = np.abs(factors)**2 * lorentz
    groups, start = [], 0
    for i in range(1, len(positions) + 1):
        if i == len(positions) or positions[i] - positions[start] >= Model.TWO_THETA_TOL:
            groups.append((start, i, float(np.sum(intensities[start:i]))))
            start = i
    maximum_intensity = max(g[2] for g in groups)
    if maximum_intensity <= 0 or not np.all(np.isfinite(intensities)):
        raise ValueError("Invalid calculated diffraction intensities")
    x, y, ds, families = [], [], [], []
    hexagonal = structure.lattice.is_hexagonal()
    for first, last, intensity in groups:
        if intensity / maximum_intensity * 100 <= Model.SCALED_INTENSITY_TOL:
            continue
        group_hkls = [tuple(int(v) for v in h) for h in hkls[first:last]]
        if hexagonal:
            group_hkls = [(h, k, -h-k, ell) for h, k, ell in group_hkls]
        family = get_unique_families(group_hkls)
        families.append([{"hkl": key, "multiplicity": count} for key, count in family.items()])
        x.append(float(positions[first]))
        y.append(intensity / maximum_intensity * 100)
        ds.append(float(1 / lengths[first]))
    return DiffractionPattern(x, y, families, ds)
