"""Explicit legacy PRF reflection-column adaptation, never native LoadWave/G.

The converter opts in only after recognizing a legacy two-block PRF workflow.
Numeric import remains lossless: an additional reflection index is retained at
the end of the returned name list, not removed from the document or input.
"""
from __future__ import annotations

import numpy as np

from materials_data_studio.tools.command_services import DocumentServices


def reflection_columns(services: DocumentServices, names: str, header: str) -> str:
    """Validate a legacy 3-index or extended 4-index PRF reflection table."""
    error = "Legacy PRF reflection compatibility: unsupported or ambiguous column schema"
    if not isinstance(names, str) or not isinstance(header, str):
        raise ValueError(error)
    lines = header.splitlines()
    try:
        metadata = np.asarray([float(item) for item in lines[0].split()])
    except (IndexError, ValueError):
        raise ValueError(error + "; missing integer phase header") from None
    if (not 2 <= metadata.size <= 128 or not np.isfinite(metadata).all()
            or not np.equal(metadata, np.floor(metadata)).all()
            or not 1 <= metadata[0] <= 100):
        raise ValueError(error + "; invalid integer phase header")
    phase_count = int(metadata[0])
    columns = names.removesuffix(";").split(";")
    if len(columns) not in {10, 11} or any(not item for item in columns) or len(set(columns)) != len(columns):
        raise ValueError(error + "; expected 10 or 11 distinct numeric waves")
    arrays = [services.wave(name).data for name in columns]
    if (any(array.ndim != 1 or array.dtype.kind not in "biuf" or not array.size for array in arrays)
            or len({array.shape for array in arrays}) != 1):
        raise ValueError(error + "; reflection waves must have equal nonempty numeric lengths")

    def integral(array: np.ndarray, *, positive: bool = False) -> bool:
        return bool(np.isfinite(array).all() and np.equal(array, np.floor(array)).all()
                    and (not positive or np.all(array > 0)))

    def valid(extra: int) -> bool:
        if not all(integral(array) for array in arrays[:3 + extra]):
            return False
        multiplicity, phase, angle, offset = arrays[3 + extra:7 + extra]
        angle = np.asarray(angle, dtype=np.float64)
        return bool(integral(multiplicity, positive=True) and integral(phase, positive=True)
                    and np.array_equal(np.unique(phase), np.arange(1, phase_count + 1))
                    and np.isfinite(angle).all() and np.all((angle >= 0) & (angle <= 180))
                    and np.isfinite(offset).all())

    legacy = valid(0)
    extended = len(columns) == 11 and valid(1)
    if len(columns) == 10 and legacy:
        return names
    if len(columns) == 11 and extended and not legacy:
        print("Warning: explicit legacy PRF compatibility mapped an additional reflection index; "
              "all imported columns are preserved, with that index last. Native LoadWave/G is unchanged.")
        return ";".join(columns[i] for i in (0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 3)) + ";"
    raise ValueError(error + "; review the reflection format before continuing")
