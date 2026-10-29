"""Centralized light and dark application themes."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from materials_data_studio.ui.icons import resource_root


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Reusable visual constants for an application theme."""

    background: str
    surface: str
    surface_muted: str
    text: str
    text_muted: str
    border: str
    primary: str
    primary_hover: str
    on_primary: str
    danger: str
    selection: str
    hover: str
    radius: int = 3
    spacing: int = 8
    font_pt: int = 10
    control_padding: int = 4
    icon_size: int = 15


LIGHT = ThemeTokens(
    background="#F1F3F5",
    surface="#FFFFFF",
    surface_muted="#F8FAFC",
    text="#172033",
    text_muted="#657188",
    border="#D8E0EA",
    primary="#0F6B78",
    primary_hover="#0B5864",
    on_primary="#FFFFFF",
    danger="#C2413B",
    selection="#D9EEF1",
    hover="#EAF5F6",
)
DARK = ThemeTokens(
    background="#1B1F24",
    surface="#22272E",
    surface_muted="#292F37",
    text="#E5E7EB",
    text_muted="#A8B3C7",
    border="#3C4653",
    primary="#5CC8D7",
    primary_hover="#2894A3",
    on_primary="#07191D",
    danger="#B83E3E",
    selection="#164E58",
    hover="#203B43",
)


def current_theme() -> str:
    app = QApplication.instance()
    return "dark" if app is not None and app.property("materialsTheme") == "dark" else "light"


def apply_application_theme(app: QApplication, theme: str) -> None:
    """Update native widget roles and QSS only when a theme is requested."""
    tokens = DARK if theme == "dark" else LIGHT
    palette = QPalette(app.palette())
    for role, color in (
        (QPalette.ColorRole.Window, tokens.background), (QPalette.ColorRole.Base, tokens.surface),
        (QPalette.ColorRole.AlternateBase, tokens.surface_muted), (QPalette.ColorRole.Button, tokens.surface),
        (QPalette.ColorRole.WindowText, tokens.text), (QPalette.ColorRole.Text, tokens.text),
        (QPalette.ColorRole.ButtonText, tokens.text), (QPalette.ColorRole.Highlight, tokens.selection),
        (QPalette.ColorRole.HighlightedText, tokens.text), (QPalette.ColorRole.ToolTipBase, tokens.surface),
        (QPalette.ColorRole.ToolTipText, tokens.text), (QPalette.ColorRole.Link, tokens.primary),
    ):
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(tokens.text_muted))
    app.setPalette(palette)
    app.setProperty("materialsTheme", theme)
    app.setStyleSheet(style_sheet(theme))


def style_sheet(theme: str) -> str:
    """Return the complete QSS for ``light`` or ``dark`` mode."""
    if theme not in {"light", "dark"}:
        raise ValueError("Theme must be 'light' or 'dark'.")
    return theme_style_sheet(DARK if theme == "dark" else LIGHT)


def light_style_sheet(tokens: ThemeTokens = LIGHT) -> str:
    """Return the light QSS for backward-compatible callers."""
    return theme_style_sheet(tokens)


