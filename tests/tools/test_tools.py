from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt, QUrl

from materials_data_studio.tools.rasx_converter import convert_rasx_file
from materials_data_studio.tools.shannon import (
    roman_numeral_value,
    shannon_radii_for_element,
)


def _write_rasx(path: Path, rows: str = "1.5\t10\t1\n2.5\t20\t1\n") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Data0/Profile0.txt", rows)


def test_rasx_converter_defaults_to_xy_beside_source(tmp_path: Path) -> None:
    source = tmp_path / "sample.rasx"
    _write_rasx(source)
    result = convert_rasx_file(source)
    assert result.destination == tmp_path / "sample.xy"
    assert result.point_count == 2
    converted = np.loadtxt(result.destination)
    np.testing.assert_allclose(converted, [[1.5, 10.0], [2.5, 20.0]])


def test_rasx_converter_supports_dat_output_and_protects_existing_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sample.rasx"
    output = tmp_path / "converted"
    output.mkdir()
    _write_rasx(source)
    first = convert_rasx_file(
        source,
        output_format="dat",
        output_directory=output,
    )
    assert first.destination.suffix == ".dat"
    with pytest.raises(FileExistsError):
        convert_rasx_file(
            source,
            output_format="dat",
            output_directory=output,
        )
    convert_rasx_file(
        source,
        output_format="dat",
        output_directory=output,
        overwrite=True,
    )


def test_shannon_radii_are_loaded_from_pymatgen_by_environment() -> None:
    rows = shannon_radii_for_element("Fe")
    assert rows
    assert any(
        row.oxidation_state == 2
        and row.coordination == "VI"
        and row.spin == "High Spin"
        and row.ionic_radius_a == pytest.approx(0.78)
        for row in rows
    )


def test_tools_launch_non_modal_multiple_windows_and_calculate(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QMainWindow

    from materials_data_studio.tools.launcher import ToolMenuManager
    from materials_data_studio.tools.windows import MolarMassWindow, ShannonRadiiWindow

    owner = QMainWindow()
    qtbot.addWidget(owner)
    tools_menu = owner.menuBar().addMenu("Tools")
    manager = ToolMenuManager(tools_menu, owner)
    owner.show()
    assert [action.text() for action in tools_menu.actions()] == [
        "Molecular Weight Calculator",
        "Shannon Radii",
        "RASX Converter",
        "Graph Editor / Igor Import",
    ]
    assert not any(
        word in action.text()
        for action in tools_menu.actions()
        for word in ("Available", "Planned")
    )
    first = manager.open_tool("molar_mass")
    second = manager.open_tool("molar_mass")
    qtbot.addWidget(first)
    qtbot.addWidget(second)
    assert first is not second
    assert first.windowModality() == Qt.WindowModality.NonModal
    assert second.windowModality() == Qt.WindowModality.NonModal
    assert first.parent() is None
    assert second.parent() is None
    assert owner.isEnabled()
    assert len(manager.open_windows) == 2

    assert isinstance(first, MolarMassWindow)
    first.formula.setText("CuSO4·5H2O")
    first.calculate()
    assert "249.677" in first.result.text()
    first.formula.setText("NaCl")
    first.calculate()
    assert first.history.rowCount() == 2
    assert first.history.item(0, 1).text() == "CuSO4·5H2O"
    assert first.history.item(1, 1).text() == "NaCl"
    first.history.selectRow(0)
    first.delete_selected_history()
    assert first.history.rowCount() == 1
    assert first.history.item(0, 0).text() == "1"
    assert first.history.item(0, 1).text() == "NaCl"

    shannon = manager.open_tool("shannon_radii")
    qtbot.addWidget(shannon)
    assert isinstance(shannon, ShannonRadiiWindow)
    assert len(shannon.element_buttons) == 118
    shannon.select_element("Fe")
    assert shannon.table.rowCount() > 0
    assert "Iron" in shannon.selection_label.text()
    assert "Pauling electronegativity: 1.83" in shannon.selection_label.text()
    assert shannon.element_buttons["Fe"].property("elementBlock") == "d"
    assert shannon.element_buttons["La"].property("elementBlock") == "f"
    assert shannon.element_buttons["He"].property("elementBlock") == "s"
    assert shannon.element_buttons["Fe"].isChecked()
    assert shannon.table.alternatingRowColors()
    assert not shannon.table.verticalHeader().isVisible()
    shannon.table.sortItems(1, Qt.SortOrder.AscendingOrder)
    coordinations = [
        shannon.table.item(row, 1).text()
        for row in range(shannon.table.rowCount())
    ]
    assert [roman_numeral_value(value) for value in coordinations] == sorted(
        roman_numeral_value(value) for value in coordinations
    )


def test_rasx_converter_window_batches_selected_files(
    qtbot, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.tools.windows import RasxConverterWindow

    first = tmp_path / "first.rasx"
    second = tmp_path / "second.rasx"
    _write_rasx(first)
    _write_rasx(second, "3\t30\n4\t40\n")
    window = RasxConverterWindow()
    qtbot.addWidget(window)
    window.add_files([first, second, first])
    assert window.files.count() == 2
    window.convert_all()
    assert (tmp_path / "first.xy").is_file()
    assert (tmp_path / "second.xy").is_file()
    assert "2 succeeded, 0 failed" in window.log.toPlainText()


def test_rasx_converter_accepts_dragged_rasx_files(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from materials_data_studio.tools.windows import RasxConverterWindow

    source = tmp_path / "dropped.rasx"
    ignored = tmp_path / "notes.txt"
    _write_rasx(source)
    ignored.write_text("not a profile")

    class FakeMimeData:
        def hasUrls(self) -> bool:
            return True

        def urls(self) -> list[QUrl]:
            return [QUrl.fromLocalFile(str(source)), QUrl.fromLocalFile(str(ignored))]

    class FakeDropEvent:
        def __init__(self) -> None:
            self.accepted = False

        def mimeData(self) -> FakeMimeData:
            return FakeMimeData()

        def acceptProposedAction(self) -> None:
            self.accepted = True

        def ignore(self) -> None:
            self.accepted = False

    window = RasxConverterWindow()
    qtbot.addWidget(window)
    event = FakeDropEvent()
    window.dragEnterEvent(event)
    assert event.accepted
    window.dropEvent(event)
    assert event.accepted
    assert window.files.count() == 1
    assert window.files.item(0).text() == source.name
    assert "Added 1 RASX file" in window.log.toPlainText()
