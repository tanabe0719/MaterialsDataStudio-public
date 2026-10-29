"""Validated named figure presets shared by Preferences and export dialogs."""
from __future__ import annotations

import json

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QInputDialog, QPushButton

from materials_data_studio.core.settings import create_settings
from materials_data_studio.plotting.appearance import (
    active_plot_appearance,
    load_plot_appearance,
    save_plot_appearance,
)


class MemorySettings:
    def __init__(self, values=None):
        self.values = values or {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        """In-memory settings have no pending disk writes."""


def templates():
    try:
        value = json.loads(str(create_settings().value("figures/templates", "{}")))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def install_figure_templates(dialog, layout):
    row = QHBoxLayout()
    combo = QComboBox()
    combo.addItems(sorted(templates()))
    row.addWidget(combo, 1)
    save, load = QPushButton("Save figure template…"), QPushButton("Load template")
    row.addWidget(save)
    row.addWidget(load)
    layout.addLayout(row)
    def save_template():
        name, accepted = QInputDialog.getText(dialog, "Save figure template", "Template name")
        if accepted and name.strip():
            width, height, dpi, transparent = dialog.image_options()
            settings = MemorySettings()
            save_plot_appearance(getattr(dialog, "appearance_override", None) or active_plot_appearance(), settings)
            values = templates()
            values[name.strip()] = {"width_cm": width, "height_cm": height, "dpi": dpi, "transparent": transparent, "appearance": settings.values}
            create_settings().setValue("figures/templates", json.dumps(values))
            combo.clear()
            combo.addItems(sorted(values))
            combo.setCurrentText(name.strip())
    def load_template():
        value = templates().get(combo.currentText())
        if not value:
            return
        for control in (dialog.vector_width, dialog.raster_width):
            control.setValue(value["width_cm"])
        for control in (dialog.vector_height, dialog.raster_height):
            control.setValue(value["height_cm"])
        dialog.raster_dpi.setValue(value["dpi"])
        for control in (dialog.vector_transparent, dialog.raster_transparent):
            control.setChecked(bool(value["transparent"]))
        dialog._sync_raster_options()
        dialog.appearance_override = load_plot_appearance(MemorySettings(value.get("appearance", {})))
    save.clicked.connect(save_template)
    load.clicked.connect(load_template)
    dialog.figure_template_controls = (combo, save, load)
