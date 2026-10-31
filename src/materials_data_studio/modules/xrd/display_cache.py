"""Bounded display transforms; scientific arrays and settings remain authoritative."""
from __future__ import annotations

import hashlib

import numpy as np

from materials_data_studio.core.models import AxisType, Dataset, NormalizationMode
from materials_data_studio.modules.xrd.models import TofCalibration
from materials_data_studio.modules.xrd.transforms import convert_x_axis, process_intensity


class DisplayDataCache:
    def __init__(self):
        self.values = {}
        self.peaks = {}

    def retain(self, identities):
        self.values = {key: value for key, value in self.values.items() if key in identities}
        self.peaks = {key: value for key, value in self.peaks.items() if key in identities}

    def annotation_parent(self, identity, x, y):
        from materials_data_studio.modules.xrd.phase_overlays import prepare_annotation_parent
        old = self.peaks.get(identity)
        if old is None or old[0] is not x or old[1] is not y:
            self.peaks[identity] = (x, y, prepare_annotation_parent(x, y))
        return self.peaks[identity][2]

    def get(self, dataset: Dataset, axis: AxisType):
        source = dataset.array("x")
        intensity = dataset.array("intensity")
        background = dataset.processed_data.get("background")
        arrays = (source, intensity, background)
        # Derived arrays can be edited in place. Hash only those writable inputs;
        # immutable raw measurements need no repeated scan or copy.
        stamps = tuple((id(a), hashlib.blake2b(memoryview(np.ascontiguousarray(a)).cast("B"), digest_size=16).digest() if a.flags.writeable else None)
                       if a is not None else None for a in arrays)
        metadata = dataset.metadata
        keys = ("source_axis", "wavelength_a", "is_simulated_phase", "simulation_zero_shift_deg",
                "simulation_displacement_coefficient_deg", "normalization", "scale", "offset", "background_enabled")
        calibration = metadata.get("tof_calibration")
        key = (axis, stamps, tuple(metadata.get(name) for name in keys), repr(calibration))
        old = self.values.get(dataset.dataset_id)
        if old is not None and old[0] == key:
            return old[2], old[3]
        source_axis = AxisType(str(metadata.get("source_axis", "two_theta")))
        x = np.asarray(source, float)
        if metadata.get("is_simulated_phase") and source_axis is AxisType.TWO_THETA:
            x = x + float(metadata.get("simulation_zero_shift_deg", 0.)) + float(metadata.get("simulation_displacement_coefficient_deg", 0.)) * np.cos(np.deg2rad(x / 2))
        x = convert_x_axis(x, source_axis, axis, wavelength_a=float(metadata.get("wavelength_a", 1.540598)),
                           tof_calibration=TofCalibration(**calibration) if isinstance(calibration, dict) else None)
        y = process_intensity(x, intensity, normalization=NormalizationMode(str(metadata.get("normalization", "none"))),
                              scale=float(metadata.get("scale", 1.)), offset=float(metadata.get("offset", 0.)),
                              background=background, subtract_background=bool(metadata.get("background_enabled", False)))
        # Own arrays: freezing these never changes the source/processed inputs.
        x = np.array(x, dtype=float, copy=True)
        y = np.array(y, dtype=float, copy=True)
        x.setflags(write=False)
        y.setflags(write=False)
        self.peaks.pop(dataset.dataset_id, None)
        self.values[dataset.dataset_id] = (key, arrays, x, y)
        return x, y
