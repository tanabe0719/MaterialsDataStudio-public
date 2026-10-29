from __future__ import annotations

from materials_data_studio.plotting.palettes import PALETTE_STOPS, gradient_colors


def test_gradient_assigns_endpoints_in_dataset_order() -> None:
    colors = gradient_colors(("#000000", "#FFFFFF"), 5)
    assert colors == ["#000000", "#404040", "#808080", "#bfbfbf", "#ffffff"]


def test_gradient_handles_empty_and_single_dataset() -> None:
    assert gradient_colors(("#123456", "#FFFFFF"), 0) == []
    assert gradient_colors(("#123456", "#FFFFFF"), 1) == ["#123456"]


def test_background_specific_presets_are_not_exposed() -> None:
    assert not any(name.startswith(("Light —", "Dark —")) for name in PALETTE_STOPS)
