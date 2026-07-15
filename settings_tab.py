"""
SettingsTab: exposes chunker.py's tunable constants (pause lengths,
chunk-size limits, abbreviation list) plus the Narration tab's highlight-
scrolling options, for live editing and save/load to settings.json.
"""

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QFormLayout,
    QMessageBox, QFileDialog, QSpinBox, QPlainTextEdit, QGroupBox,
    QScrollArea, QCheckBox, QGridLayout, QPushButton,
)

import chunker  # imported as a module (not just `from chunker import ...`) so
# this tab can read/write its tunables (PAUSE_MAP, MIN_CHUNK_CHARS, ...) at
# runtime and have chunk_text() see the changes on the very next call.

from ui_common import (
    DEFAULT_SETTINGS_PATH, STATUS_STYLE_SETTINGS,
    COLOR_DARK_GREEN, COLOR_DARK_RED, COLOR_DARK_BLUE,
    STANDARD_BTN_HEIGHT, btn_style, print_error,
    save_json_merged, ElidingLabel,
)

# Punctuation-keyed pauses shown in the settings tab, with human-readable
# labels -- in the same order they're defined in chunker.PAUSE_MAP.
_PUNCT_PAUSE_LABELS = [
    (",", "Comma  ( , )"),
    (";", "Semicolon  ( ; )"),
    (":", "Colon  ( : )"),
    ("\u2014", "Em dash  ( \u2014 )"),
    ("\u2013", "En dash  ( \u2013 )"),
    (".", "Period  ( . )"),
    ("!", "Exclamation  ( ! )"),
    ("?", "Question  ( ? )"),
]

# The other (non-PAUSE_MAP) pause constants in chunker.py, with labels and
# short explanations of what each one is for.
_OTHER_PAUSE_FIELDS = [
    ("DEFAULT_PAUSE_MS", "Default pause", "Used when a chunk ends without any of the punctuation above."),
    ("URL_PAUSE_MS", "Pause after a URL", "Inserted right after a URL is read, regardless of what follows."),
    ("PARAGRAPH_PAUSE_MS", "Paragraph break pause", "Used for a blank line (2+ newlines) between paragraphs."),
    ("FORCED_SPLIT_PAUSE_MS", "Forced long-chunk split pause", "Used when an overlong unpunctuated chunk is force-split."),
]


