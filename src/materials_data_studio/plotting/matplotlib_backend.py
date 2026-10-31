"""Publication-quality Matplotlib rendering from PlotSpec."""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

from materials_data_studio.plotting.appearance import PlotAppearance
from materials_data_studio.plotting.plot_spec import PlotSpec
from materials_data_studio.plotting.styles import MATPLOTLIB_LINE_STYLES

if TYPE_CHECKING:
    from matplotlib.figure import Figure

FIGURE_FILE_FILTERS = (
    "PNG (*.png);;SVG (*.svg);;PDF (*.pdf);;TIFF (*.tif *.tiff);;JPEG (*.jpg *.jpeg)"
)
_FIGURE_SUFFIXES = {".png", ".svg", ".pdf", ".tif", ".tiff", ".jpg", ".jpeg"}


def resolve_figure_export_path(path: str | Path, selected_filter: str = "") -> Path:
    """Make the filename extension match the save-dialog format selection."""
    resolved = Path(path)
    lowered_filter = selected_filter.lower()
    selected_suffix = (
        ".svg"
        if "svg" in lowered_filter
        else ".pdf"
        if "pdf" in lowered_filter
        else ".tif"
        if "tif" in lowered_filter
        else ".jpg"
        if "jpeg" in lowered_filter or "jpg" in lowered_filter
        else ".png"
        if "png" in lowered_filter
        else ""
    )
    if selected_suffix:
        return resolved.with_suffix(selected_suffix)
    return resolved if resolved.suffix.lower() in _FIGURE_SUFFIXES else resolved.with_suffix(".png")


