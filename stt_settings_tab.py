"""
STTSettingsTab: tunables for audio_chunker.py's silence detection, chunk
sizing, and subtitle segmentation, plus transcription options (language
code, suppress-non-speech, chunked mode) and a table of speaker-speed
presets (Name / Comma / Period / Paragraph / Target length / Range) that
can be edited and applied with one click -- similar in spirit to the Seed
Vault tab's table, but for chunking behaviour instead of TTS seeds.

Persists into the same shared settings.json as the other tabs, via
ui_common's save_json_merged/load_json_dict (merge-on-save, so this
tab's keys don't clobber SettingsTab's or SeedVaultTab's).
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFormLayout, QSpinBox,
    QDoubleSpinBox, QComboBox, QGroupBox, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView,
    QLineEdit, QCheckBox, QFileDialog, QScrollArea,
)

from audio_chunker import (
    ChunkerConfig, SUBTITLE_MAX_CHARS, SUBTITLE_LINGER_LONG_PAUSE_S,
)

from ui_common import (
    DEFAULT_SETTINGS_PATH, STATUS_STYLE_STT, COLOR_DARK_BLUE,
    COLOR_DARK_GREEN, COLOR_DARK_RED, COLOR_DARK_PURPLE,
    STANDARD_BTN_HEIGHT, btn_style, print_error, load_json_dict,
    save_json_merged, ElidingLabel,
)

# settings.json keys this tab owns.
_KEY_SCAN = "STT_SCAN"                       # VAD/amplitude/window/min-gap settings
_KEY_PRESETS = "STT_SPEAKER_PRESETS"         # list of preset dicts (the table)
_KEY_OPTIONS = "STT_OPTIONS"                 # langcode / suppress / chunked mode
_KEY_SUBTITLE = "STT_SUBTITLE_SEGMENTATION"  # subtitle segmentation group
_KEY_LINGER = "STT_SUBTITLE_LINGER"          # subtitle lingering group

_TABLE_COLUMNS = ["Name", "Comma (ms)", "Period (ms)", "Paragraph (ms)", "Target (s)", "Range (s)"]

# Shipped as a starting point -- editable/removable like any other row.
_FACTORY_PRESETS = [
    {"name": "Slow / deliberate speaker", "comma_ms": 300, "period_ms": 700, "paragraph_ms": 1500, "target_s": 15.0, "range_s": 5.0},
    {"name": "Average pace (default)",    "comma_ms": 250, "period_ms": 500, "paragraph_ms": 1200, "target_s": 15.0, "range_s": 5.0},
    {"name": "Fast talker / podcast",     "comma_ms": 180, "period_ms": 350, "paragraph_ms": 900,  "target_s": 10.0, "range_s": 4.0},
]


class STTSettingsTab(QWidget):
    settings_loaded = Signal(Path)  # emitted after a manual "Load Settings…", matching SettingsTab's pattern
    preset_changed = Signal(str)    # emitted whenever "Use with Transcriber" is clicked -- description string

    def __init__(self, parent=None):
        super().__init__(parent)
        self._building = True
        self._next_row_id = 0
        self._build_ui()
        self._populate_scan_fields(self._factory_scan_defaults())
        self._populate_options_fields(self._factory_options_defaults())
        self._populate_subtitle_fields(self._factory_subtitle_defaults())
        self._populate_linger_fields(self._factory_linger_defaults())
        self._populate_table(_FACTORY_PRESETS)
        if self.preset_table.rowCount() > 1:
            self._active_preset_id = self._row_id_at(1)
        self._building = False
        self._try_autoload()

    # ---- UI construction --------------------------------------------------

    def _build_ui(self):
        # Everything except the Save/Load/Reset row and status label lives
        # inside a scroll area -- this tab has grown a lot of groups, and
        # not every window will be tall enough to show them all at once.
        page_outer = QVBoxLayout(self)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)
        page_outer.addWidget(scroll_area, 1)

        content = QWidget()
        scroll_area.setWidget(content)
        outer = QVBoxLayout(content)

        # -- Transcription options (moved here from the STT tab) --
        options_group = QGroupBox("Transcription options")
        options_form = QFormLayout(options_group)

        self.langcode_edit = QLineEdit("en")
        self.langcode_edit.setToolTip(
            "Passed through to Whisper as-is, e.g. \"en\", \"no\", \"auto\" "
            "(exact accepted values depend on the loaded whisper model)."
        )
        self.langcode_edit.setFixedWidth(80)
        options_form.addRow("Language code:", self.langcode_edit)

        self.suppress_checkbox = QCheckBox("Suppress non-speech")
        self.suppress_checkbox.setToolTip(
            "Whisper's suppress_non_speech flag -- tries to drop tokens "
            "like [music] or [laughter] from the output."
        )
        options_form.addRow("", self.suppress_checkbox)

        self.chunk_checkbox = QCheckBox("Split into chunks (long audio)")
        self.chunk_checkbox.setChecked(True)
        self.chunk_checkbox.setToolTip(
            "Splits the file at silence boundaries before sending each piece "
            "to Whisper -- needed for anything over ~30s, and required for "
            "SRT export (per-chunk timestamps). Turn off only for quick "
            "single-shot tests on short clips."
        )
        options_form.addRow("", self.chunk_checkbox)

        outer.addWidget(options_group)

        # -- Silence detection (VAD) --
        scan_group = QGroupBox("Silence detection")
        scan_form = QFormLayout(scan_group)

        self.vad_type_combo = QComboBox()
        self.vad_type_combo.addItem("RMS (stdlib audioop)")
        self.vad_type_combo.setMaximumWidth(220)
        self.vad_type_combo.setToolTip(
            "Only one VAD implementation for now -- this is what audio_chunker.py "
            "actually does. A dropdown so more can be added later without "
            "reshuffling the rest of this tab."
        )
        scan_form.addRow("VAD type:", self.vad_type_combo)

        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(0, 32767)
        self.threshold_spin.setSingleStep(50)
        self.threshold_spin.setMaximumWidth(100)
        self.threshold_spin.setToolTip(
            "Raw RMS threshold (16-bit PCM, 0-32767). Below this counts as "
            "silence. Very recording-dependent -- tune by testing against "
            "your own files."
        )
        scan_form.addRow("Amplitude threshold:", self.threshold_spin)

        self.window_spin = QSpinBox()
        self.window_spin.setRange(5, 500)
        self.window_spin.setSingleStep(5)
        self.window_spin.setMaximumWidth(100)
        self.window_spin.setSuffix(" ms")
        self.window_spin.setToolTip("Size of each analysis window while scanning for silence.")
        scan_form.addRow("Analysis window:", self.window_spin)

        self.min_gap_spin = QSpinBox()
        self.min_gap_spin.setRange(0, 5000)
        self.min_gap_spin.setSingleStep(10)
        self.min_gap_spin.setMaximumWidth(100)
        self.min_gap_spin.setSuffix(" ms")
        self.min_gap_spin.setToolTip(
            "Minimum continuous below-threshold duration to count as a real "
            "pause at all -- shorter dips (a plosive consonant, a quick "
            "breath) are bridged over rather than treated as a gap."
        )
        scan_form.addRow("Minimum gap duration:", self.min_gap_spin)

        outer.addWidget(scan_group)

        # -- Subtitle segmentation --
        subtitle_group = QGroupBox("Subtitle segmentation")
        subtitle_form = QFormLayout(subtitle_group)

        self.subtitle_enabled_checkbox = QCheckBox("Split long chunks into multiple SRT entries")
        self.subtitle_enabled_checkbox.setChecked(True)
        self.subtitle_enabled_checkbox.setToolTip(
            "Splits each chunk's text into several shorter SRT entries sized "
            "for on-screen reading, instead of one long entry per chunk. "
            "Only affects Save SRT, not the plain-text box/save."
        )
        subtitle_form.addRow("", self.subtitle_enabled_checkbox)

        self.subtitle_snap_checkbox = QCheckBox("Prioritize detected pauses near split points")
        self.subtitle_snap_checkbox.setChecked(True)
        self.subtitle_snap_checkbox.setToolTip(
            "When splitting a chunk's text, snap the split's timing to a real "
            "detected pause if one exists close to where the split would "
            "otherwise land. If off, timing is always estimated purely from "
            "word-count proportion. Either way this is approximate -- "
            "KoboldCpp doesn't return real per-word timestamps."
        )
        subtitle_form.addRow("", self.subtitle_snap_checkbox)

        self.subtitle_max_chars_spin = QSpinBox()
        self.subtitle_max_chars_spin.setRange(20, 300)
        self.subtitle_max_chars_spin.setSingleStep(5)
        self.subtitle_max_chars_spin.setMaximumWidth(100)
        self.subtitle_max_chars_spin.setSuffix(" chars")
        self.subtitle_max_chars_spin.setToolTip(
            "Roughly how many characters (not words -- word length varies too "
            "much to be a good proxy for on-screen reading time) each SRT "
            "entry should target before it gets split again."
        )
        subtitle_form.addRow("Max entry length:", self.subtitle_max_chars_spin)

        outer.addWidget(subtitle_group)

        # -- Subtitle lingering --
        linger_group = QGroupBox("Subtitle lingering")
        linger_form = QFormLayout(linger_group)

        self.linger_enabled_checkbox = QCheckBox("Linger through pauses instead of going blank")
        self.linger_enabled_checkbox.setChecked(True)
        self.linger_enabled_checkbox.setToolTip(
            "Without this, a subtitle disappears the instant its chunk's "
            "speech ends and nothing shows again until the next one starts -- "
            "which flickers for short, ordinary pauses (a breath, a beat)."
        )
        linger_form.addRow("", self.linger_enabled_checkbox)

        self.linger_long_pause_spin = QSpinBox()
        self.linger_long_pause_spin.setRange(0, 10000)
        self.linger_long_pause_spin.setSingleStep(100)
        self.linger_long_pause_spin.setMaximumWidth(100)
        self.linger_long_pause_spin.setSuffix(" ms")
        self.linger_long_pause_spin.setToolTip(
            "Pauses linger this long past the original end before going blank, "
            "rather than cutting off instantly. The screen does go blank for "
            "the remainder of a genuinely long pause."
        )
        linger_form.addRow("Linger past pauses:", self.linger_long_pause_spin)

        outer.addWidget(linger_group)

        # -- Speaker-speed presets --
        table_group = QGroupBox("Speaker-speed presets")
        table_layout = QVBoxLayout(table_group)

        hint = QLabel(
            "Comma/Period/Paragraph are pause-tier durations (ms) used for text "
            "formatting; Paragraph also sets the \"trim this silence out "
            "entirely\" threshold. Target/Range (s) set the elastic chunk-length "
            "search window sent to Whisper. Select a row and click \"Use with "
            "Transcriber\" to apply it."
        )
        hint.setWordWrap(True)
        table_layout.addWidget(hint)

        self.preset_table = QTableWidget(0, len(_TABLE_COLUMNS))
        self.preset_table.setHorizontalHeaderLabels(_TABLE_COLUMNS)
        self.preset_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(_TABLE_COLUMNS)):
            self.preset_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.preset_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preset_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.preset_table.verticalHeader().setVisible(False)
        self.preset_table.setSortingEnabled(True)
        self.preset_table.horizontalHeader().setSortIndicatorShown(True)
        table_layout.addWidget(self.preset_table)

        table_btn_row = QHBoxLayout()
        self.add_row_btn = QPushButton("Add Row")
        self.add_row_btn.setStyleSheet(btn_style(COLOR_DARK_BLUE))
        self.add_row_btn.setFixedHeight(STANDARD_BTN_HEIGHT)
        self.add_row_btn.clicked.connect(self._on_add_row_clicked)
        table_btn_row.addWidget(self.add_row_btn)

        self.remove_row_btn = QPushButton("Remove Row")
        self.remove_row_btn.setStyleSheet(btn_style(COLOR_DARK_RED))
        self.remove_row_btn.setFixedHeight(STANDARD_BTN_HEIGHT)
        self.remove_row_btn.clicked.connect(self._on_remove_row_clicked)
        table_btn_row.addWidget(self.remove_row_btn)

        table_btn_row.addStretch(1)

        self.use_preset_btn = QPushButton("Use with Transcriber")
        self.use_preset_btn.setStyleSheet(btn_style(COLOR_DARK_PURPLE))
        self.use_preset_btn.setFixedHeight(STANDARD_BTN_HEIGHT)
        self.use_preset_btn.clicked.connect(self._on_use_preset_clicked)
        table_btn_row.addWidget(self.use_preset_btn)

        table_layout.addLayout(table_btn_row)
        outer.addWidget(table_group, 1)

        # -- Save / Load / Reset -- kept outside the scroll area, always visible.
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
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.load_btn)
        btn_row.addWidget(self.reset_btn)
        page_outer.addLayout(btn_row)

        self.settings_status = ElidingLabel("")
        self.settings_status.setStyleSheet(STATUS_STYLE_STT)
        page_outer.addWidget(self.settings_status)

        # The preset currently backing get_active_config(), tracked by a
        # stable per-row id (not row index) since the table is sortable and
        # row positions shift when the user clicks a column header.
        # Defaults to the "Average pace" preset once the table's populated;
        # _on_use_preset_clicked updates this on selection.
        self._active_preset_id = None

    # ---- gathering / populating: scan fields ------------------------------

    @staticmethod
    def _factory_scan_defaults() -> dict:
        c = ChunkerConfig()
        return {
            "vad_type": "RMS (stdlib audioop)",
            "amplitude_threshold": c.amplitude_threshold,
            "analysis_window_ms": c.analysis_window_ms,
            "min_gap_ms": c.min_gap_ms,
        }

    def _gather_scan_fields(self) -> dict:
        return {
            "vad_type": self.vad_type_combo.currentText(),
            "amplitude_threshold": self.threshold_spin.value(),
            "analysis_window_ms": self.window_spin.value(),
            "min_gap_ms": self.min_gap_spin.value(),
        }

    def _populate_scan_fields(self, settings: dict):
        if "vad_type" in settings:
            idx = self.vad_type_combo.findText(settings["vad_type"])
            if idx >= 0:
                self.vad_type_combo.setCurrentIndex(idx)
        if "amplitude_threshold" in settings:
            self.threshold_spin.setValue(int(settings["amplitude_threshold"]))
        if "analysis_window_ms" in settings:
            self.window_spin.setValue(int(settings["analysis_window_ms"]))
        if "min_gap_ms" in settings:
            self.min_gap_spin.setValue(int(settings["min_gap_ms"]))

    # ---- gathering / populating: transcription options ---------------------

    @staticmethod
    def _factory_options_defaults() -> dict:
        return {"langcode": "en", "suppress_non_speech": False, "chunked_mode": True}

    def _gather_options_fields(self) -> dict:
        return {
            "langcode": self.langcode_edit.text().strip() or "en",
            "suppress_non_speech": self.suppress_checkbox.isChecked(),
            "chunked_mode": self.chunk_checkbox.isChecked(),
        }

    def _populate_options_fields(self, settings: dict):
        if "langcode" in settings:
            self.langcode_edit.setText(str(settings["langcode"]))
        if "suppress_non_speech" in settings:
            self.suppress_checkbox.setChecked(bool(settings["suppress_non_speech"]))
        if "chunked_mode" in settings:
            self.chunk_checkbox.setChecked(bool(settings["chunked_mode"]))

    # ---- gathering / populating: subtitle segmentation ---------------------

    @staticmethod
    def _factory_subtitle_defaults() -> dict:
        return {"enabled": True, "snap_to_gaps": True, "max_chars": SUBTITLE_MAX_CHARS}

    def _gather_subtitle_fields(self) -> dict:
        return {
            "enabled": self.subtitle_enabled_checkbox.isChecked(),
            "snap_to_gaps": self.subtitle_snap_checkbox.isChecked(),
            "max_chars": self.subtitle_max_chars_spin.value(),
        }

    def _populate_subtitle_fields(self, settings: dict):
        if "enabled" in settings:
            self.subtitle_enabled_checkbox.setChecked(bool(settings["enabled"]))
        if "snap_to_gaps" in settings:
            self.subtitle_snap_checkbox.setChecked(bool(settings["snap_to_gaps"]))
        if "max_chars" in settings:
            self.subtitle_max_chars_spin.setValue(int(settings["max_chars"]))

    # ---- gathering / populating: subtitle lingering ------------------------

    @staticmethod
    def _factory_linger_defaults() -> dict:
        return {
            "enabled": True,
            "long_pause_ms": int(SUBTITLE_LINGER_LONG_PAUSE_S * 1000),
        }

    def _gather_linger_fields(self) -> dict:
        return {
            "enabled": self.linger_enabled_checkbox.isChecked(),
            "long_pause_ms": self.linger_long_pause_spin.value(),
        }

    def _populate_linger_fields(self, settings: dict):
        if "enabled" in settings:
            self.linger_enabled_checkbox.setChecked(bool(settings["enabled"]))
        if "long_pause_ms" in settings:
            self.linger_long_pause_spin.setValue(int(settings["long_pause_ms"]))

    # ---- gathering / populating: speaker presets ---------------------------

    def _row_to_dict(self, row: int) -> dict:
        def cell_text(col):
            item = self.preset_table.item(row, col)
            return item.text() if item is not None else ""

        def cell_float(col, default=0.0):
            try:
                return float(cell_text(col))
            except ValueError:
                return default

        def cell_int(col, default=0):
            try:
                return int(float(cell_text(col)))
            except ValueError:
                return default

        return {
            "name": cell_text(0),
            "comma_ms": cell_int(1, 250),
            "period_ms": cell_int(2, 500),
            "paragraph_ms": cell_int(3, 1200),
            "target_s": cell_float(4, 15.0),
            "range_s": cell_float(5, 5.0),
        }

    def _gather_presets(self) -> list:
        return [self._row_to_dict(r) for r in range(self.preset_table.rowCount())]

    def _row_id_at(self, row: int):
        """Reads back the stable id stashed on a row's first cell (Qt.UserRole),
        used instead of row index since sorting reorders rows."""
        item = self.preset_table.item(row, 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _find_row_by_id(self, row_id) -> int:
        if row_id is not None:
            for row in range(self.preset_table.rowCount()):
                if self._row_id_at(row) == row_id:
                    return row
        return -1

    def _add_table_row(self, preset: dict):
        # Sorting must be off while a row is being inserted and filled --
        # otherwise a re-sort can fire between individual setItem() calls
        # and scatter a single row's cells across the wrong rows.
        was_sorting = self.preset_table.isSortingEnabled()
        self.preset_table.setSortingEnabled(False)
        row = self.preset_table.rowCount()
        self.preset_table.insertRow(row)
        values = [
            preset.get("name", preset.get("notes", "")),  # "notes" accepted too, for older saved settings.json files
            str(preset.get("comma_ms", 250)),
            str(preset.get("period_ms", 500)),
            str(preset.get("paragraph_ms", 1200)),
            str(preset.get("target_s", 15.0)),
            str(preset.get("range_s", 5.0)),
        ]
        row_id = self._next_row_id
        self._next_row_id += 1
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            if col == 0:
                item.setData(Qt.UserRole, row_id)
            self.preset_table.setItem(row, col, item)
        if was_sorting:
            self.preset_table.setSortingEnabled(True)
        return row_id

    def _populate_table(self, presets: list):
        self.preset_table.setSortingEnabled(False)
        self.preset_table.setRowCount(0)
        for preset in presets:
            self._add_table_row(preset)
        self.preset_table.setSortingEnabled(True)

    # ---- table row management ------------------------------------------

    def _on_add_row_clicked(self):
        # _add_table_row fills in sane defaults for any keys not given.
        self._add_table_row({"name": "New preset"})

    def _on_remove_row_clicked(self):
        row = self.preset_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "No row selected", "Select a row to remove first.")
            return
        removed_id = self._row_id_at(row)
        self.preset_table.removeRow(row)
        if self._active_preset_id == removed_id:
            new_row = min(row, self.preset_table.rowCount() - 1)
            self._active_preset_id = self._row_id_at(new_row) if new_row >= 0 else None

    def _on_use_preset_clicked(self):
        row = self.preset_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "No row selected", "Select a preset row first.")
            return
        self._active_preset_id = self._row_id_at(row)
        description = self.get_active_preset_description()
        self.settings_status.setText(f"Using preset: {description}")
        self.preset_changed.emit(description)

    # ---- the interface STTTab actually uses ------------------------------

    def get_active_config(self) -> ChunkerConfig:
        """Builds a ChunkerConfig from the current scan fields plus
        whichever preset row was last applied via "Use with Transcriber"
        (falling back to row 0 if none was ever explicitly chosen)."""
        scan = self._gather_scan_fields()
        row = self._find_row_by_id(self._active_preset_id)
        if row < 0:
            row = 0
        preset = self._row_to_dict(row) if self.preset_table.rowCount() else {}

        return ChunkerConfig(
            amplitude_threshold=scan.get("amplitude_threshold", ChunkerConfig.amplitude_threshold),
            analysis_window_ms=scan.get("analysis_window_ms", ChunkerConfig.analysis_window_ms),
            min_gap_ms=scan.get("min_gap_ms", ChunkerConfig.min_gap_ms),
            chunk_target_s=preset.get("target_s", ChunkerConfig.chunk_target_s),
            chunk_range_s=preset.get("range_s", ChunkerConfig.chunk_range_s),
            pause_comma_ms=preset.get("comma_ms", ChunkerConfig.pause_comma_ms),
            pause_sentence_ms=preset.get("period_ms", ChunkerConfig.pause_sentence_ms),
            pause_paragraph_ms=preset.get("paragraph_ms", ChunkerConfig.pause_paragraph_ms),
        )

    def get_active_preset_description(self) -> str:
        row = self._find_row_by_id(self._active_preset_id)
        if row < 0:
            return "No preset selected -- using built-in defaults."
        preset = self._row_to_dict(row)
        name = preset["name"] or f"Row {row + 1}"
        return (f"{name} — target {preset['target_s']:.0f}s ±{preset['range_s']:.0f}s, "
                f"comma {preset['comma_ms']}ms / period {preset['period_ms']}ms / "
                f"paragraph {preset['paragraph_ms']}ms")

    def get_langcode(self) -> str:
        return self.langcode_edit.text().strip() or "en"

    def get_suppress_non_speech(self) -> bool:
        return self.suppress_checkbox.isChecked()

    def get_chunked_mode(self) -> bool:
        return self.chunk_checkbox.isChecked()

    def get_subtitle_segmentation_enabled(self) -> bool:
        return self.subtitle_enabled_checkbox.isChecked()

    def get_subtitle_snap_to_gaps(self) -> bool:
        return self.subtitle_snap_checkbox.isChecked()

    def get_subtitle_max_chars(self) -> int:
        return self.subtitle_max_chars_spin.value()

    def get_subtitle_linger_enabled(self) -> bool:
        return self.linger_enabled_checkbox.isChecked()

    def get_subtitle_linger_long_pause_ms(self) -> int:
        return self.linger_long_pause_spin.value()

    # ---- save / load / reset -----------------------------------------------

    def _try_autoload(self):
        if DEFAULT_SETTINGS_PATH.exists():
            try:
                self._load_from_path(DEFAULT_SETTINGS_PATH)
                self.settings_status.setText(f"Auto-loaded STT settings from {DEFAULT_SETTINGS_PATH}")
            except (OSError, ValueError) as e:
                message = f"Couldn't auto-load STT settings: {e}"
                self.settings_status.setText(message)
                print_error(message)

    def _load_from_path(self, path: Path):
        data = load_json_dict(path)
        if _KEY_SCAN in data:
            self._populate_scan_fields(data[_KEY_SCAN])
        if _KEY_OPTIONS in data:
            self._populate_options_fields(data[_KEY_OPTIONS])
        if _KEY_SUBTITLE in data:
            self._populate_subtitle_fields(data[_KEY_SUBTITLE])
        if _KEY_LINGER in data:
            self._populate_linger_fields(data[_KEY_LINGER])
        if _KEY_PRESETS in data and data[_KEY_PRESETS]:
            self._populate_table(data[_KEY_PRESETS])

    def _save_to_path(self, path: Path):
        save_json_merged(path, {
            _KEY_SCAN: self._gather_scan_fields(),
            _KEY_OPTIONS: self._gather_options_fields(),
            _KEY_SUBTITLE: self._gather_subtitle_fields(),
            _KEY_LINGER: self._gather_linger_fields(),
            _KEY_PRESETS: self._gather_presets(),
        })

    def _on_save_clicked(self):
        try:
            self._save_to_path(DEFAULT_SETTINGS_PATH)
            self.settings_status.setText(f"Saved STT settings to {DEFAULT_SETTINGS_PATH}")
        except OSError as e:
            print_error(f"Couldn't save STT settings: {e}")
            QMessageBox.warning(self, "Couldn't save settings", str(e))

    def _on_load_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load settings", str(DEFAULT_SETTINGS_PATH.parent), "JSON (*.json)"
        )
        if not path:
            return
        try:
            self._load_from_path(Path(path))
            self.settings_status.setText(f"Loaded STT settings from {path}")
            self.settings_loaded.emit(Path(path))
        except (OSError, ValueError) as e:
            print_error(f"Couldn't load STT settings: {e}")
            QMessageBox.warning(self, "Couldn't load settings", str(e))

    def _on_reset_clicked(self):
        self._populate_scan_fields(self._factory_scan_defaults())
        self._populate_options_fields(self._factory_options_defaults())
        self._populate_subtitle_fields(self._factory_subtitle_defaults())
        self._populate_linger_fields(self._factory_linger_defaults())
        self._populate_table(_FACTORY_PRESETS)
        self._active_preset_id = self._row_id_at(1) if self.preset_table.rowCount() > 1 else None
        self.settings_status.setText("Reset to built-in defaults")