class SettingsTab(QWidget):
    """Exposes chunker.py's tunable constants (pause lengths, chunk-size
    limits, abbreviation list) for live editing, plus save/load to a JSON
    settings file. Changes are applied to the `chunker` module immediately
    -- there's no separate "Apply" step -- so the very next chunk_text()
    call (i.e. the next time Start is pressed) picks them up."""

    settings_loaded = Signal(Path)  # emitted with the path after a manual "Load Settings…"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._building = True  # suppress live-apply while widgets are first populated
        self.punct_spinboxes: dict[str, QSpinBox] = {}
        self.other_spinboxes: dict[str, QSpinBox] = {}
        self._build_ui()
        self._load_from_chunker_defaults()
        # Snapshot chunker.py's as-written values *before* any config file
        # gets auto-loaded, so "Reset to Defaults" has something to go back
        # to that isn't just whatever was last saved.
        self._factory_defaults = self._gather_settings()
        self._building = False
        self._try_autoload()

    # ---- UI construction --------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)

        # -- Connection (kept outside the scroll area so it's always visible) --
        connection_row = QHBoxLayout()
        self.url_edit = QLineEdit("http://127.0.0.1:5001")
        self.url_edit.textChanged.connect(self._on_field_changed)
        # Narrowed from its old full-width self so the Model field below
        # fits on the same line -- a plain "http://host:port" URL doesn't
        # need much more room than this to stay readable.
        self.url_edit.setFixedWidth(220)
        connection_row.addWidget(QLabel("KoboldCpp URL:"))
        connection_row.addWidget(self.url_edit)
        self.model_edit = QLineEdit("kcpp")
        self.model_edit.setToolTip(
            "Sent as the \"model\" field in the /v1/audio/speech request payload."
        )
        self.model_edit.textChanged.connect(self._on_field_changed)
        self.model_edit.setFixedWidth(120)
        connection_row.addWidget(QLabel("Model:"))
        connection_row.addWidget(self.model_edit)
        connection_row.addStretch(1)
        outer.addLayout(connection_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        # 2x2: Scrolling (top-left) / Chunk sizing (top-right) over
        # Pauses (bottom-left) / Abbreviations (bottom-right). Row 2 gets
        # all the stretch so the four groups stay pinned to the top of
        # the scroll area instead of spreading out to fill it.
        layout = QGridLayout(content)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

        # -- Pauses --
        pause_group = QGroupBox("Pauses (milliseconds)")
        pause_form = QFormLayout(pause_group)
        pause_form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        for char, label in _PUNCT_PAUSE_LABELS:
            spin = QSpinBox()
            spin.setRange(0, 10000)
            spin.setSingleStep(10)
            spin.valueChanged.connect(self._on_field_changed)
            self.punct_spinboxes[char] = spin
            pause_form.addRow(label, spin)
        for name, label, tip in _OTHER_PAUSE_FIELDS:
            spin = QSpinBox()
            spin.setRange(0, 10000)
            spin.setSingleStep(10)
            spin.setToolTip(tip)
            spin.valueChanged.connect(self._on_field_changed)
            self.other_spinboxes[name] = spin
            pause_form.addRow(label, spin)
        layout.addWidget(pause_group, 1, 0, alignment=Qt.AlignLeft | Qt.AlignTop)

        # -- Chunking limits --
        chunk_group = QGroupBox("Chunk sizing")
        chunk_form = QFormLayout(chunk_group)
        chunk_form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        self.long_chunk_spin = QSpinBox()
        self.long_chunk_spin.setRange(2, 2000)
        self.long_chunk_spin.setToolTip(
            "Force-split a chunk that has no natural break once it reaches this many words."
        )
        self.long_chunk_spin.valueChanged.connect(self._on_field_changed)
        chunk_form.addRow("Long-chunk word limit:", self.long_chunk_spin)

        self.min_chars_spin = QSpinBox()
        self.min_chars_spin.setRange(0, 500)
        self.min_chars_spin.setToolTip(
            "Chunks shorter than this (characters) get merged into the next one."
        )
        self.min_chars_spin.valueChanged.connect(self._on_field_changed)
        chunk_form.addRow("Min chunk chars:", self.min_chars_spin)
        layout.addWidget(chunk_group, 0, 1, alignment=Qt.AlignLeft | Qt.AlignTop)

        # -- Abbreviations --
        abbrev_group = QGroupBox("Abbreviations (not treated as sentence-enders)")
        abbrev_layout = QVBoxLayout(abbrev_group)
        self.abbrev_edit = QPlainTextEdit()
        self.abbrev_edit.setPlaceholderText("One per line, or comma-separated, e.g.: dr., mr., etc.")
        self.abbrev_edit.setFixedHeight(295)
        self.abbrev_edit.textChanged.connect(self._on_field_changed)
        abbrev_layout.addWidget(self.abbrev_edit)
        layout.addWidget(abbrev_group, 1, 1, alignment=Qt.AlignLeft | Qt.AlignTop)

        # -- Scrolling --
        scroll_group = QGroupBox("Scrolling")
        scroll_form = QFormLayout(scroll_group)
        scroll_form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        self.highlight_margin_checkbox = QCheckBox("Highlight Margin")
        self.highlight_margin_checkbox.setToolTip(
            "Enables the playback highlight margin / distance to top or bottom edge.\n"
            "Set by 1/Scroll Denominator ratio of textbox height."
        )
        self.highlight_margin_checkbox.stateChanged.connect(self._on_field_changed)
        scroll_form.addRow(self.highlight_margin_checkbox)

        self.clamp_highlight_checkbox = QCheckBox("Clamp Highlight Distance")
        self.clamp_highlight_checkbox.setToolTip(
            "Checked: Keeps the playback highlight at a constant distance from the edge.\n"
            "Unchecked: Auto-scrolls the highlight within same distance to opposite edge."
        )
        self.clamp_highlight_checkbox.stateChanged.connect(self._on_field_changed)
        scroll_form.addRow(self.clamp_highlight_checkbox)

        self.scroll_denominator_spin = QSpinBox()
        self.scroll_denominator_spin.setRange(2, 20)
        self.scroll_denominator_spin.setToolTip(
            "Scroll Denominator (SD): The highlight is kept a distance from the top or bottom\n"
            "edge, of at least 1/SD ratio of the text box's height, except at beginning and end."
        )
        self.scroll_denominator_spin.valueChanged.connect(self._on_field_changed)
        scroll_form.addRow("Scroll Denominator:", self.scroll_denominator_spin)

        self.highlight_margin_checkbox.toggled.connect(self._sync_highlight_margin_dependents_enabled)
        self._sync_highlight_margin_dependents_enabled(self.highlight_margin_checkbox.isChecked())
        layout.addWidget(scroll_group, 0, 0, alignment=Qt.AlignLeft | Qt.AlignTop)

        layout.setRowStretch(2, 1)

        # -- Save / load --
        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("💾 Save Settings")
        self.load_btn = QPushButton("📂 Load Settings…")
        self.reset_btn = QPushButton("Reset to Defaults")
        self.save_btn.setStyleSheet(btn_style(COLOR_DARK_GREEN))
        self.load_btn.setStyleSheet(btn_style(COLOR_DARK_RED))
        self.reset_btn.setStyleSheet(btn_style(COLOR_DARK_BLUE))
        for btn in (self.save_btn, self.load_btn, self.reset_btn):
            btn.setFixedHeight(STANDARD_BTN_HEIGHT)
        self.save_btn.clicked.connect(self._on_save_clicked)
        self.load_btn.clicked.connect(self._on_load_clicked)
        self.reset_btn.clicked.connect(self._on_reset_clicked)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.load_btn)
        btn_row.addWidget(self.reset_btn)
        outer.addLayout(btn_row)

        self.settings_status = ElidingLabel("")
        self.settings_status.setStyleSheet(STATUS_STYLE_SETTINGS)
        outer.addWidget(self.settings_status)

    # ---- gathering / applying / populating ---------------------------------

    def _gather_settings(self) -> dict:
        """Reads the current widget values into a plain dict, in the same
        shape that gets written to / read from the JSON settings file."""
        pause_map = {char: spin.value() for char, spin in self.punct_spinboxes.items()}
        other = {name: spin.value() for name, spin in self.other_spinboxes.items()}
        abbrevs = self._parse_abbrev_text(self.abbrev_edit.toPlainText())
        return {
            "KOBOLDCPP_URL": self.url_edit.text().strip(),
            "MODEL": self.model_edit.text().strip(),
            "PAUSE_MAP": pause_map,
            **other,
            "LONG_CHUNK_WORD_LIMIT": self.long_chunk_spin.value(),
            "MIN_CHUNK_CHARS": self.min_chars_spin.value(),
            "ABBREVIATIONS": sorted(abbrevs),
            "HIGHLIGHT_MARGIN_ENABLED": self.highlight_margin_checkbox.isChecked(),
            "SCROLL_DENOMINATOR": self.scroll_denominator_spin.value(),
            "CLAMP_HIGHLIGHT_DISTANCE": self.clamp_highlight_checkbox.isChecked(),
        }

    @staticmethod
    def _parse_abbrev_text(text: str) -> set:
        """Splits on both commas and newlines, so either style of editing
        (one per line, or a comma-separated run) works."""
        parts = [p.strip().lower() for p in text.replace(",", "\n").splitlines()]
        return {p for p in parts if p}

    def _apply_settings(self, settings: dict):
        """Pushes a settings dict onto the live `chunker` module. Mutating
        PAUSE_MAP in place (rather than reassigning it) so other code that
        already holds a reference to the dict still sees the update."""
        pause_map = settings.get("PAUSE_MAP", {})
        for char, ms in pause_map.items():
            chunker.PAUSE_MAP[char] = ms
        for name, _label, _tip in _OTHER_PAUSE_FIELDS:
            if name in settings:
                setattr(chunker, name, settings[name])
        if "LONG_CHUNK_WORD_LIMIT" in settings:
            chunker.LONG_CHUNK_WORD_LIMIT = settings["LONG_CHUNK_WORD_LIMIT"]
        if "MIN_CHUNK_CHARS" in settings:
            chunker.MIN_CHUNK_CHARS = settings["MIN_CHUNK_CHARS"]
        if "ABBREVIATIONS" in settings:
            chunker.ABBREVIATIONS = set(settings["ABBREVIATIONS"])

    def _populate_fields(self, settings: dict):
        """The inverse of _gather_settings -- fills the widgets from a dict.
        Missing keys just leave that field at whatever it already was."""
        self._building = True
        if "KOBOLDCPP_URL" in settings:
            self.url_edit.setText(settings["KOBOLDCPP_URL"])
        if "MODEL" in settings:
            self.model_edit.setText(settings["MODEL"])
        pause_map = settings.get("PAUSE_MAP", {})
        for char, spin in self.punct_spinboxes.items():
            if char in pause_map:
                spin.setValue(int(pause_map[char]))
        for name, spin in self.other_spinboxes.items():
            if name in settings:
                spin.setValue(int(settings[name]))
        if "LONG_CHUNK_WORD_LIMIT" in settings:
            self.long_chunk_spin.setValue(int(settings["LONG_CHUNK_WORD_LIMIT"]))
        if "MIN_CHUNK_CHARS" in settings:
            self.min_chars_spin.setValue(int(settings["MIN_CHUNK_CHARS"]))
        if "ABBREVIATIONS" in settings:
            self.abbrev_edit.setPlainText("\n".join(sorted(settings["ABBREVIATIONS"])))
        if "HIGHLIGHT_MARGIN_ENABLED" in settings:
            self.highlight_margin_checkbox.setChecked(bool(settings["HIGHLIGHT_MARGIN_ENABLED"]))
        if "SCROLL_DENOMINATOR" in settings:
            self.scroll_denominator_spin.setValue(int(settings["SCROLL_DENOMINATOR"]))
        if "CLAMP_HIGHLIGHT_DISTANCE" in settings:
            self.clamp_highlight_checkbox.setChecked(bool(settings["CLAMP_HIGHLIGHT_DISTANCE"]))
        self._sync_highlight_margin_dependents_enabled(self.highlight_margin_checkbox.isChecked())
        self._building = False

    def _load_from_chunker_defaults(self):
        """Used on first startup (no config file yet) -- the chunker
        module's own current values become the shown defaults. The three
        scroll-margin settings aren't chunker constants, so they get a
        plain hardcoded default here instead (off, SD=4, clamp off --
        matching the original fixed 1/4 margin, jump-to-opposite-edge
        behavior)."""
        settings = {
            "KOBOLDCPP_URL": "http://127.0.0.1:5001",
            "MODEL": "kcpp",
            "PAUSE_MAP": dict(chunker.PAUSE_MAP),
            "LONG_CHUNK_WORD_LIMIT": chunker.LONG_CHUNK_WORD_LIMIT,
            "MIN_CHUNK_CHARS": chunker.MIN_CHUNK_CHARS,
            "ABBREVIATIONS": sorted(chunker.ABBREVIATIONS),
            "HIGHLIGHT_MARGIN_ENABLED": False,
            "SCROLL_DENOMINATOR": 4,
            "CLAMP_HIGHLIGHT_DISTANCE": False,
        }
        for name, _label, _tip in _OTHER_PAUSE_FIELDS:
            settings[name] = getattr(chunker, name)
        self._populate_fields(settings)

    def _on_field_changed(self, *_args):
        if self._building:
            return
        self._apply_settings(self._gather_settings())

    def _sync_highlight_margin_dependents_enabled(self, checked: bool):
        """Scroll Denominator and Clamp Highlight Distance only mean
        anything once Highlight Margin itself is turned on -- grey them out
        otherwise instead of leaving them editable but inert."""
        self.scroll_denominator_spin.setEnabled(checked)
        self.clamp_highlight_checkbox.setEnabled(checked)

    # ---- save / load / reset -----------------------------------------------

    def _try_autoload(self):
        if DEFAULT_SETTINGS_PATH.exists():
            try:
                self._load_from_path(DEFAULT_SETTINGS_PATH)
                self.settings_status.setText(f"Auto-loaded settings from {DEFAULT_SETTINGS_PATH}")
            except (OSError, ValueError) as e:
                message = f"Couldn't auto-load settings: {e}"
                self.settings_status.setText(message)
                print_error(message)

    def _load_from_path(self, path: Path):
        with open(path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        self._populate_fields(settings)
        self._apply_settings(self._gather_settings())

    def _save_to_path(self, path: Path):
        save_json_merged(path, self._gather_settings())

    def _on_save_clicked(self):
        try:
            self._save_to_path(DEFAULT_SETTINGS_PATH)
            self.settings_status.setText(f"Saved settings to {DEFAULT_SETTINGS_PATH}")
        except OSError as e:
            print_error(f"Couldn't save settings: {e}")
            QMessageBox.warning(self, "Couldn't save settings", str(e))

    def _on_load_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load settings", str(DEFAULT_SETTINGS_PATH.parent), "JSON (*.json)"
        )
        if not path:
            return
        try:
            self._load_from_path(Path(path))
            self.settings_status.setText(f"Loaded settings from {path}")
            self.settings_loaded.emit(Path(path))
        except (OSError, ValueError) as e:
            print_error(f"Couldn't load settings: {e}")
            QMessageBox.warning(self, "Couldn't load settings", str(e))

    def _on_reset_clicked(self):
        """Resets the fields (and the live chunker module) back to the
        values chunker.py was written with -- not the last-saved file."""
        self._populate_fields(self._factory_defaults)
        self._apply_settings(self._gather_settings())
        self.settings_status.setText("Reset to chunker.py's built-in defaults")
