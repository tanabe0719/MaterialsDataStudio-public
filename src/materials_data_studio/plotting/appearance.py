"""Persistent appearance settings shared by every interactive plot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from materials_data_studio.core.settings import create_settings


@dataclass(frozen=True, slots=True)
class PlotThemeColors:
    """Theme-specific colors for text, frames, and ticks."""

    axis_label: str
    title: str
    tick_label: str
    legend_text: str
    frame: str
    tick: str
    grid: str
    background: str
    legend_background: str
    legend_border: str


@dataclass(frozen=True, slots=True)
class PlotAppearance:
    """Typography and geometry used by interactive plot axes and legends."""

    axis_label_font_size: int
    title_font_size: int
    tick_label_font_size: int
    legend_font_size: int
    frame_width: float
    tick_width: float
    tick_length: float
    minor_tick_length: float
    grid_width: float
    grid_opacity: float
    show_title: bool
    show_axis_labels: bool
    show_tick_labels: bool
    show_legend: bool
    show_grid: bool
    show_left_axis: bool
    show_bottom_axis: bool
    show_top_axis: bool
    show_right_axis: bool
    show_major_ticks: bool
    show_minor_ticks: bool
    x_major_tick_interval: float
    x_minor_tick_interval: float
    y_major_tick_interval: float
    y_minor_tick_interval: float
    light: PlotThemeColors
    dark: PlotThemeColors


DEFAULT_PLOT_APPEARANCE = PlotAppearance(
    axis_label_font_size=11,
    title_font_size=12,
    tick_label_font_size=9,
    legend_font_size=9,
    frame_width=1.0,
    tick_width=1.0,
    tick_length=5.0,
    minor_tick_length=3.0,
    grid_width=0.75,
    grid_opacity=0.25,
    show_title=True,
    show_axis_labels=True,
    show_tick_labels=True,
    show_legend=True,
    show_grid=True,
    show_left_axis=True,
    show_bottom_axis=True,
    show_top_axis=True,
    show_right_axis=True,
    show_major_ticks=True,
    show_minor_ticks=False,
    x_major_tick_interval=0.0,
    x_minor_tick_interval=0.0,
    y_major_tick_interval=0.0,
    y_minor_tick_interval=0.0,
    light=PlotThemeColors(
        axis_label="#172033",
        title="#172033",
        tick_label="#172033",
        legend_text="#172033",
        frame="#172033",
        tick="#172033",
        grid="#CBD5E1",
        background="#FFFFFF",
        legend_background="#FFFFFF",
        legend_border="#CBD5E1",
    ),
    dark=PlotThemeColors(
        axis_label="#E5E7EB",
        title="#E5E7EB",
        tick_label="#E5E7EB",
        legend_text="#E5E7EB",
        frame="#A8B3C7",
        tick="#A8B3C7",
        grid="#475569",
        background="#111827",
        legend_background="#172033",
        legend_border="#475569",
    ),
)

_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_active_appearance = DEFAULT_PLOT_APPEARANCE


def _bounded_number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default


def _color(value: Any, default: str) -> str:
    candidate = str(value)
    return candidate.upper() if _COLOR_PATTERN.fullmatch(candidate) else default


def _boolean(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _load_colors(settings: Any, theme: str, defaults: PlotThemeColors) -> PlotThemeColors:
    prefix = f"appearance/plot/{theme}"
    return PlotThemeColors(
        axis_label=_color(settings.value(f"{prefix}/axis_label"), defaults.axis_label),
        title=_color(settings.value(f"{prefix}/title"), defaults.title),
        tick_label=_color(settings.value(f"{prefix}/tick_label"), defaults.tick_label),
        legend_text=_color(settings.value(f"{prefix}/legend_text"), defaults.legend_text),
        frame=_color(settings.value(f"{prefix}/frame"), defaults.frame),
        tick=_color(settings.value(f"{prefix}/tick"), defaults.tick),
        grid=_color(settings.value(f"{prefix}/grid"), defaults.grid),
        background=_color(settings.value(f"{prefix}/background"), defaults.background),
        legend_background=_color(
            settings.value(f"{prefix}/legend_background"), defaults.legend_background
        ),
        legend_border=_color(
            settings.value(f"{prefix}/legend_border"), defaults.legend_border
        ),
    )


def load_plot_appearance(settings: Any | None = None) -> PlotAppearance:
    """Load validated interactive-plot settings from QSettings."""
    if settings is None:
        settings = create_settings()
    defaults = DEFAULT_PLOT_APPEARANCE
    return PlotAppearance(
        axis_label_font_size=int(
            _bounded_number(
                settings.value("appearance/plot/axis_label_font_size"),
                defaults.axis_label_font_size,
                6,
                32,
            )
        ),
        title_font_size=int(
            _bounded_number(
                settings.value("appearance/plot/title_font_size"),
                defaults.title_font_size,
                6,
                32,
            )
        ),
        tick_label_font_size=int(
            _bounded_number(
                settings.value("appearance/plot/tick_label_font_size"),
                defaults.tick_label_font_size,
                6,
                32,
            )
        ),
        legend_font_size=int(
            _bounded_number(
                settings.value("appearance/plot/legend_font_size"),
                defaults.legend_font_size,
                6,
                32,
            )
        ),
        frame_width=_bounded_number(
            settings.value("appearance/plot/frame_width"), defaults.frame_width, 0.25, 6.0
        ),
        tick_width=_bounded_number(
            settings.value("appearance/plot/tick_width"), defaults.tick_width, 0.25, 6.0
        ),
        tick_length=_bounded_number(
            settings.value("appearance/plot/tick_length"), defaults.tick_length, 0.0, 30.0
        ),
        minor_tick_length=_bounded_number(
            settings.value("appearance/plot/minor_tick_length"),
            defaults.minor_tick_length,
            0.0,
            30.0,
        ),
        grid_width=_bounded_number(
            settings.value("appearance/plot/grid_width"), defaults.grid_width, 0.1, 6.0
        ),
        grid_opacity=_bounded_number(
            settings.value("appearance/plot/grid_opacity"), defaults.grid_opacity, 0.0, 1.0
        ),
        show_title=_boolean(settings.value("appearance/plot/show_title"), defaults.show_title),
        show_axis_labels=_boolean(
            settings.value("appearance/plot/show_axis_labels"), defaults.show_axis_labels
        ),
        show_tick_labels=_boolean(
            settings.value("appearance/plot/show_tick_labels"), defaults.show_tick_labels
        ),
        show_legend=_boolean(
            settings.value("appearance/plot/show_legend"), defaults.show_legend
        ),
        show_grid=_boolean(settings.value("appearance/plot/show_grid"), defaults.show_grid),
        show_left_axis=_boolean(
            settings.value("appearance/plot/show_left_axis"), defaults.show_left_axis
        ),
        show_bottom_axis=_boolean(
            settings.value("appearance/plot/show_bottom_axis"), defaults.show_bottom_axis
        ),
        show_top_axis=_boolean(
            settings.value("appearance/plot/show_top_axis"), defaults.show_top_axis
        ),
        show_right_axis=_boolean(
            settings.value("appearance/plot/show_right_axis"), defaults.show_right_axis
        ),
        show_major_ticks=_boolean(
            settings.value("appearance/plot/show_major_ticks"), defaults.show_major_ticks
        ),
        show_minor_ticks=_boolean(
            settings.value("appearance/plot/show_minor_ticks"), defaults.show_minor_ticks
        ),
        x_major_tick_interval=_bounded_number(
            settings.value("appearance/plot/x_major_tick_interval"),
            defaults.x_major_tick_interval,
            0.0,
            1.0e12,
        ),
        x_minor_tick_interval=_bounded_number(
            settings.value("appearance/plot/x_minor_tick_interval"),
            defaults.x_minor_tick_interval,
            0.0,
            1.0e12,
        ),
        y_major_tick_interval=_bounded_number(
            settings.value("appearance/plot/y_major_tick_interval"),
            defaults.y_major_tick_interval,
            0.0,
            1.0e12,
        ),
        y_minor_tick_interval=_bounded_number(
            settings.value("appearance/plot/y_minor_tick_interval"),
            defaults.y_minor_tick_interval,
            0.0,
            1.0e12,
        ),
        light=_load_colors(settings, "light", defaults.light),
        dark=_load_colors(settings, "dark", defaults.dark),
    )


def save_plot_appearance(appearance: PlotAppearance, settings: Any | None = None) -> None:
    """Persist every interactive-plot setting and update the runtime cache."""
    if settings is None:
        settings = create_settings()
    prefix = "appearance/plot"
    settings.setValue(f"{prefix}/axis_label_font_size", appearance.axis_label_font_size)
    settings.setValue(f"{prefix}/title_font_size", appearance.title_font_size)
    settings.setValue(f"{prefix}/tick_label_font_size", appearance.tick_label_font_size)
    settings.setValue(f"{prefix}/legend_font_size", appearance.legend_font_size)
    settings.setValue(f"{prefix}/frame_width", appearance.frame_width)
    settings.setValue(f"{prefix}/tick_width", appearance.tick_width)
    settings.setValue(f"{prefix}/tick_length", appearance.tick_length)
    settings.setValue(f"{prefix}/minor_tick_length", appearance.minor_tick_length)
    settings.setValue(f"{prefix}/grid_width", appearance.grid_width)
    settings.setValue(f"{prefix}/grid_opacity", appearance.grid_opacity)
    for field_name in (
        "show_title",
        "show_axis_labels",
        "show_tick_labels",
        "show_legend",
        "show_grid",
        "show_left_axis",
        "show_bottom_axis",
        "show_top_axis",
        "show_right_axis",
        "show_major_ticks",
        "show_minor_ticks",
        "x_major_tick_interval",
        "x_minor_tick_interval",
        "y_major_tick_interval",
        "y_minor_tick_interval",
    ):
        settings.setValue(f"{prefix}/{field_name}", getattr(appearance, field_name))
    for theme, colors in (("light", appearance.light), ("dark", appearance.dark)):
        for field_name in (
            "axis_label",
            "title",
            "tick_label",
            "legend_text",
            "frame",
            "tick",
            "grid",
            "background",
            "legend_background",
            "legend_border",
        ):
            settings.setValue(
                f"{prefix}/{theme}/{field_name}", getattr(colors, field_name)
            )
    settings.sync()
    set_active_plot_appearance(appearance)


def reset_plot_appearance(settings: Any | None = None) -> None:
    """Remove persisted overrides and restore runtime defaults."""
    if settings is None:
        settings = create_settings()
    settings.remove("appearance/plot")
    settings.sync()
    set_active_plot_appearance(DEFAULT_PLOT_APPEARANCE)


def active_plot_appearance() -> PlotAppearance:
    """Return the cached settings used during frequent plot refreshes."""
    return _active_appearance


def set_active_plot_appearance(appearance: PlotAppearance) -> None:
    """Replace the runtime appearance cache after loading or saving settings."""
    global _active_appearance
    _active_appearance = appearance
