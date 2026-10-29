"""GUI-independent search orchestration and reproducible diffraction snapshots."""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from threading import Event
from typing import Any

import numpy as np

from materials_data_studio.core.models import AxisType, Dataset, ModuleType, NormalizationMode
from materials_data_studio.modules.xrd.cif_backend import CifDocument
from materials_data_studio.modules.xrd.models import TofCalibration
from materials_data_studio.modules.xrd.phase_cod import CODProvider
from materials_data_studio.modules.xrd.phase_models import (
    MAX_CANDIDATES,
    CalculationSettings,
    Candidate,
    SearchQuery,
    match_peaks,
)
from materials_data_studio.modules.xrd.phase_store import PhaseStore, cif_metadata, digest
from materials_data_studio.modules.xrd.simulation import simulate_cif
from materials_data_studio.modules.xrd.transforms import convert_x_axis

LOGGER = logging.getLogger(__name__)
CALCULATOR = "pymatgen-" + version("pymatgen") + ":mds-phase-v1"


def experimental_data(dataset: Dataset, wavelength: float, *, subtract_background: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Use existing axis conversion and background result, never display offsets."""
    calibration = dataset.metadata.get("tof_calibration")
    x = convert_x_axis(dataset.array("x"), AxisType(dataset.metadata.get("source_axis", "two_theta")), AxisType.TWO_THETA,
                       wavelength_a=wavelength, tof_calibration=TofCalibration(**calibration) if calibration else None)
    y = np.asarray(dataset.array("intensity"), dtype=float).copy()
    if subtract_background and dataset.metadata.get("background_enabled") and "background" in dataset.processed_data:
        y -= np.asarray(dataset.processed_data["background"], dtype=float)
    if np.all(x[1:] > x[:-1]):
        return x.copy(), y
    order = np.argsort(x)
    return x[order], y[order]


def experimental_signature(dataset: Dataset) -> str:
    """Content-based scientific input identity survives session reloads."""
    import hashlib
    hashed = hashlib.sha256()
    hashed.update(dataset.dataset_id.encode())
    hashed.update(json.dumps({k: dataset.metadata.get(k) for k in ("source_axis", "wavelength_a", "tof_calibration", "background_enabled")}, sort_keys=True).encode())
    for key in ("x", "intensity", "background"):
        if key in dataset.raw_data or key in dataset.processed_data:
            hashed.update(memoryview(np.ascontiguousarray(dataset.array(key), dtype=np.float64)).cast("B"))
    return hashed.hexdigest()


class PhaseSearchService:
    def __init__(self, store: PhaseStore, cod: CODProvider | None = None):
        self.store, self.cod = store, cod or CODProvider(store)

    def search(self, query: SearchQuery, *, local=True, online=False, cached=False, stop: Event | None = None, report=lambda text: None) -> tuple[list[Candidate], list[str]]:
        stop = stop or Event()
        query.validate()
        candidates = self.store.local_search(query) if local else []
        errors: list[str] = []
        if online or cached:
            try:
                candidates += self.cod.search(query, stop, report, cached_only=cached)
            except InterruptedError:
                raise
            except Exception as exc:
                LOGGER.warning("COD search failed: %s", exc)
                errors.append(f"COD: {exc}. Local library remains available.")
        if stop.is_set():
            raise InterruptedError("Search stopped")
        if len(candidates) > MAX_CANDIDATES:
            raise ValueError("More than 200 candidates; narrow the search conditions.")
        return candidates, errors

    def calculate(self, candidates: list[Candidate], peaks: list[dict[str, Any]], settings: CalculationSettings,
                  stop: Event | None = None, report=lambda text: None, *, snapshots: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
        settings.validate()
        if not 1 <= len(candidates) <= MAX_CANDIDATES:
            raise ValueError("Search 1–200 candidates; narrow the search conditions.")
        stop = stop or Event()
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        by_hash: dict[str, dict[str, Any]] = {}
        for index, candidate in enumerate(candidates):
            if stop.is_set():
                raise InterruptedError("Calculation stopped")
            try:
                report(f"Downloading CIF / parsing structures {index + 1}/{len(candidates)}: {candidate.identity[:20]}")
                retained = (snapshots or {}).get(candidate.content_hash)
                if retained is not None:
                    if digest(retained.encode()) != candidate.content_hash:
                        raise ValueError("Saved CIF snapshot does not match its content identity.")
                    text = retained
                else:
                    text = self.cod.cif(candidate, stop) if candidate.source == "COD" else self.store.cif(candidate.content_hash)
                parsed = cif_metadata(text, candidate.identity, candidate.source, candidate.filename)
                # Keep source publication/URL and update structure-derived information.
                for name in ("formula", "elements", "space_group", "space_group_number", "crystal_system", "lattice", "volume", "content_hash", "site_count"):
                    setattr(candidate, name, getattr(parsed, name))
                if candidate.content_hash in by_hash:
                    by_hash[candidate.content_hash]["candidate"]["aliases"].append(candidate.identity)
                    continue
                estimate = candidate.volume * (4 * math.pi / 3) * (2 * math.sin(math.radians(settings.upper / 2)) / settings.wavelength) ** 3
                import gemmi
                group = gemmi.find_spacegroup_by_number(candidate.space_group_number)
                atoms_bound = candidate.site_count * (len(group.operations()) if group else 1)
                if estimate > 500_000 or atoms_bound > 2000 or estimate * atoms_bound > 50_000_000:
                    raise ValueError("Diffraction calculation exceeds the resource bound; narrow range or use a longer wavelength.")
                key = digest(json.dumps({"hash": candidate.content_hash, "wavelength": settings.wavelength,
                    "range": [settings.lower, settings.upper], "radiation": settings.radiation, "calculator": CALCULATOR}, sort_keys=True).encode())
                pattern = self.store.read(f"patterns/{key}.json")
                if pattern is None:
                    report(f"Calculating patterns {index + 1}/{len(candidates)}")
                    simulated = simulate_cif(CifDocument(Path(candidate.filename or "snapshot.cif"), text, text),
                                             wavelength_a=settings.wavelength, two_theta_range=(settings.lower, settings.upper), occupancy_preview=False)
                    pattern = {"positions": simulated.two_theta_deg.tolist(), "intensities": simulated.intensity.tolist(),
                               "d": simulated.d_spacing_a.tolist() if simulated.d_spacing_a is not None else [], "hkl": simulated.hkl_labels or []}
                    self.store.write(f"patterns/{key}.json", pattern)
                if stop.is_set():
                    raise InterruptedError("Calculation stopped")
                report(f"Matching peaks {index + 1}/{len(candidates)}")
                matched = match_peaks(peaks, pattern["positions"], pattern["intensities"], settings)
                result = {"candidate": candidate.to_dict(), "cif": text, "pattern": pattern, "match": matched,
                          "settings": asdict(settings), "calculator": CALCULATOR, "adopted": False}
                if sum(len(json.dumps(r).encode()) for r in results) + len(json.dumps(result).encode()) > 8 * 1024 * 1024:
                    raise ValueError("Saved search result limit (8 MiB) reached; calculate fewer candidates.")
                results.append(result)
                by_hash[candidate.content_hash] = result
            except InterruptedError:
                raise
            except Exception as exc:
                LOGGER.warning("Phase calculation failed for %s: %s", candidate.identity, exc)
                errors.append(f"{candidate.identity}: {exc}")
        report("Ranking candidates")
        results.sort(key=lambda result: result["match"]["score"], reverse=True)
        return results, errors


def overlay_dataset(result: dict[str, Any]) -> Dataset:
    """Create a normal simulated-phase Dataset for existing XRD visibility/export."""
    c, p, s = result["candidate"], result["pattern"], result["settings"]
    positions = np.asarray(p["positions"], dtype=float)
    intensity = np.asarray(p["intensities"], dtype=float)
    if s["radiation"] == "Cu Kα1/2":
        from materials_data_studio.modules.xrd.phase_detection import alpha2_position
        positions = np.concatenate((positions, alpha2_position(positions)))
        intensity = np.concatenate((intensity, intensity * s.get("alpha2_ratio", .5)))
        order = np.argsort(positions)
        positions, intensity = positions[order], intensity[order]
    x, y = stick_segments(positions + s["zero_shift"], intensity)
    dataset = Dataset(f"{c['formula']} ({c['identity'][:18]})", ModuleType.XRD, {"x": x, "intensity": y},
                      source_filename=Path(c.get("filename") or (c["identity"] + ".cif")).name,
                      source_hash=digest(result["cif"].encode()) if result.get("cif") else c["content_hash"], source_bytes=result["cif"].encode() if result.get("cif") else None,
                      metadata={"is_simulated_phase": True, "source_axis": "two_theta", "wavelength_a": s["wavelength"],
                                "normalization": NormalizationMode.MAX_100.value, "scale": 1.0, "offset": 0.0,
                                "edited_cif": result["cif"], "stick_two_theta_deg": p["positions"], "stick_intensity": p["intensities"],
                                "stick_hkl_labels": p["hkl"], "stick_d_spacing_a": p["d"], "space_group_name": c["space_group"],
                                "space_group_number": c["space_group_number"], "crystal_system": c["crystal_system"],
                                "simulation_zero_shift_deg": 0., "fwhm_deg": 0.1, "profile_step_deg": 0.01,
                                "two_theta_range": [s["lower"], s["upper"]], "phase_search_identity": c["identity"]})
    if result.get("database"):
        dataset.metadata["reference_database"] = result["database"]
        dataset.metadata["is_reference_pattern"] = not bool(result.get("cif"))
    dataset.plot_style.color = "#e86828"
    dataset.plot_style.marker = "none"
    return dataset


def stick_segments(positions, intensity) -> tuple[np.ndarray, np.ndarray]:
    """Separate vertical segments with NaN; never draw a zero-intensity baseline."""
    positions, intensity = np.asarray(positions, dtype=float), np.asarray(intensity, dtype=float)
    valid = np.isfinite(positions) & np.isfinite(intensity) & (intensity > 0)
    x = np.repeat(positions[valid], 3)
    y = np.column_stack((np.zeros(np.sum(valid)), intensity[valid], np.full(np.sum(valid), np.nan))).ravel()
    return x, y