def theme_style_sheet(tokens: ThemeTokens) -> str:
    """Build the application QSS from centralized tokens."""
    check_icon = (resource_root() / "resources/icons/controls" / ("check_dark.svg" if tokens == DARK else "check_light.svg")).as_posix()
    if tokens == DARK:
        block_colors = {
            "s": ("#1E3A5F", "#DBEAFE", "#3B82F6"),
            "p": ("#174A34", "#DCFCE7", "#22C55E"),
            "d": ("#5A4315", "#FEF3C7", "#F59E0B"),
            "f": ("#5B2347", "#FCE7F3", "#EC4899"),
        }
    else:
        block_colors = {
            "s": ("#DBEAFE", "#1E3A8A", "#93C5FD"),
            "p": ("#DCFCE7", "#14532D", "#86EFAC"),
            "d": ("#FEF3C7", "#78350F", "#FCD34D"),
            "f": ("#FCE7F3", "#831843", "#F9A8D4"),
        }
    block_rules = "\n".join(
        f"""
        QPushButton[elementBlock="{block}"], QLabel[elementBlock="{block}"] {{
            background: {background};
            color: {foreground};
            border: 1px solid {border};
            border-radius: 5px;
            font-weight: 600;
        }}
        """
        for block, (background, foreground, border) in block_colors.items()
    )
    return f"""
    * {{
        font-family: "Segoe UI Variable", "Segoe UI";
        font-size: {tokens.font_pt}pt;
        color: {tokens.text};
    }}
    QMainWindow, QWidget {{
        background: {tokens.background};
    }}
    QLabel, QCheckBox, QRadioButton {{
        background: transparent;
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 14px;
        height: 14px;
        border: 1px solid {tokens.text_muted};
        background: {tokens.surface};
        border-radius: 3px;
    }}
    QRadioButton::indicator {{ border-radius: 7px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {tokens.primary};
        border: 2px solid {tokens.on_primary};
    }}
    QCheckBox::indicator:checked {{ image: url("{check_icon}"); }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
        border-color: {tokens.border};
        background: {tokens.surface_muted};
    }}
    QFrame[card="true"], QGroupBox {{
        background: {tokens.surface};
        border: 1px solid {tokens.border};
        border-radius: {tokens.radius}px;
    }}
    QGroupBox {{
        margin-top: 11px;
        padding: 10px 8px 8px 8px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 26px;
        padding: 0 4px;
    }}
    QToolButton[sectionToggle="true"] {{ border: none; padding: 0; min-height: 16px; background: transparent; }}
    QPushButton[panelToggle="true"]:checked {{ background: {tokens.selection}; border-color: {tokens.primary}; }}
    QPushButton[destructive="true"] {{ color: {tokens.danger}; border-color: {tokens.danger}; background: {tokens.surface}; }}
    QPushButton[supportAction="true"] {{ margin-top: 8px; }}
    QGroupBox[panelFrame="true"] {{ border: none; background: {tokens.surface}; padding: 4px; }}
    QLabel[sectionHeading="true"] {{
        color: {tokens.primary};
        font-weight: 650;
        padding-top: 5px;
    }}
    QLabel[scopeBadge="true"] {{
        color: {tokens.text_muted};
        font-size: 9pt;
        font-weight: 400;
        padding-bottom: 3px;
    }}
    QGroupBox[settingsPage] {{
        border: none;
        border-bottom: 1px solid {tokens.border};
        border-radius: 0;
    }}
    QDockWidget::title {{
        background: {tokens.surface_muted};
        padding: 5px 8px;
        font-weight: 600;
    }}
    QLabel[dropHint="true"] {{
        color: {tokens.text_muted};
        background: {tokens.surface_muted};
        border: 1px dashed {tokens.border};
        border-radius: 4px;
        padding: 8px;
    }}
    QPushButton, QToolButton {{
        background: {tokens.surface};
        border: 1px solid {tokens.border};
        border-radius: {tokens.radius}px;
        padding: {tokens.control_padding}px 9px;
        min-height: 18px;
    }}
    QPushButton:hover, QToolButton:hover {{
        border-color: {tokens.primary};
        background: {tokens.hover};
    }}
    QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus,
    QSpinBox:focus, QDoubleSpinBox:focus, QListWidget:focus,
    QPlainTextEdit:focus, QTextEdit:focus, QTreeView:focus, QTableView:focus {{
        border-color: {tokens.primary};
    }}
    QPushButton:default {{ border: 2px solid {tokens.primary}; padding: 3px 8px; }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {tokens.text_muted};
        background: {tokens.surface_muted};
        border-color: {tokens.border};
    }}
    QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{
        color: {tokens.text_muted}; background: {tokens.surface_muted};
    }}
    QPushButton[primary="true"] {{
        background: {tokens.primary};
        border-color: {tokens.primary};
        color: {tokens.on_primary};
        font-weight: 600;
    }}
    QPushButton[primary="true"]:hover {{
        background: {tokens.primary_hover};
        color: {tokens.on_primary};
    }}
    QPushButton[danger="true"] {{
        background: {tokens.danger};
        border-color: {tokens.danger};
        color: white;
        font-weight: 600;
    }}
    QPushButton[danger="true"]:hover {{
        background: {tokens.danger};
        border-color: {tokens.text};
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget, QTableView,
    QPlainTextEdit, QTextEdit, QTreeView {{
        background: {tokens.surface};
        border: 1px solid {tokens.border};
        border-radius: 4px;
        padding: 4px;
        selection-background-color: {tokens.selection};
        selection-color: {tokens.text};
    }}
    QTreeView::item {{ min-height: 24px; padding: 1px 3px; }}
    QTreeView::item:selected {{ background: {tokens.selection}; color: {tokens.text}; }}
    QTreeView::item:hover:!selected {{ background: {tokens.hover}; }}
    QTableView::item:selected {{ background: {tokens.selection}; color: {tokens.text}; }}
    QComboBox QAbstractItemView {{ background: {tokens.surface}; color: {tokens.text}; selection-background-color: {tokens.selection}; }}
    QMenu::item {{ padding: 5px 22px 5px 12px; }}
    QMenu::separator {{ height: 1px; background: {tokens.border}; margin: 4px 8px; }}
    QProgressBar {{ background: {tokens.surface_muted}; border: 1px solid {tokens.border}; border-radius: 3px; text-align: center; }}
    QProgressBar::chunk {{ background: {tokens.primary}; }}
    QTableView {{
        alternate-background-color: {tokens.surface_muted};
        gridline-color: {tokens.border};
    }}
    QListWidget::item {{
        border-radius: 3px;
        padding: 4px 5px;
    }}
    QListWidget::item:hover {{
        background: {tokens.hover};
    }}
    QListWidget::item:selected {{
        background: {tokens.selection};
        color: {tokens.text};
    }}
    QHeaderView::section {{
        background: {tokens.surface_muted};
        color: {tokens.text};
        border: none;
        border-right: 1px solid {tokens.border};
        border-bottom: 1px solid {tokens.border};
        padding: 6px;
        font-weight: 600;
    }}
    QMenu, QMenuBar, QScrollArea, QAbstractScrollArea {{
        background: {tokens.surface};
        color: {tokens.text};
    }}
    QMenuBar::item {{
        padding: 5px 9px;
        border-radius: 3px;
    }}
    QMenuBar::item:selected {{
        background: {tokens.hover};
    }}
    QMenu::item:selected {{
        background: {tokens.selection};
    }}
    QToolTip {{
        background: {tokens.surface_muted};
        color: {tokens.text};
        border: 1px solid {tokens.border};
    }}
    QTabWidget::pane {{
        border: none;
        background: {tokens.surface};
    }}
    QTabBar {{
        background: {tokens.surface};
        border-bottom: 1px solid {tokens.border};
        qproperty-drawBase: 0;
    }}
    QTabBar::tab {{
        background: transparent;
        padding: 7px 12px;
        margin-right: 3px;
        border: none;
        border-bottom: 2px solid transparent;
        color: {tokens.text_muted};
    }}
    QTabBar::tab:selected {{
        background: transparent;
        color: {tokens.primary};
        font-weight: 600;
        border-bottom-color: {tokens.primary};
    }}
    QTabBar::tab:hover:!selected {{
        background: {tokens.hover};
        color: {tokens.text};
    }}
    QTabBar[navigation="primary"]::tab {{
        padding: 11px 26px 9px 26px;
        border-bottom-width: 3px;
        font-size: 10.5pt;
    }}
    QTabBar[navigation="mode"]::tab,
    QTabBar[navigation="plot"]::tab {{
        background: {tokens.surface_muted};
        border: 1px solid {tokens.border};
        border-radius: 5px;
        padding: 6px 15px;
        margin: 4px 3px 5px 0;
    }}
    QTabBar[navigation="mode"]::tab:selected,
    QTabBar[navigation="plot"]::tab:selected {{
        background: {tokens.primary};
        border-color: {tokens.primary};
        color: {tokens.on_primary};
    }}
    QTabBar[navigation="dialog"]::tab {{
        padding: 7px 16px;
    }}
    QToolBar {{
        background: {tokens.surface};
        border-bottom: 1px solid {tokens.border};
        spacing: 3px;
        padding: 2px 5px;
    }}
    QToolBar QToolButton {{
        background: transparent;
        border: none;
        padding: 1px;
        margin: 0 1px;
        border-radius: 4px;
    }}
    QToolBar QToolButton:hover {{
        background: {tokens.hover};
        border: none;
    }}
    QToolBar QToolButton:pressed {{
        background: {tokens.selection};
        border: none;
    }}
    QToolBar QToolButton:focus {{
        background: {tokens.selection}; border: 1px solid {tokens.primary}; padding: 0;
    }}
    QPushButton[primary="true"]:disabled, QPushButton[danger="true"]:disabled {{
        color: {tokens.text_muted}; background: {tokens.surface_muted}; border-color: {tokens.border};
    }}
    QToolBar QToolButton:disabled {{
        background: transparent;
        border: none;
    }}
    QStatusBar {{
        background: {tokens.surface};
        border-top: 1px solid {tokens.border};
    }}
    QSplitter::handle {{
        background: transparent;
        width: 7px;
        height: 7px;
    }}
    QSlider::groove:horizontal {{
        height: 4px;
        background: {tokens.border};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {tokens.primary};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 14px;
        margin: -5px 0;
        background: {tokens.surface};
        border: 2px solid {tokens.primary};
        border-radius: 7px;
    }}
    QScrollBar:vertical {{
        width: 10px;
        background: transparent;
        margin: 1px;
    }}
    QScrollBar::handle:vertical {{
        min-height: 28px;
        background: {tokens.border};
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {tokens.text_muted};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        height: 10px;
        background: transparent;
        margin: 1px;
    }}
    QScrollBar::handle:horizontal {{
        min-width: 28px;
        background: {tokens.border};
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {tokens.text_muted};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
    }}
    {block_rules}
    QPushButton[elementBlock]:checked {{
        border: 3px solid {tokens.primary};
        padding: 4px 8px;
    }}
    """
