"""Color-entry previews remain recognizable on both themed field surfaces."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from materials_data_studio.ui.style_inputs import ColorEdit
from materials_data_studio.ui.theme import style_sheet


@pytest.mark.parametrize("theme,value", [("light", "#ffffff"), ("dark", "#000000")])
@pytest.mark.parametrize("enabled", [True, False])
def test_extreme_colors_have_a_visible_theme_border(
    qapp: QApplication, theme: str, value: str, enabled: bool
) -> None:
    edit = ColorEdit(value)
    edit.setStyleSheet(style_sheet(theme))
    edit.setEnabled(enabled)
    edit.ensurePolished()
    qapp.processEvents()

    mode = QIcon.Mode.Normal if enabled else QIcon.Mode.Disabled
    group = QPalette.ColorGroup.Active if enabled else QPalette.ColorGroup.Disabled
    image = edit.swatch.icon().pixmap(QSize(18, 18), mode).toImage()
    border = edit.palette().color(group, QPalette.ColorRole.Text)
    assert image.pixelColor(9, 9) == QColor(value)
    for x, y in [(0, 9), (17, 9), (9, 0), (9, 17)]:
        assert image.pixelColor(x, y) == border
        assert image.pixelColor(x, y) != image.pixelColor(9, 9)
    assert edit.text() == value
    edit.close()


def test_existing_preview_refreshes_when_theme_changes(qapp: QApplication) -> None:
    edit = ColorEdit("#ffffff")
    borders = []
    for theme in ["light", "dark", "light"]:
        edit.setStyleSheet(style_sheet(theme))
        edit.ensurePolished()
        qapp.processEvents()
        image = edit.swatch.icon().pixmap(QSize(18, 18)).toImage()
        borders.append(image.pixelColor(0, 9).name())
        assert image.pixelColor(9, 9) == QColor("white")
        assert image.pixelColor(0, 9) == edit.palette().color(QPalette.ColorRole.Text)
    assert borders[0] != borders[1]
    assert borders[0] == borders[2]
    edit.close()


@pytest.mark.parametrize("value", ["", "not-a-color", "none"])
def test_invalid_color_keeps_editable_text_and_bordered_fallback(
    qapp: QApplication, value: str
) -> None:
    edit = ColorEdit(value)
    edit.setStyleSheet(style_sheet("dark"))
    edit.ensurePolished()
    qapp.processEvents()
    image = edit.swatch.icon().pixmap(QSize(18, 18)).toImage()
    assert edit.text() == value
    assert image.pixelColor(9, 9) == QColor("#808080")
    assert image.pixelColor(0, 9) != image.pixelColor(9, 9)
    edit.setText("#123456")
    assert edit.swatch.icon().pixmap(QSize(18, 18)).toImage().pixelColor(9, 9) == QColor("#123456")
    edit.close()
