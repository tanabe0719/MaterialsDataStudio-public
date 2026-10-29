"""Non-modal utility windows available from the Tools workspace."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.modules.mpms.formula import calculate_molar_mass
from materials_data_studio.tools.rasx_converter import convert_rasx_file
from materials_data_studio.tools.shannon import (
    roman_numeral_value,
    shannon_radii_for_element,
)
from materials_data_studio.ui.tasks import blocking_task


class RomanNumeralTableItem(QTableWidgetItem):
    """Table item whose visual Roman numeral sorts by numeric value."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        return (roman_numeral_value(self.text()), self.text()) < (
            roman_numeral_value(other.text()),
            other.text(),
        )


class ToolWindow(QMainWindow):
    """Base for independent, concurrently usable laboratory tools."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)


class MolarMassWindow(ToolWindow):
    """Calculate formula mass and retain a removable ordered history."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Molecular Weight Calculator", parent)
        self.resize(640, 520)
        central = QWidget()
        layout = QVBoxLayout(central)
        description = QLabel(
            "Enter a chemical formula. Parentheses, decimal stoichiometry, and "
            "dot-separated adducts are supported."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        entry_row = QHBoxLayout()
        self.formula = QLineEdit()
        self.formula.setPlaceholderText("Example: La2NiMnO6 or CuSO4·5H2O")
        self.calculate_button = QPushButton("Calculate")
        self.calculate_button.setProperty("primary", True)
        entry_row.addWidget(self.formula, 1)
        entry_row.addWidget(self.calculate_button)
        layout.addLayout(entry_row)

        self.result = QLabel("Molar mass: —")
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.result)
        history_group = QGroupBox("Calculation history")
        history_layout = QVBoxLayout(history_group)
        self.history = QTableWidget(0, 3)
        self.history.setHorizontalHeaderLabels(
            ["No.", "Composition", "Molar mass (g/mol)"]
        )
        self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.history.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.history.setAlternatingRowColors(True)
        self.history.verticalHeader().setVisible(False)
        self.history.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.history.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.history.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        history_layout.addWidget(self.history, 1)
        history_buttons = QHBoxLayout()
        self.delete_history_button = QPushButton("Delete Selected")
        self.clear_history_button = QPushButton("Clear History")
        history_buttons.addWidget(self.delete_history_button)
        history_buttons.addWidget(self.clear_history_button)
        history_buttons.addStretch(1)
        history_layout.addLayout(history_buttons)
        layout.addWidget(history_group, 1)
        self.setCentralWidget(central)

        self.calculate_button.clicked.connect(self.calculate)
        self.formula.returnPressed.connect(self.calculate)
        self.delete_history_button.clicked.connect(self.delete_selected_history)
        self.clear_history_button.clicked.connect(
            lambda _checked=False: self.history.setRowCount(0)
        )

    def calculate(self) -> None:
        """Calculate and display molar mass using the shared MPMS parser."""
        formula = self.formula.text().strip()
        try:
            molar_mass = calculate_molar_mass(formula)
        except ValueError as exc:
            self.result.setText(f"Error: {exc}")
            return
        self.result.setText(f"Molar mass: {molar_mass:.8g} g/mol")
        row = self.history.rowCount()
        self.history.insertRow(row)
        for column, value in enumerate(
            (str(row + 1), formula, f"{molar_mass:.10g}")
        ):
            item = QTableWidgetItem(value)
            if column != 1:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history.setItem(row, column, item)
        self.history.scrollToBottom()

    def delete_selected_history(self) -> None:
        """Delete selected calculations and keep the remaining order explicit."""
        rows = sorted(
            {index.row() for index in self.history.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self.history.removeRow(row)
        self._renumber_history()

    def _renumber_history(self) -> None:
        for row in range(self.history.rowCount()):
            item = self.history.item(row, 0)
            if item is not None:
                item.setText(str(row + 1))


class ShannonRadiiWindow(ToolWindow):
    """Browse pymatgen's environment-specific Shannon-radius table."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Shannon Radii", parent)
        self.resize(1220, 820)
        central = QWidget()
        layout = QVBoxLayout(central)
        source = QLabel(
            "Select an element. Values are read from pymatgen's Shannon radii "
            "dataset and are reported in Å. Pauling electronegativity is shown "
            "with the selected element."
        )
        source.setWordWrap(True)
        layout.addWidget(source)

        periodic_widget = QWidget()
        periodic = QGridLayout(periodic_widget)
        periodic.setSpacing(3)
        self.element_buttons: dict[str, QPushButton] = {}
        self.element_button_group = QButtonGroup(self)
        self.element_button_group.setExclusive(True)
        legend = QHBoxLayout()
        legend.addWidget(QLabel("Element blocks:"))
        for block in ("s", "p", "d", "f"):
            block_label = QLabel(f" {block} block ")
            block_label.setProperty("elementBlock", block)
            block_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            legend.addWidget(block_label)
        legend.addStretch(1)
        layout.addLayout(legend)
        self._populate_periodic_table(periodic)
        periodic_scroll = QScrollArea()
        periodic_scroll.setWidgetResizable(True)
        periodic_scroll.setWidget(periodic_widget)
        periodic_scroll.setMinimumHeight(440)
        layout.addWidget(periodic_scroll)

        self.selection_label = QLabel("Element: —")
        self.selection_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        selection_group = QGroupBox("Selected element")
        selection_layout = QVBoxLayout(selection_group)
        selection_layout.addWidget(self.selection_label)
        layout.addWidget(selection_group)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [
                "Oxidation state",
                "Coordination",
                "Spin state",
                "Ionic radius (Å)",
                "Crystal radius (Å)",
            ]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        header = self.table.horizontalHeader()
        for column in range(3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(3, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        table_group = QGroupBox("Shannon radii")
        table_layout = QVBoxLayout(table_group)
        table_layout.addWidget(self.table)
        layout.addWidget(table_group, 1)
        self.setCentralWidget(central)

    def _populate_periodic_table(self, layout: QGridLayout) -> None:
        from pymatgen.core import Element

        for group in range(1, 19):
            heading = QLabel(str(group))
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(heading, 0, group)
        for period in range(1, 8):
            heading = QLabel(str(period))
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(heading, period, 0)
        layout.addWidget(QLabel("Lanthanides"), 8, 0, 1, 3)
        layout.addWidget(QLabel("Actinides"), 9, 0, 1, 3)

        for atomic_number in range(1, 119):
            element = Element.from_Z(atomic_number)
            if 57 <= atomic_number <= 71:
                row, column = 8, atomic_number - 54
            elif 89 <= atomic_number <= 103:
                row, column = 9, atomic_number - 86
            else:
                group = element.group
                if group is None:
                    continue
                row, column = int(element.row), int(group)
            block = (
                "f"
                if 57 <= atomic_number <= 71 or 89 <= atomic_number <= 103
                else "s"
                if atomic_number == 2
                else str(element.block)
            )
            button = QPushButton(f"{atomic_number}\n{element.symbol}")
            button.setFixedSize(50, 42)
            button.setCheckable(True)
            button.setProperty("elementBlock", block)
            button.setToolTip(f"{atomic_number}: {element.long_name}")
            button.clicked.connect(
                lambda _checked=False, symbol=element.symbol: self.select_element(symbol)
            )
            layout.addWidget(button, row, column)
            self.element_button_group.addButton(button)
            self.element_buttons[element.symbol] = button

    def select_element(self, symbol: str) -> None:
        """Display every Shannon-radius entry available for one element."""
        from pymatgen.core import Element

        element = Element(symbol)
        rows = shannon_radii_for_element(symbol)
        button = self.element_buttons.get(symbol)
        if button is not None:
            button.setChecked(True)
        block = (
            "f"
            if 57 <= element.Z <= 71 or 89 <= element.Z <= 103
            else "s"
            if element.Z == 2
            else str(element.block)
        )
        electronegativity = element.data.get("X")
        electronegativity_text = (
            f"{float(electronegativity):.3g}"
            if isinstance(electronegativity, (int, float))
            else "not available"
        )
        self.selection_label.setText(
            f"{element.long_name} ({symbol}, Z={element.Z})  |  "
            f"Block: {block}  |  Pauling electronegativity: "
            f"{electronegativity_text}  |  Shannon entries: {len(rows)}"
        )
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_index, radius in enumerate(rows):
            oxidation = (
                f"{radius.oxidation_state:+d}"
                if radius.oxidation_state
                else "0"
            )
            values = (
                oxidation,
                radius.coordination,
                radius.spin,
                f"{radius.ionic_radius_a:.6g}",
                f"{radius.crystal_radius_a:.6g}",
            )
            for column, value in enumerate(values):
                item = (
                    RomanNumeralTableItem(value)
                    if column == 1
                    else QTableWidgetItem(value)
                )
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_index, column, item)
        self.table.setSortingEnabled(True)


class RasxConverterWindow(ToolWindow):
    """Convert multiple RASX profiles to two-column XY or DAT files."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("RASX Converter", parent)
        self.resize(780, 640)
        self.setAcceptDrops(True)
        central = QWidget()
        layout = QVBoxLayout(central)
        description = QLabel(
            "Batch-convert Data0/Profile0.txt from each RASX archive. "
            "Only the first two columns (2θ and Intensity) are written."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        files_group = QGroupBox("Input RASX files")
        files_layout = QVBoxLayout(files_group)
        drop_hint = QLabel("Drop one or more .rasx files here, or use Add Files.")
        drop_hint.setProperty("dropHint", True)
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        files_layout.addWidget(drop_hint)
        self.files = QListWidget()
        self.files.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        files_layout.addWidget(self.files)
        file_buttons = QHBoxLayout()
        self.add_button = QPushButton("Add Files…")
        self.remove_button = QPushButton("Remove Selected")
        self.clear_button = QPushButton("Clear")
        file_buttons.addWidget(self.add_button)
        file_buttons.addWidget(self.remove_button)
        file_buttons.addWidget(self.clear_button)
        file_buttons.addStretch(1)
        files_layout.addLayout(file_buttons)
        layout.addWidget(files_group, 1)

        options = QGroupBox("Conversion")
        form = QFormLayout(options)
        self.output_format = QComboBox()
        self.output_format.addItem("XY file (*.xy)", "xy")
        self.output_format.addItem("DAT file (*.dat)", "dat")
        form.addRow("Output format", self.output_format)
        directory_row = QHBoxLayout()
        self.output_directory = QLineEdit()
        self.output_directory.setPlaceholderText(
            "Same directory as each source file (default)"
        )
        self.browse_directory_button = QPushButton("Browse…")
        directory_row.addWidget(self.output_directory, 1)
        directory_row.addWidget(self.browse_directory_button)
        form.addRow("Output directory", directory_row)
        self.overwrite = QCheckBox("Overwrite existing output files")
        form.addRow(self.overwrite)
        self.convert_button = QPushButton("Convert All")
        self.convert_button.setProperty("primary", True)
        form.addRow(self.convert_button)
        layout.addWidget(options)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Conversion results will appear here.")
        self.log.setMaximumBlockCount(1000)
        layout.addWidget(self.log, 1)
        self.setCentralWidget(central)

        self.add_button.clicked.connect(self.choose_files)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.files.clear)
        self.browse_directory_button.clicked.connect(self.choose_output_directory)
        self.convert_button.clicked.connect(self.convert_all)

    @staticmethod
    def _dropped_rasx_paths(event) -> list[Path]:  # type: ignore[no-untyped-def]
        return [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
            and Path(url.toLocalFile()).is_file()
            and Path(url.toLocalFile()).suffix.casefold() == ".rasx"
        ]

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """Accept local RASX files dragged from the operating system."""
        if event.mimeData().hasUrls() and self._dropped_rasx_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """Add every valid dropped RASX path without starting conversion."""
        paths = self._dropped_rasx_paths(event)
        if not paths:
            event.ignore()
            return
        before = self.files.count()
        self.add_files(paths)
        added = self.files.count() - before
        self.log.appendPlainText(f"Added {added} RASX file(s) by drag and drop.")
        event.acceptProposedAction()

    def add_files(self, paths: list[Path]) -> None:
        """Add unique RASX paths while preserving the selected order."""
        existing = {
            str(self.files.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.files.count())
        }
        for path in paths:
            normalized = str(Path(path).resolve())
            if normalized in existing:
                continue
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(normalized)
            item.setData(Qt.ItemDataRole.UserRole, normalized)
            self.files.addItem(item)
            existing.add(normalized)

    def choose_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "Select RASX Profiles",
            "",
            "Rigaku RASX (*.rasx);;All files (*)",
        )
        self.add_files([Path(name) for name in names])

    def remove_selected(self) -> None:
        for item in self.files.selectedItems():
            self.files.takeItem(self.files.row(item))

    def choose_output_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Output Directory",
            self.output_directory.text(),
        )
        if directory:
            self.output_directory.setText(directory)

    def convert_all(self) -> None:
        """Convert each selected archive and retain per-file error accounting."""
        if self.files.count() == 0:
            self.log.appendPlainText("No RASX files are selected.")
            return
        output_text = self.output_directory.text().strip()
        output_directory = Path(output_text) if output_text else None
        succeeded = 0
        failed = 0
        for row in range(self.files.count()):
            source = Path(
                str(self.files.item(row).data(Qt.ItemDataRole.UserRole))
            )
            try:
                options = {"output_format": str(self.output_format.currentData()), "output_directory": output_directory,
                           "overwrite": self.overwrite.isChecked()}
                result = blocking_task(self, f"Convert {source.name}", lambda current=source, config=options: convert_rasx_file(current, **config), allow_cancel=False)
            except (OSError, ValueError) as exc:
                failed += 1
                self.log.appendPlainText(f"FAILED  {source.name}: {exc}")
                continue
            succeeded += 1
            self.log.appendPlainText(
                f"OK  {source.name} → {result.destination} "
                f"({result.point_count} points)"
            )
        self.log.appendPlainText(
            f"Completed: {succeeded} succeeded, {failed} failed."
        )
