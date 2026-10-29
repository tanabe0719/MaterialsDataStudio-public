"""Application and interactive-plot appearance preferences."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from materials_data_studio.plotting.appearance import (
    DEFAULT_PLOT_APPEARANCE,
    PlotAppearance,
    PlotThemeColors,
)
from materials_data_studio.ui.numeric_inputs import FlexibleDoubleSpinBox


class ColorButton(QPushButton):
    """Compact color selector that shows both a swatch and hexadecimal value."""

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self.clicked.connect(self._choose_color)
        self._update_display()

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        self._color = QColor(color).name().upper()
        self._update_display()

    def _choose_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._color), self, "Select plot color")
        if selected.isValid():
            self.set_color(selected.name())

    def _update_display(self) -> None:
        foreground = "#FFFFFF" if QColor(self._color).lightnessF() < 0.48 else "#111827"
        self.setText(self._color)
        self.setStyleSheet(
            f"background-color: {self._color}; color: {foreground}; font-weight: 600;"
        )


class PreferencesDialog(QDialog):
    """Edit persistent theme and the complete interactive-plot appearance."""

    defaultsReset = Signal()
    applyRequested = Signal()

    def __init__(
        self,
        theme: str,
        appearance: PlotAppearance,
        parent: QWidget | None = None,
        *,
        igor_executable: str = "",
        confirm_reset: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.resize(700, 680)
        self.setMinimumSize(620, 560)
        self._confirm_reset = confirm_reset or self._ask_reset

        layout = QVBoxLayout(self)
        application_row = QHBoxLayout()
        application_row.addWidget(QLabel("Application color theme"))
        self.theme = QComboBox()
        self.theme.addItem("Light", "light")
        self.theme.addItem("Dark", "dark")
        self.theme.setCurrentIndex(max(0, self.theme.findData(theme)))
        application_row.addWidget(self.theme)
        from materials_data_studio.core.settings import create_settings
        application_row.addWidget(QLabel("UI language"))
        self.language = QComboBox()
        self.language.addItem("English", "en")
        self.language.addItem("日本語", "ja")
        self.language.setCurrentIndex(max(0, self.language.findData(str(create_settings().value("appearance/language", "en")))))
        application_row.addWidget(self.language)
        application_row.addStretch(1)
        layout.addLayout(application_row)

        self.plot_tabs = QTabWidget()
        self.plot_tabs.tabBar().setProperty("navigation", "dialog")
        layout.addWidget(self.plot_tabs, 1)
        self._add_elements_tab(appearance)
        self._add_ticks_tab(appearance)
        self._add_text_lines_tab(appearance)

        self._color_buttons: dict[str, dict[str, ColorButton]] = {}
        self._add_color_tab("light", "Light colors", appearance.light)
        self._add_color_tab("dark", "Dark colors", appearance.dark)
        self._add_applications_tab(igor_executable)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Reset
            | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        reset_button = self.buttons.button(QDialogButtonBox.StandardButton.Reset)
        if reset_button is not None:
            reset_button.setText("Reset to Defaults")
            reset_button.clicked.connect(self._reset)
        apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        if apply_button is not None:
            apply_button.clicked.connect(self.applyRequested.emit)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @staticmethod
    def _scroll_page(content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _checked(text: str, value: bool) -> QCheckBox:
        control = QCheckBox(text)
        control.setChecked(value)
        return control

    def _add_elements_tab(self, appearance: PlotAppearance) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        content = QGroupBox("Visible plot elements")
        form = QFormLayout(content)
        self.show_title = self._checked("Show plot title", appearance.show_title)
        self.show_axis_labels = self._checked("Show axis labels", appearance.show_axis_labels)
        self.show_tick_labels = self._checked("Show tick labels", appearance.show_tick_labels)
        self.show_legend = self._checked("Allow legend when enabled", appearance.show_legend)
        self.show_grid = self._checked("Allow grid when enabled", appearance.show_grid)
        self.show_left_axis = self._checked("Show left frame", appearance.show_left_axis)
        self.show_bottom_axis = self._checked("Show bottom frame", appearance.show_bottom_axis)
        self.show_top_axis = self._checked("Show top frame", appearance.show_top_axis)
        self.show_right_axis = self._checked("Show right frame", appearance.show_right_axis)
        for control in (
            self.show_title,
            self.show_axis_labels,
            self.show_tick_labels,
            self.show_legend,
            self.show_grid,
            self.show_left_axis,
            self.show_bottom_axis,
            self.show_top_axis,
            self.show_right_axis,
        ):
            form.addRow(control)
        layout.addWidget(content)
        layout.addStretch(1)
        self.plot_tabs.addTab(page, "Elements")

    def _add_ticks_tab(self, appearance: PlotAppearance) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        visibility = QGroupBox("Tick visibility")
        visibility_form = QFormLayout(visibility)
        self.show_major_ticks = self._checked("Show major ticks", appearance.show_major_ticks)
        self.show_minor_ticks = self._checked("Show minor ticks", appearance.show_minor_ticks)
        visibility_form.addRow(self.show_major_ticks)
        visibility_form.addRow(self.show_minor_ticks)
        layout.addWidget(visibility)

        spacing = QGroupBox("Fixed spacing (0 = automatic)")
        spacing_form = QFormLayout(spacing)
        self.x_major_tick_interval = self._interval_control(appearance.x_major_tick_interval)
        self.x_minor_tick_interval = self._interval_control(appearance.x_minor_tick_interval)
        self.y_major_tick_interval = self._interval_control(appearance.y_major_tick_interval)
        self.y_minor_tick_interval = self._interval_control(appearance.y_minor_tick_interval)
        spacing_form.addRow("X major interval", self.x_major_tick_interval)
        spacing_form.addRow("X minor interval", self.x_minor_tick_interval)
        spacing_form.addRow("Y major interval", self.y_major_tick_interval)
        spacing_form.addRow("Y minor interval", self.y_minor_tick_interval)
        layout.addWidget(spacing)
        note = QLabel(
            "Automatic spacing adapts to zoom. Fixed intervals use the displayed linear axis units; "
            "logarithmic axes retain automatic spacing."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        self.plot_tabs.addTab(page, "Ticks")

    def _add_text_lines_tab(self, appearance: PlotAppearance) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.axis_label_font_size = self._integer_control(6, 32, appearance.axis_label_font_size)
        self.title_font_size = self._integer_control(6, 32, appearance.title_font_size)
        self.tick_label_font_size = self._integer_control(6, 32, appearance.tick_label_font_size)
        self.legend_font_size = self._integer_control(6, 32, appearance.legend_font_size)
        self.frame_width = self._decimal_control(0.25, 6.0, appearance.frame_width, " pt")
        self.tick_width = self._decimal_control(0.25, 6.0, appearance.tick_width, " pt")
        self.tick_length = self._decimal_control(0.0, 30.0, appearance.tick_length, " px")
        self.minor_tick_length = self._decimal_control(
            0.0, 30.0, appearance.minor_tick_length, " px"
        )
        self.grid_width = self._decimal_control(0.1, 6.0, appearance.grid_width, " pt")
        self.grid_opacity = self._decimal_control(0.0, 1.0, appearance.grid_opacity, "")
        form.addRow("Title size", self.title_font_size)
        form.addRow("Axis-label size", self.axis_label_font_size)
        form.addRow("Tick-label size", self.tick_label_font_size)
        form.addRow("Legend size", self.legend_font_size)
        form.addRow("Frame width", self.frame_width)
        form.addRow("Tick width", self.tick_width)
        form.addRow("Major-tick length", self.tick_length)
        form.addRow("Minor-tick length", self.minor_tick_length)
        form.addRow("Grid width", self.grid_width)
        form.addRow("Grid opacity", self.grid_opacity)
        self.plot_tabs.addTab(page, "Text & lines")

    @staticmethod
    def _integer_control(minimum: int, maximum: int, value: int) -> QSpinBox:
        control = QSpinBox()
        control.setRange(minimum, maximum)
        control.setSuffix(" pt")
        control.setValue(value)
        return control

    @staticmethod
    def _decimal_control(
        minimum: float, maximum: float, value: float, suffix: str
    ) -> FlexibleDoubleSpinBox:
        control = FlexibleDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setDecimals(2)
        control.setSingleStep(0.25)
        control.setSuffix(suffix)
        control.setValue(value)
        return control

    @staticmethod
    def _interval_control(value: float) -> FlexibleDoubleSpinBox:
        control = FlexibleDoubleSpinBox()
        control.setRange(0.0, 1.0e12)
        control.setDecimals(6)
        control.setSingleStep(0.1)
        control.setSpecialValueText("Automatic")
        control.setValue(value)
        return control

    def _add_color_tab(self, key: str, title: str, colors: PlotThemeColors) -> None:
        page = QWidget()
        form = QFormLayout(page)
        buttons = {
            "background": ColorButton(colors.background),
            "title": ColorButton(colors.title),
            "axis_label": ColorButton(colors.axis_label),
            "tick_label": ColorButton(colors.tick_label),
            "legend_text": ColorButton(colors.legend_text),
            "frame": ColorButton(colors.frame),
            "tick": ColorButton(colors.tick),
            "grid": ColorButton(colors.grid),
            "legend_background": ColorButton(colors.legend_background),
            "legend_border": ColorButton(colors.legend_border),
        }
        labels = {
            "background": "Plot background",
            "title": "Title",
            "axis_label": "Axis labels",
            "tick_label": "Tick labels",
            "legend_text": "Legend text",
            "frame": "Frame",
            "tick": "Ticks",
            "grid": "Grid",
            "legend_background": "Legend background",
            "legend_border": "Legend border",
        }
        for field_name, button in buttons.items():
            form.addRow(labels[field_name], button)
        self._color_buttons[key] = buttons
        self.plot_tabs.addTab(self._scroll_page(page), title)

    def _add_applications_tab(self, igor_executable: str) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("Igor Pro 10")
        form = QFormLayout(group)
        executable_row = QHBoxLayout()
        self.igor_executable = QLineEdit(igor_executable)
        self.igor_executable.setPlaceholderText(
            "Not configured — use the Windows .h5xp file association"
        )
        self.igor_executable.setClearButtonEnabled(True)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_igor_executable)
        executable_row.addWidget(self.igor_executable, 1)
        executable_row.addWidget(browse)
        form.addRow("Igor executable", executable_row)
        note = QLabel(
            "Select Igor64.exe from the Igor Pro 10 installation. If left empty, "
            "Send to Igor Pro uses the Windows association for .h5xp files."
        )
        note.setWordWrap(True)
        form.addRow(note)
        layout.addWidget(group)
        import sys
        if sys.platform == "win32":
            from materials_data_studio.integrations.windows_files import (
                open_default_apps_settings,
                register_document_types,
            )
            files = QGroupBox("MDS document files (.mdss / .mdsg)")
            file_layout = QVBoxLayout(files)
            register = QPushButton("Register MDS / open Windows Default Apps")
            def register_files() -> None:
                try:
                    register_document_types()
                    open_default_apps_settings()
                except OSError as exc:
                    QMessageBox.warning(self, "File associations", str(exc))
            register.clicked.connect(register_files)
            file_layout.addWidget(register)
            note = QLabel("Select Materials Data Studio as the default for .mdss and .mdsg in Windows Settings. Each document type has its own icon.")
            note.setWordWrap(True)
            file_layout.addWidget(note)
            layout.addWidget(files)
        layout.addStretch(1)
        self.plot_tabs.addTab(page, "Applications")

    def _browse_igor_executable(self) -> None:
        name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Select Igor Pro Executable",
            self.igor_executable.text(),
            "Igor Pro (Igor64.exe Igor.exe);;Applications (*.exe);;All files (*)",
        )
        if name:
            self.igor_executable.setText(name)

    def selected_theme(self) -> str:
        return str(self.theme.currentData())

    def igor_executable_path(self) -> str:
        return self.igor_executable.text().strip()

    def plot_appearance(self) -> PlotAppearance:
        return PlotAppearance(
            axis_label_font_size=self.axis_label_font_size.value(),
            title_font_size=self.title_font_size.value(),
            tick_label_font_size=self.tick_label_font_size.value(),
            legend_font_size=self.legend_font_size.value(),
            frame_width=self.frame_width.value(),
            tick_width=self.tick_width.value(),
            tick_length=self.tick_length.value(),
            minor_tick_length=self.minor_tick_length.value(),
            grid_width=self.grid_width.value(),
            grid_opacity=self.grid_opacity.value(),
            show_title=self.show_title.isChecked(),
            show_axis_labels=self.show_axis_labels.isChecked(),
            show_tick_labels=self.show_tick_labels.isChecked(),
            show_legend=self.show_legend.isChecked(),
            show_grid=self.show_grid.isChecked(),
            show_left_axis=self.show_left_axis.isChecked(),
            show_bottom_axis=self.show_bottom_axis.isChecked(),
            show_top_axis=self.show_top_axis.isChecked(),
            show_right_axis=self.show_right_axis.isChecked(),
            show_major_ticks=self.show_major_ticks.isChecked(),
            show_minor_ticks=self.show_minor_ticks.isChecked(),
            x_major_tick_interval=self.x_major_tick_interval.value(),
            x_minor_tick_interval=self.x_minor_tick_interval.value(),
            y_major_tick_interval=self.y_major_tick_interval.value(),
            y_minor_tick_interval=self.y_minor_tick_interval.value(),
            light=self._theme_colors("light"),
            dark=self._theme_colors("dark"),
        )

    def _theme_colors(self, theme: str) -> PlotThemeColors:
        buttons = self._color_buttons[theme]
        return PlotThemeColors(**{name: button.color() for name, button in buttons.items()})

    def _set_controls(self, appearance: PlotAppearance) -> None:
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
        ):
            getattr(self, field_name).setChecked(getattr(appearance, field_name))
        for field_name in (
            "axis_label_font_size",
            "title_font_size",
            "tick_label_font_size",
            "legend_font_size",
            "frame_width",
            "tick_width",
            "tick_length",
            "minor_tick_length",
            "grid_width",
            "grid_opacity",
            "x_major_tick_interval",
            "x_minor_tick_interval",
            "y_major_tick_interval",
            "y_minor_tick_interval",
        ):
            getattr(self, field_name).setValue(getattr(appearance, field_name))
        for theme, colors in (("light", appearance.light), ("dark", appearance.dark)):
            for field_name, button in self._color_buttons[theme].items():
                button.set_color(getattr(colors, field_name))

    def _reset(self) -> None:
        if not self._confirm_reset():
            return
        self.theme.setCurrentIndex(self.theme.findData("light"))
        self.language.setCurrentIndex(self.language.findData("en"))
        self._set_controls(DEFAULT_PLOT_APPEARANCE)
        self.igor_executable.clear()
        self.defaultsReset.emit()

    def _ask_reset(self) -> bool:
        return (
            QMessageBox.question(
                self,
                "Reset Preferences",
                "Reset the application theme, plot settings, and external application paths "
                "to defaults?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )
