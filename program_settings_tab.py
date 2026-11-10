"""
ProgramSettingsTab: settings for the program itself (as opposed to the TTS
or STT settings). For now just the color theme (Light / Dark), saved under
"THEME" in the shared settings.json. Save / Load / Reset buttons and the
status line sit in the same place, and look the same, as on the TTS and
STT settings tabs.
"""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QComboBox,
    QPushButton, QMessageBox, QFileDialog,
)

from ui_common import (
    DEFAULT_SETTINGS_PATH, STATUS_STYLE_PROGRAM,
    COLOR_DARK_GREEN, COLOR_DARK_RED, COLOR_DARK_BLUE,
    STANDARD_BTN_HEIGHT, btn_style, print_error, load_json_dict,
    save_json_merged, ElidingLabel, THEMES, DEFAULT_THEME,
    apply_theme, current_theme,
)

_KEY_THEME = "THEME"


class ProgramSettingsTab(QWidget):
    settings_loaded = Signal(Path)  # emitted after a manual "Load Settings…", matching the other settings tabs

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._try_autoload()

    def _build_ui(self):
        outer = QVBoxLayout(self)

        appearance_group = QGroupBox("Appearance")
        form = QFormLayout(appearance_group)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(THEMES)
        self.theme_combo.setMaximumWidth(120)
        self.theme_combo.setToolTip(
            "Color theme. Applied immediately; use Save Settings to keep it for next start."
        )
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        form.addRow("Theme:", self.theme_combo)
        outer.addWidget(appearance_group)

        # Pushes the groups to the top, so the buttons below end up at the
        # bottom of the tab like on the other settings tabs.
        outer.addStretch(1)

        # -- Save / Load / Reset (same order, colors and position as TTS/STT Settings) --
        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("💾 Save Settings")
        self.save_btn.setStyleSheet(btn_style(COLOR_DARK_GREEN))
        self.load_btn = QPushButton("📂 Load Settings…")
        self.load_btn.setStyleSheet(btn_style(COLOR_DARK_BLUE))
        self.reset_btn = QPushButton("Reset to Defaults")
        self.reset_btn.setStyleSheet(btn_style(COLOR_DARK_RED))
        for btn in (self.save_btn, self.load_btn, self.reset_btn):
            btn.setFixedHeight(STANDARD_BTN_HEIGHT)
        self.save_btn.clicked.connect(self._on_save_clicked)
        self.load_btn.clicked.connect(self._on_load_clicked)
        self.reset_btn.clicked.connect(self._on_reset_clicked)
        btn_row.addWidget(self.save_btn, 1)
        btn_row.addWidget(self.load_btn, 1)
        btn_row.addWidget(self.reset_btn, 1)
        outer.addLayout(btn_row)

        self.settings_status = ElidingLabel("")
        self.settings_status.setStyleSheet(STATUS_STYLE_PROGRAM)
        outer.addWidget(self.settings_status)

    # ---- gathering / populating ---------------------------------------------

    def get_theme(self) -> str:
        return self.theme_combo.currentText()

    def _gather_settings(self) -> dict:
        return {_KEY_THEME: self.get_theme()}

    def _populate_fields(self, settings: dict):
        """Unknown or missing theme values leave the combo as it was."""
        theme = settings.get(_KEY_THEME)
        idx = self.theme_combo.findText(theme) if isinstance(theme, str) else -1
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)

    def _on_theme_changed(self, theme: str):
        apply_theme(theme)

    # ---- save / load / reset ------------------------------------------------

    def _try_autoload(self):
        if DEFAULT_SETTINGS_PATH.exists():
            try:
                self._populate_fields(load_json_dict(DEFAULT_SETTINGS_PATH))
                # main() normally has applied the saved theme already; this
                # covers the combo showing a theme the app isn't using yet.
                if current_theme() != self.get_theme():
                    apply_theme(self.get_theme())
                self.settings_status.setText(f"Auto-loaded program settings from {DEFAULT_SETTINGS_PATH}")
            except (OSError, ValueError) as e:
                message = f"Couldn't auto-load program settings: {e}"
                self.settings_status.setText(message)
                print_error(message)

    def _on_save_clicked(self):
        try:
            save_json_merged(DEFAULT_SETTINGS_PATH, self._gather_settings())
            self.settings_status.setText(f"Saved program settings to {DEFAULT_SETTINGS_PATH}")
        except OSError as e:
            print_error(f"Couldn't save program settings: {e}")
            QMessageBox.warning(self, "Couldn't save settings", str(e))

    def _on_load_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load settings", str(DEFAULT_SETTINGS_PATH.parent), "JSON (*.json)"
        )
        if not path:
            return
        try:
            self._populate_fields(load_json_dict(Path(path)))
            self.settings_status.setText(f"Loaded program settings from {path}")
            self.settings_loaded.emit(Path(path))
        except (OSError, ValueError) as e:
            print_error(f"Couldn't load program settings: {e}")
            QMessageBox.warning(self, "Couldn't load settings", str(e))

    def _on_reset_clicked(self):
        self._populate_fields({_KEY_THEME: DEFAULT_THEME})
        self.settings_status.setText("Reset to built-in defaults")
