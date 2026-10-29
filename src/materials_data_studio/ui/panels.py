"""Shared compact settings navigation and responsive form layout."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class CardCollapse(QObject):
    """Fold a body container; conditional visibility inside is left untouched."""
    def __init__(self, card: QGroupBox) -> None:
        super().__init__(card)
        self.card, self.closed = card, False
        self.body = QWidget(card)
        old = card.layout()
        if old is not None:
            self.body.setLayout(old)
            old.setContentsMargins(4, 4, 4, 4)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.body)
        self.toggle = QToolButton(card)
        self.toggle.setProperty("sectionToggle", True)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.toggle.setAccessibleName("Expand or collapse section")
        self.toggle.setToolTip("Expand or collapse section")
        self.toggle.setGeometry(4, 0, 20, 20)
        self.toggle.toggled.connect(self.set_expanded)
        self.key = f"ui/sections/{card.property('settingsKey')}/{card.property('sectionSource')}"
        from materials_data_studio.core.settings import create_settings
        expanded = str(create_settings().value(self.key, "true")).lower() != "false"
        self.toggle.setChecked(expanded)
        card.setProperty("sectionController", self)

    def set_expanded(self, expanded: bool) -> None:
        self.closed = not expanded
        self.body.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.card.updateGeometry()
        from materials_data_studio.core.settings import create_settings
        create_settings().setValue(self.key, expanded)


def compact_forms(root: QWidget) -> None:
    for label in root.findChildren(QLabel):
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    for form in root.findChildren(QFormLayout):
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
    for layout in root.findChildren(QLayout):
        layout.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)


def settings_tabs(root: QWidget, key: str = "workspace", sample_scope: str = "Selected dataset") -> QTabWidget:
    """Move existing cards without changing their controls, signals or state."""
    compact_forms(root)
    tabs = QTabWidget()
    tabs.setObjectName("SettingsNavigation")
    tabs.tabBar().setProperty("navigation", "dialog")
    tabs.tabBar().setExpanding(False)
    tabs.setMinimumWidth(0)
    tabs.setUsesScrollButtons(True)
    layouts: dict[str, QVBoxLayout] = {}
    searches: dict[str, QLineEdit] = {}
    for title in ("Data", "Style", "Analysis", "Export"):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        search = QLineEdit()
        search.setPlaceholderText("Find a setting in this category…")
        layout.addWidget(search)
        searches[title] = search
        scroll = QScrollArea()
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        page_host = QWidget()
        host_layout = QVBoxLayout(page_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.addWidget(scroll, 1)
        tabs.addTab(page_host, title)
        layouts[title] = layout
    for card in root.findChildren(QGroupBox, options=Qt.FindChildOption.FindDirectChildrenOnly):
        title = card.title().lower()
        category = (
            "Export"
            if "export" in title or "figure" in title
            else "Style"
            if "style" in title or "appearance" in title
            else "Analysis"
            if any(
                word in title
                for word in ("fit", "curie", "background", "lattice", "entropy", "calculation")
            )
            else "Data"
        )
        layouts[category].addWidget(card)
        card.setProperty("settingsPage", list(layouts).index(category))
        card.setProperty("settingsKey", key)
        card.setProperty("sectionSource", card.title())
        scope = "Plot display / selected wavelength" if title == "axis" else "Plot-wide" if "figure" in title else sample_scope if "sample" in title else "Selected dataset / branch"
        card.setToolTip(scope)
        badge = QLabel(scope)
        badge.setWordWrap(True)
        badge.setProperty("scopeBadge", True)
        card_layout = card.layout()
        if category == "Export" and isinstance(card_layout, QFormLayout):
            # Keep compatibility controls alive for session restoration, but
            # expose one authoritative image-settings dialog rather than two.
            for field in card.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
                if isinstance(field, QAbstractSpinBox) or (isinstance(field, QCheckBox) and field.text() != "Show legend"):
                    card_layout.setRowVisible(field, False)
            description = QLabel("Image size, DPI, transparency and reusable templates are configured in the unified Export dialog.")
            description.setWordWrap(True)
            card_layout.addRow(description)
            configure = QPushButton("Configure / export figure…")
            def open_export(_checked=False, target=card):
                window = target.window()
                callback = getattr(window, "export_figure", None)
                if callable(callback):
                    callback()
            configure.clicked.connect(open_export)
            card_layout.addRow(configure)
        if isinstance(card_layout, QFormLayout):
            card_layout.insertRow(0, badge)
        elif isinstance(card_layout, QVBoxLayout):
            card_layout.insertWidget(0, badge)
        CardCollapse(card)
        searches[category].textChanged.connect(lambda text, target=card: target.setVisible(text.casefold() in (target.title() + " " + " ".join(label.text() for label in target.findChildren(QLabel))).casefold()))
    for layout in layouts.values():
        layout.addStretch(1)
    for index, layout in enumerate(layouts.values()):
        tabs.setTabVisible(index, layout.count() > 2)
    root.deleteLater()
    from materials_data_studio.ui.settings_actions import ExecutionFooter
    for index in range(tabs.count()):
        host = tabs.widget(index)
        if host is None:
            continue
        cards = [card for card in host.findChildren(QGroupBox) if card.property("settingsPage") == index]
        if index == 2:
            footer = ExecutionFooter(cards, host)
            if footer.entries:
                host_layout = host.layout()
                if isinstance(host_layout, QVBoxLayout):
                    host_layout.addWidget(footer)
    return tabs


def install_settings_panel(scroll: QScrollArea, root: QWidget, key: str = "workspace", sample_scope: str = "Selected dataset") -> None:
    """Keep category navigation reachable while the central plot absorbs resizing."""
    host = QWidget()
    host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    layout = QVBoxLayout(host)
    layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
    layout.setContentsMargins(0, 0, 0, 0)
    heading = QLabel("Editing target")
    heading.setProperty("sectionHeading", True)
    layout.addWidget(heading)
    target = QLabel("Select a dataset or branch")
    target.setObjectName("SettingsTarget")
    target.setWordWrap(True)
    target.setMaximumHeight(42)
    target.setProperty("mdsLiteralData", True)
    layout.addWidget(target)
    tabs = settings_tabs(root, key, sample_scope)
    tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    layout.addWidget(tabs, 1)
    # The existing outer shell is a fixed viewport. Only category pages scroll;
    # QScrollArea's widget size-hint negotiation would clip the pinned footer.
    viewport_layout = QVBoxLayout(scroll.viewport())
    viewport_layout.setContentsMargins(0, 0, 0, 0)
    viewport_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
    viewport_layout.addWidget(host)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setMinimumWidth(300)
    scroll.setObjectName("SettingsPanel")


def dataset_context(root: QWidget, enabled) -> None:
    from materials_data_studio.core.models import Dataset
    from materials_data_studio.ui.localization import tr
    dataset = enabled if isinstance(enabled, Dataset) else None
    for label in root.findChildren(QLabel, "SettingsTarget"):
        if dataset is None:
            label.setProperty("mdsLiteralData", False)
            label.setText(tr("Select a dataset or branch"))
            label.setProperty("mdsCaptionSource", "Select a dataset or branch")
            label.setProperty("mdsCaptionRendered", tr("Select a dataset or branch"))
            label.setToolTip("")
        else:
            label.setProperty("mdsLiteralData", True)
            state = getattr(root, "state", None)
            from materials_data_studio.core.measurement_branches import root_dataset
            parent = root_dataset(state, dataset) if state else dataset
            names = [parent.display_name]
            branch = dataset.metadata.get("measurement_branch", {})
            if branch.get("bridge"):
                names.append(f"Bridge {branch['bridge']}")
            if dataset is not parent:
                names.append(dataset.display_name)
            text = " → ".join(names)
            label.setText(label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, max(180, root.width()//3)))
            label.setToolTip(dataset.source_filename + "\n" + text)
    for button in root.findChildren(QPushButton):
        source = str(button.property("mdsCaptionSource") or button.text())
        if source.startswith("Import "):
            from materials_data_studio.ui.action_layout import button_role
            button.setProperty("mdsImportAction", True)
            button_role(button, "secondary" if dataset is not None else "primary")
    for card in root.findChildren(QGroupBox):
        if card.property("settingsPage") is not None:
            card.setEnabled(bool(enabled))


def reveal_settings(card: QGroupBox) -> None:
    parent = card.parentWidget()
    while parent is not None:
        if isinstance(parent, QTabWidget) and parent.objectName() == "SettingsNavigation":
            parent.setCurrentIndex(int(card.property("settingsPage") or 0))
            return
        parent = parent.parentWidget()
