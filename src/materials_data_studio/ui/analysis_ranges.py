"""Shared draggable fit ranges with explicit plotted-coordinate mapping."""
from __future__ import annotations

from typing import cast

import numpy as np
import pyqtgraph as pg


def install_fit_range(plot, lower, upper, *, to_plot=lambda v: v, to_physical=lambda v: v, exclusions=None):
    region = pg.LinearRegionItem(values=(to_plot(lower.value()), to_plot(upper.value())), movable=True)
    region.setVisible(False)
    persistent = getattr(plot, "_materials_persistent_overlays", [])
    persistent.append(region)
    plot._materials_persistent_overlays = persistent
    plot.addItem(region)
    menu = plot.getViewBox().getMenu(None)
    action = menu.addAction("Edit fit range (drag handles)")
    action.setCheckable(True)
    action.toggled.connect(region.setVisible)
    updating = False
    def update_fields():
        nonlocal updating
        if updating:
            return
        updating = True
        try:
            values = sorted(to_physical(float(v)) for v in cast(tuple[float, float], region.getRegion()))
            if np.isfinite(values).all():
                lower.setValue(values[0])
                upper.setValue(values[1])
        finally:
            updating = False
    def update_region():
        if updating:
            return
        region.blockSignals(True)
        region.setRegion(sorted((to_plot(lower.value()), to_plot(upper.value()))))
        region.blockSignals(False)
    region.sigRegionChangeFinished.connect(update_fields)
    lower.valueChanged.connect(update_region)
    upper.valueChanged.connect(update_region)
    if exclusions is not None:
        def exclude():
            values = sorted(to_physical(float(v)) for v in cast(tuple[float, float], region.getRegion()))
            text = exclusions.text().strip()
            exclusions.setText((text + "; " if text else "") + f"{values[0]:.10g}-{values[1]:.10g}")
        menu.addAction("Exclude draggable range").triggered.connect(exclude)
    return region