def build_figure(
    spec: PlotSpec,
    *,
    appearance: PlotAppearance | None = None,
    theme: str = "light",
) -> Figure:
    """Create a standalone Matplotlib figure from the shared specification."""
    dimensions = (spec.figure_width_cm, spec.figure_height_cm, spec.dpi)
    if not all(math.isfinite(value) and value > 0 for value in dimensions):
        raise ValueError("Figure size and DPI must be finite positive numbers.")
    pixels = spec.figure_width_cm * spec.figure_height_cm * (spec.dpi / 2.54) ** 2
    if pixels > 80_000_000:
        raise ValueError("Figure exceeds the 80 megapixel safety limit; reduce size or DPI.")
    from matplotlib.figure import Figure

    figure = Figure(
        figsize=(spec.figure_width_cm / 2.54, spec.figure_height_cm / 2.54),
        dpi=spec.dpi,
    )
    axis = figure.add_subplot(111)
    for curve in spec.curves:
        style = curve.style
        from materials_data_studio.plotting.marker_symbols import mpl_symbol
        marker = mpl_symbol(style.marker)
        axis.plot(
            curve.x,
            curve.y,
            label=style.legend_text or curve.label,
            color=style.color,
            linestyle=MATPLOTLIB_LINE_STYLES.get(style.line_style, style.line_style),
            linewidth=style.line_width,
            marker=marker if curve.marker_sizes is None else None,
            markersize=style.marker_size,
            markeredgecolor=style.marker_edge_color or style.color,
            markerfacecolor=style.marker_fill_color or "none",
            markeredgewidth=style.marker_edge_width,
        )
        if curve.marker_sizes is not None and marker is not None:
            from matplotlib.markers import MarkerStyle
            scatter_colors: dict[str, Any] = ({"edgecolors": style.marker_edge_color or style.color, "facecolors": style.marker_fill_color or "none"}
                      if MarkerStyle(marker).is_filled() else {"color": style.marker_edge_color or style.color})
            axis.scatter(curve.x, curve.y, s=curve.marker_sizes**2, marker=marker, linewidths=style.marker_edge_width, **scatter_colors)
        for x, y, text in zip(curve.x, curve.y, curve.point_labels, strict=False):
            if text:
                axis.annotate(text, (x, y), xytext=(0, (style.marker_size / 2 if style.marker != "none" else 0) + 4), textcoords="offset points",
                              rotation=curve.label_angle, rotation_mode="anchor", ha="left", va="center",
                              fontfamily=curve.label_font or None, fontsize=curve.label_size, color=curve.label_color)
    axis.set_xlabel(spec.x_label)
    axis.set_ylabel(spec.y_label)
    if spec.x_log_scale:
        axis.set_xscale("log")
    if spec.y_log_scale:
        axis.set_yscale("log")
    if spec.title:
        axis.set_title(spec.title)
    if spec.x_range is not None:
        axis.set_xlim(*spec.x_range)
    if spec.y_range is not None:
        axis.set_ylim(*spec.y_range)
    axis.grid(spec.grid_enabled)
    axis.tick_params(direction="in", top=True, right=True)
    if spec.legend_enabled and spec.curves:
        axis.legend()
    if appearance is not None:
        colors = appearance.dark if theme == "dark" else appearance.light
        background = colors.background
        figure.patch.set_facecolor(background)
        axis.set_facecolor(background)
        axis.set_title(
            spec.title if appearance.show_title else "",
            color=colors.title,
            fontsize=appearance.title_font_size,
        )
        axis.xaxis.label.set_visible(appearance.show_axis_labels)
        axis.yaxis.label.set_visible(appearance.show_axis_labels)
        axis.xaxis.label.set_fontsize(appearance.axis_label_font_size)
        axis.yaxis.label.set_fontsize(appearance.axis_label_font_size)
        axis.xaxis.label.set_color(colors.axis_label)
        axis.yaxis.label.set_color(colors.axis_label)
        axis.tick_params(
            axis="both",
            which="both",
            direction="in",
            top=True,
            right=True,
            labelsize=appearance.tick_label_font_size,
            labelcolor=colors.tick_label,
            color=colors.tick,
            width=appearance.tick_width,
            length=appearance.tick_length if appearance.show_major_ticks else 0,
        )
        axis.tick_params(
            axis="both",
            which="minor",
            length=appearance.minor_tick_length if appearance.show_minor_ticks else 0,
        )
        axis.tick_params(labelbottom=appearance.show_tick_labels, labelleft=appearance.show_tick_labels)
        axis.minorticks_on() if appearance.show_minor_ticks else axis.minorticks_off()
        if appearance.x_major_tick_interval > 0 and not spec.x_log_scale:
            from matplotlib.ticker import MultipleLocator

            axis.xaxis.set_major_locator(MultipleLocator(appearance.x_major_tick_interval))
        if appearance.x_minor_tick_interval > 0 and appearance.show_minor_ticks and not spec.x_log_scale:
            from matplotlib.ticker import MultipleLocator

            axis.xaxis.set_minor_locator(MultipleLocator(appearance.x_minor_tick_interval))
        if appearance.y_major_tick_interval > 0 and not spec.y_log_scale:
            from matplotlib.ticker import MultipleLocator

            axis.yaxis.set_major_locator(MultipleLocator(appearance.y_major_tick_interval))
        if appearance.y_minor_tick_interval > 0 and appearance.show_minor_ticks and not spec.y_log_scale:
            from matplotlib.ticker import MultipleLocator

            axis.yaxis.set_minor_locator(MultipleLocator(appearance.y_minor_tick_interval))
        axis.spines["left"].set_visible(appearance.show_left_axis)
        axis.spines["bottom"].set_visible(appearance.show_bottom_axis)
        axis.spines["top"].set_visible(appearance.show_top_axis)
        axis.spines["right"].set_visible(appearance.show_right_axis)
        for spine in axis.spines.values():
            spine.set_color(colors.frame)
            spine.set_linewidth(appearance.frame_width)
        axis.tick_params(top=appearance.show_top_axis, right=appearance.show_right_axis)
        grid_visible = spec.grid_enabled and appearance.show_grid
        axis.grid(grid_visible)
        if grid_visible:
            for gridline in (*axis.get_xgridlines(), *axis.get_ygridlines()):
                gridline.set_color(colors.grid)
                gridline.set_linewidth(appearance.grid_width)
                gridline.set_alpha(appearance.grid_opacity)
        legend = axis.get_legend()
        if legend is not None:
            legend.set_visible(appearance.show_legend)
            legend.get_frame().set_facecolor(colors.legend_background)
            legend.get_frame().set_edgecolor(colors.legend_border)
            for text in legend.get_texts():
                text.set_color(colors.legend_text)
                text.set_fontsize(appearance.legend_font_size)
    figure.tight_layout()
    return figure


def export_figure(
    path: Path,
    spec: PlotSpec,
    *,
    appearance: PlotAppearance | None = None,
    theme: str = "light",
) -> None:
    """Export a supported raster/vector figure with configured dimensions."""
    if path.suffix.lower() not in _FIGURE_SUFFIXES:
        raise ValueError("Figure export supports PNG, SVG, PDF, TIFF, and JPEG.")
    figure = build_figure(spec, appearance=appearance, theme=theme)
    figure.savefig(
        path,
        dpi=spec.dpi,
        transparent=spec.transparent,
        format=path.suffix.lower().removeprefix("."),
    )
