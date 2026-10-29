"""Measurement-order overview with existing automatic/manual segment boundaries."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from materials_data_studio.plotting.plot_spec import CurveSpec, PlotSpec
from materials_data_studio.plotting.pyqtgraph_backend import create_plot_widget, render_plot


def measurement_overview(dataset_list):
    dataset = dataset_list.current_dataset()
    if dataset is None:
        return
    owner = dataset_list.parentWidget()
    while owner is not None and not any(hasattr(owner, key) for key in ("_split_for", "_current_segment", "_selected_segment")):
        owner = owner.parentWidget()
    dialog = QDialog(dataset_list)
    dialog.setWindowTitle("Measurement order / segmentation — " + dataset.display_name)
    dialog.resize(1050, 750)
    layout = QVBoxLayout(dialog)
    explanation = QLabel("Original measurement order is preserved. Vertical lines show segment boundaries; the highlighted interval is the selected segment. Manual split/segment controls remain in the Data category. No raw points are deleted.")
    explanation.setWordWrap(True)
    layout.addWidget(explanation)
    boundaries, selected = [], None
    if owner is not None:
        if hasattr(owner, "_split_for"):
            split = owner._split_for(dataset)
            boundaries = [split.active_index]
        else:
            current, segments = owner._current_segment(dataset) if hasattr(owner, "_current_segment") else owner._selected_segment(dataset)
            boundaries = [int(segment.indices[0]) for segment in segments[1:]]
            selected = (int(current.indices[0]), int(current.indices[-1]))
    arrays = dataset.raw_data
    if owner is not None and hasattr(owner, "_averaged_arrays"):
        arrays = owner._averaged_arrays(dataset)
    fields = [key for key in ("temperature_k", "field_oe") if key in arrays]
    if not fields:
        fields = [key for key, value in arrays.items() if value.ndim == 1 and value.dtype.kind in "biuf"][:2]
    for field in fields:
        values = np.asarray(arrays[field], dtype=float)
        plot = create_plot_widget()
        render_plot(plot, PlotSpec("Measurement index", field, [CurveSpec(np.arange(len(values), dtype=float), values, field)]))
        for index in boundaries:
            plot.addItem(pg.InfiniteLine(index, angle=90, pen=pg.mkPen("#dc2626", width=1.5)))
        if selected:
            plot.addItem(pg.LinearRegionItem(selected, movable=False, brush=pg.mkBrush(15, 107, 120, 35)))
        layout.addWidget(plot, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.close)
    layout.addWidget(buttons)
    dialog.exec()
    dialog.deleteLater()
