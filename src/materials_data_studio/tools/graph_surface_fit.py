"""Finite matrix polynomial/Gaussian fits for image Quick Fit workflows."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from scipy.optimize import curve_fit

from materials_data_studio.tools.graph_waves import make_wave


def surface_fit(wave: Any, destination: str, *, gaussian: bool = False, degree: int = 2) -> list[Any]:
    if wave.data.ndim != 2 or wave.data.dtype.kind not in "biuf" or wave.data.size > 250000:
        raise ValueError("Surface fit requires a real matrix with <=250000 pixels")
    x,y = np.meshgrid(wave.coordinate(0),wave.coordinate(1),indexing="ij")
    z = np.asarray(wave.data,float)
    finite = np.isfinite(z) & np.isfinite(x) & np.isfinite(y)
    xx,yy,zz = x[finite],y[finite],z[finite]
    if not len(zz) or np.ptp(xx)==0 or np.ptp(yy)==0:
        raise ValueError("Surface fitting requires finite pixels spanning both dimensions")
    if gaussian:
        if len(zz) <= 7:
            raise ValueError("Rotated Gaussian needs more than seven finite pixels")
        def model(coords: Any, baseline: float, amplitude: float, cx: float, cy: float, wx: float, wy: float, angle: float) -> Any:
            a,b=coords[0]-cx,coords[1]-cy
            u,v=np.cos(angle)*a+np.sin(angle)*b,-np.sin(angle)*a+np.cos(angle)*b
            return baseline+amplitude*np.exp(-((u/wx)**2+(v/wy)**2))
        peak=int(np.argmax(zz))
        initial=[float(zz.min()),float(np.ptp(zz)),float(xx[peak]),float(yy[peak]),float(np.ptp(xx)/4),float(np.ptp(yy)/4),0.]
        coefficients,_=curve_fit(model,(xx,yy),zz,p0=initial,bounds=([-np.inf,-np.inf,-np.inf,-np.inf,1e-12,1e-12,-np.pi],[np.inf]*6+[np.pi]),maxfev=15000)
        fitted=model((x,y),*coefficients)
        labels=("baseline","amplitude","x_center","y_center","x_width","y_width","angle_radians")
    else:
        if not 1<=degree<=4:
            raise ValueError("Polynomial surface degree must be 1..4")
        powers=[(i,j) for total in range(degree+1) for i in range(total,-1,-1) for j in [total-i]]
        if len(zz)<=len(powers):
            raise ValueError("More finite pixels than polynomial coefficients are required")
        basis=np.array([xx**i*yy**j for i,j in powers]).T
        coefficients,_,rank,_=np.linalg.lstsq(basis,zz,rcond=None)
        if rank<len(powers):
            raise ValueError("Polynomial surface is rank deficient")
        fitted=sum(c*x**i*y**j for c,(i,j) in zip(coefficients,powers,strict=True))
        labels=tuple(f"x{i}_y{j}" for i,j in powers)
    if not np.isfinite(fitted).all():
        raise ValueError("Surface fit produced nonfinite pixels")
    from materials_data_studio.tools.igor_wave_ops import set_dimension_label
    coef=make_wave(destination+"_coef",coefficients)
    for i,label in enumerate(labels):
        coef=set_dimension_label(coef,0,i,label)
    note=f"MDS surface fit: {'Gauss2D rotated' if gaussian else 'poly2D total degree '+str(degree)}; source={wave.igor_path}; finite_pixels={len(zz)}"
    return [replace(make_wave(destination,fitted,wave),wave_note=note),
            replace(make_wave(destination+"_residual",z-fitted,wave),wave_note=note),
            replace(coef,wave_note=note)]
