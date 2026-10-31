"""Resolve reflection labels from structure snapshots only when requested."""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import numpy as np


def index_text(value) -> str:
    parts = re.findall(r"\(([^()]*)\)", str(value)) or [str(value)]
    return " / ".join(dict.fromkeys("".join(re.findall(r"-?\d+", part)) for part in parts))


def assigned_indices(result) -> list[str]:
    hkls = result["pattern"].get("hkl", [])
    intensities = result["pattern"]["intensities"]
    return [" / ".join(dict.fromkeys(index_text(hkls[i]) for i in sorted(result["match"]["groups"][a["predicted"]]["members"], key=lambda i: intensities[i], reverse=True) if i < len(hkls)))
            for a in result["match"]["assignments"]]


def display_indices(value: str, show_all: bool = False) -> str:
    """Input order records descending calculated reflection intensity."""
    indices = list(dict.fromkeys(part.strip() for part in value.split("/") if part.strip()))
    return " / ".join(indices if show_all else indices[:1])


def resolve_indices(reference, positions, service, stop) -> list[str]:
    from materials_data_studio.modules.xrd.cif_backend import (
        CifDocument,
        extract_cif_tables,
        update_cif_tables,
    )
    from materials_data_studio.modules.xrd.mp_database import (
        MaterialsProjectBackend,
        MPDatabaseManager,
    )
    from materials_data_studio.modules.xrd.phase_models import Candidate
    from materials_data_studio.modules.xrd.simulation import simulate_cif
    candidate = Candidate(**reference["candidate"])
    text = reference.get("cif", "")
    if not text:
        if candidate.source == "Materials Project":
            info = MPDatabaseManager(service.store).configuration()
            if not info or info.get("snapshot") != candidate.revision or info.get("content_hash") != reference.get("database", {}).get("content_hash"):
                raise ValueError("Install the original MP index to view this saved structure")
            backend = MaterialsProjectBackend(info, stop)
            try:
                text = backend.cif(candidate.identity)
            finally:
                backend.close()
        elif candidate.source == "Local":
            text = service.store.cif(candidate.content_hash)
        else:
            text = service.cod.cif(candidate, stop)
    if stop.is_set():
        raise InterruptedError("Stopped")
    document = CifDocument(Path("phase.cif"), text, text)
    scale = reference.get("lattice_scale", 1.)
    if scale != 1:
        cell, sites, _ = extract_cif_tables(document)
        update_cif_tables(document, replace(cell, a=cell.a * scale, b=cell.b * scale, c=cell.c * scale), sites)
    settings = reference["settings"]
    pattern = simulate_cif(document, wavelength_a=settings["wavelength"], two_theta_range=(settings["lower"], settings["upper"]), occupancy_preview=False)
    calculated = pattern.two_theta_deg + settings["zero_shift"]
    labels = pattern.hkl_labels or []
    result = []
    for position in positions:
        if stop.is_set():
            raise InterruptedError("Stopped")
        if not len(calculated):
            result.append("")
            continue
        nearest = int(np.argmin(abs(calculated - position)))
        if abs(calculated[nearest] - position) > settings["tolerance"] + settings["resolution"]:
            result.append("")
            continue
        indices = np.flatnonzero(abs(calculated - calculated[nearest]) <= settings["resolution"])
        indices = sorted(indices, key=lambda i: pattern.intensity[i], reverse=True)
        result.append(" / ".join(dict.fromkeys(index_text(labels[i]) for i in indices if i < len(labels))))
    return result
