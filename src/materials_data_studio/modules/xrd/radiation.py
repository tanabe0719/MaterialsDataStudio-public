"""Lightweight radiation geometry, independent of peak fitting libraries."""
import numpy as np

CU_ALPHA1 = 1.540598
CU_ALPHA2 = 1.544426
DOUBLET = "Cu Kα1/2"


def alpha2_position(alpha1):
    argument = np.sin(np.deg2rad(np.asarray(alpha1) / 2)) * CU_ALPHA2 / CU_ALPHA1
    return np.rad2deg(2 * np.arcsin(np.clip(argument, -1, 1)))
