from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSettings

from materials_data_studio.plotting.appearance import (
    DEFAULT_PLOT_APPEARANCE,
    load_plot_appearance,
    reset_plot_appearance,
    save_plot_appearance,
)


def test_plot_appearance_round_trips_and_resets(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    custom = replace(
        DEFAULT_PLOT_APPEARANCE,
        axis_label_font_size=15,
        title_font_size=17,
        tick_label_font_size=10,
        legend_font_size=13,
        frame_width=2.5,
        tick_width=1.5,
        tick_length=9.0,
        minor_tick_length=4.0,
        grid_width=1.25,
        grid_opacity=0.6,
        show_right_axis=False,
        show_minor_ticks=True,
        x_major_tick_interval=5.0,
        light=replace(
            DEFAULT_PLOT_APPEARANCE.light,
            axis_label="#102030",
            title="#203040",
            grid="#304050",
        ),
        dark=replace(
            DEFAULT_PLOT_APPEARANCE.dark,
            axis_label="#E0D0C0",
            background="#101820",
        ),
    )
    save_plot_appearance(custom, settings)
    assert load_plot_appearance(settings) == custom
    reset_plot_appearance(settings)
    assert load_plot_appearance(settings) == DEFAULT_PLOT_APPEARANCE
