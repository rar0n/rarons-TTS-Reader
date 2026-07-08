"""
rarons TTS Reader - Read long-form text aloud (KoboldCpp API)

- Built to work around KoboldCpp's tendency to drift in voice/speed
  and stop on long single-shot TTS requests.


MIT License

Copyright (c) 2026 Ragnar Aronsen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.


Contact: On my github page://github.com/rar0n/rarons-TTS-Reader/


A small Python app that reads pasted text aloud through KoboldCpp's
TTS API, with live highlighting one sentence at a time, better pauses
(hopefully), and basic Play, Pause/Resume, Rwd/Fwd and Stop controls.

Note: Saving as mp3 might take a little while, depending on size.

More in README.md

    2026 raron ( But mostly Claude :) )

That is all.

"""

import sys
import os
import time
import json
import random
from pathlib import Path

import numpy as np
import soundfile as sf

from PySide6.QtCore import QObject, Signal, Qt, QThread, QTimer
from PySide6.QtGui import QTextCursor, QColor, QIntValidator, QFontMetrics
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLineEdit, QLabel, QFormLayout, QMessageBox,
    QComboBox, QFileDialog, QTabWidget, QSpinBox, QPlainTextEdit,
    QGroupBox, QScrollArea, QTableWidget, QTableWidgetItem, QAbstractItemView,
)

import chunker  # imported as a module (not just `from chunker import ...`) so the
# settings tab can read/write its tunables (PAUSE_MAP, MIN_CHUNK_CHARS, ...) at
# runtime and have chunk_text() see the changes on the very next call.
from chunker import chunk_text, Chunk
from tts_client import KoboldTTSClient, TTSError
from synth_worker import SynthWorker
from audio_engine import AudioEngine, PlaybackState

# Where chunking settings get saved to / auto-loaded from on startup.
DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

# Status label colors for the two states it can be in.
_STATUS_STYLE_NORMAL = "background-color: #204090; color: white; padding: 4px;"
_STATUS_STYLE_ERROR = "background-color: #8b0000; color: white; padding: 4px;"

# Shared dark RGB palette for buttons across all tabs.
_COLOR_DARK_BLUE = "#101040"    # same blue as the status label -- Play, Randomize seed, Copy seed
_COLOR_DARK_GREEN = "#104010"   # "ready" / save / store actions
_COLOR_DARK_RED = "#401010"     # same red as the error style -- stop / remove / destructive-ish actions
_COLOR_DARK_AMBER = "#804000"   # active seek controls -- Rewind / Forward while playing or paused


def _btn_style(color: str) -> str:
    return f"background-color: {color}; color: white; padding: 4px;"

# Used to project remaining time before any chunk has actually been
# synthesized yet (roughly 150 words/minute of speech).
_FALLBACK_SECONDS_PER_WORD = 0.4


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 ** 2:.1f} MB"


def _with_extension(path: str, ext: str) -> str:
    """Returns `path` with its extension forced to `ext` (e.g. '.mp3'),
    replacing whatever extension (if any) is already there."""
    base, _ = os.path.splitext(path)
    return base + ext


def _load_json_dict(path: Path) -> dict:
    """Reads a JSON file into a dict, returning {} if it's missing or
    doesn't parse as an object."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_json_merged(path: Path, updates: dict):
    """Merges `updates` into whatever's already on disk at `path` instead
    of overwriting the whole file, so SettingsTab and SeedVaultTab can
    both save to the same settings.json without erasing each other's
    top-level keys."""
    data = _load_json_dict(path)
    data.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class ZoomableTextEdit(QTextEdit):
    """A QTextEdit where holding Ctrl while scrolling changes the font size
    instead of scrolling the view -- Qt doesn't expose this as a setting,
    so it's done by hand here, one point size per notch, clamped so it
    can't be scrolled down to unreadable or up to absurd."""

    MIN_POINT_SIZE = 6
    MAX_POINT_SIZE = 48

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            steps = 1 if event.angleDelta().y() > 0 else -1
            self._zoom(steps)
            event.accept()
            return
        super().wheelEvent(event)

    def _zoom(self, steps: int):
        font = self.font()
        size = font.pointSize()
        if size <= 0:  # widget font was set by pixel size instead of point size
            size = 11
        new_size = max(self.MIN_POINT_SIZE, min(self.MAX_POINT_SIZE, size + steps))
        if new_size != size:
            font.setPointSize(new_size)
            self.setFont(font)


class EngineBridge(QObject):
    """Relays AudioEngine callbacks (which fire on background threads)
    into Qt signals, so the GUI thread can safely react to them."""
    chunk_started = Signal(int)
    chunk_ended = Signal(int)
    playback_finished = Signal()
    chunk_error = Signal(int, str)


class SpeakerFetchWorker(QThread):
    """Fetches the voice list from KoboldCpp's /api/extra/speakers_list
    in the background so the UI doesn't stall while the request is out."""
    fetched = Signal(list)
    failed = Signal(str)

    def __init__(self, client: KoboldTTSClient, parent=None):
        super().__init__(parent)
        self.client = client

    def run(self):
        try:
            speakers = self.client.list_speakers()
        except TTSError as e:
            self.failed.emit(str(e))
            return
        self.fetched.emit(speakers)



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

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)

        # -- Pauses --
        pause_group = QGroupBox("Pauses (milliseconds)")
        pause_form = QFormLayout(pause_group)
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
        layout.addWidget(pause_group)

        # -- Chunking limits --
        chunk_group = QGroupBox("Chunk sizing")
        chunk_form = QFormLayout(chunk_group)
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
        layout.addWidget(chunk_group)

        # -- Abbreviations --
        abbrev_group = QGroupBox("Abbreviations (not treated as sentence-enders)")
        abbrev_layout = QVBoxLayout(abbrev_group)
        self.abbrev_edit = QPlainTextEdit()
        self.abbrev_edit.setPlaceholderText("One per line, or comma-separated, e.g.: dr., mr., etc.")
        self.abbrev_edit.setFixedHeight(120)
        self.abbrev_edit.textChanged.connect(self._on_field_changed)
        abbrev_layout.addWidget(self.abbrev_edit)
        layout.addWidget(abbrev_group)

        layout.addStretch(1)

        # -- Save / load --
        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("💾 Save Settings")
        self.load_btn = QPushButton("📂 Load Settings…")
        self.reset_btn = QPushButton("Reset to Defaults")
        self.save_btn.setStyleSheet(_btn_style(_COLOR_DARK_GREEN))
        self.load_btn.setStyleSheet(_btn_style(_COLOR_DARK_RED))
        self.reset_btn.setStyleSheet(_btn_style(_COLOR_DARK_BLUE))
        self.save_btn.clicked.connect(self._on_save_clicked)
        self.load_btn.clicked.connect(self._on_load_clicked)
        self.reset_btn.clicked.connect(self._on_reset_clicked)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.load_btn)
        btn_row.addWidget(self.reset_btn)
        outer.addLayout(btn_row)

        self.settings_status = QLabel("")
        outer.addWidget(self.settings_status)

    # ---- gathering / applying / populating ---------------------------------

    def _gather_settings(self) -> dict:
        """Reads the current widget values into a plain dict, in the same
        shape that gets written to / read from the JSON settings file."""
        pause_map = {char: spin.value() for char, spin in self.punct_spinboxes.items()}
        other = {name: spin.value() for name, spin in self.other_spinboxes.items()}
        abbrevs = self._parse_abbrev_text(self.abbrev_edit.toPlainText())
        return {
            "PAUSE_MAP": pause_map,
            **other,
            "LONG_CHUNK_WORD_LIMIT": self.long_chunk_spin.value(),
            "MIN_CHUNK_CHARS": self.min_chars_spin.value(),
            "ABBREVIATIONS": sorted(abbrevs),
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
        self._building = False

    def _load_from_chunker_defaults(self):
        """Used on first startup (no config file yet) -- the chunker
        module's own current values become the shown defaults."""
        settings = {
            "PAUSE_MAP": dict(chunker.PAUSE_MAP),
            "LONG_CHUNK_WORD_LIMIT": chunker.LONG_CHUNK_WORD_LIMIT,
            "MIN_CHUNK_CHARS": chunker.MIN_CHUNK_CHARS,
            "ABBREVIATIONS": sorted(chunker.ABBREVIATIONS),
        }
        for name, _label, _tip in _OTHER_PAUSE_FIELDS:
            settings[name] = getattr(chunker, name)
        self._populate_fields(settings)

    def _on_field_changed(self, *_args):
        if self._building:
            return
        self._apply_settings(self._gather_settings())

    # ---- save / load / reset -----------------------------------------------

    def _try_autoload(self):
        if DEFAULT_SETTINGS_PATH.exists():
            try:
                self._load_from_path(DEFAULT_SETTINGS_PATH)
                self.settings_status.setText(f"Auto-loaded settings from {DEFAULT_SETTINGS_PATH}")
            except (OSError, ValueError) as e:
                self.settings_status.setText(f"Couldn't auto-load settings: {e}")

    def _load_from_path(self, path: Path):
        with open(path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        self._populate_fields(settings)
        self._apply_settings(self._gather_settings())

    def _save_to_path(self, path: Path):
        _save_json_merged(path, self._gather_settings())

    def _on_save_clicked(self):
        try:
            self._save_to_path(DEFAULT_SETTINGS_PATH)
            self.settings_status.setText(f"Saved settings to {DEFAULT_SETTINGS_PATH}")
        except OSError as e:
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
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Couldn't load settings", str(e))

    def _on_reset_clicked(self):
        """Resets the fields (and the live chunker module) back to the
        values chunker.py was written with -- not the last-saved file."""
        self._populate_fields(self._factory_defaults)
        self._apply_settings(self._gather_settings())
        self.settings_status.setText("Reset to chunker.py's built-in defaults")


class SeedVaultTab(QWidget):
    """A little table of remembered (voice, seed, comment) triples. Rows
    get added from the Narration tab's "Store seed" button; this tab just
    manages the table itself (remove/copy-back/save) plus auto-loading
    from the shared settings.json on startup."""

    seed_requested = Signal(str, str)  # (voice, seed) -- picked to send back to the Narration tab

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._try_autoload()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Voice", "Seed", "Comment"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 120)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.remove_row_btn = QPushButton("🗑 Remove row")
        self.copy_to_reader_btn = QPushButton("⇦ Copy seed to Narration")
        self.save_btn = QPushButton("💾 Save Table")
        self.remove_row_btn.setStyleSheet(_btn_style(_COLOR_DARK_RED))
        self.copy_to_reader_btn.setStyleSheet(_btn_style(_COLOR_DARK_BLUE))
        self.save_btn.setStyleSheet(_btn_style(_COLOR_DARK_GREEN))
        self.remove_row_btn.clicked.connect(self._on_remove_row_clicked)
        self.copy_to_reader_btn.clicked.connect(self._on_copy_to_reader_clicked)
        self.save_btn.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(self.remove_row_btn)
        btn_row.addWidget(self.copy_to_reader_btn)
        btn_row.addWidget(self.save_btn)
        layout.addLayout(btn_row)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    # ---- row management -----------------------------------------------------

    def add_row(self, voice: str, seed: str, comment: str = ""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        voice_item = QTableWidgetItem(voice)
        seed_item = QTableWidgetItem(seed)
        comment_item = QTableWidgetItem(comment)
        # Voice/Seed are set programmatically (from "Store seed") -- only
        # the Comment cell should be directly editable by clicking into it.
        for item in (voice_item, seed_item):
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 0, voice_item)
        self.table.setItem(row, 1, seed_item)
        self.table.setItem(row, 2, comment_item)

    def _gather_rows(self) -> list:
        rows = []
        for r in range(self.table.rowCount()):
            voice = self.table.item(r, 0).text() if self.table.item(r, 0) else ""
            seed = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
            comment = self.table.item(r, 2).text() if self.table.item(r, 2) else ""
            rows.append({"voice": voice, "seed": seed, "comment": comment})
        return rows

    def _populate_rows(self, rows: list):
        self.table.setRowCount(0)
        for entry in rows:
            self.add_row(entry.get("voice", ""), entry.get("seed", ""), entry.get("comment", ""))

    def _on_remove_row_clicked(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "No selection", "Select a row first.")
            return
        self.table.removeRow(row)

    def _on_copy_to_reader_clicked(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "No selection", "Select a row first.")
            return
        voice = self.table.item(row, 0).text() if self.table.item(row, 0) else ""
        seed = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
        self.seed_requested.emit(voice, seed)

    # ---- save / load ----------------------------------------------------------

    def _try_autoload(self):
        data = _load_json_dict(DEFAULT_SETTINGS_PATH)
        rows = data.get("SEED_VAULT", [])
        if rows:
            self._populate_rows(rows)
            self.status_label.setText(f"Loaded {len(rows)} seed(s) from {DEFAULT_SETTINGS_PATH}")

    def save_to_disk(self) -> bool:
        """Overwrites settings.json's SEED_VAULT entry with the table's
        current contents. Public (no leading underscore) since MainWindow's
        "Store seed" also calls this, to persist a newly stored seed right
        away rather than only on an explicit Save Table click."""
        try:
            _save_json_merged(DEFAULT_SETTINGS_PATH, {"SEED_VAULT": self._gather_rows()})
            return True
        except OSError as e:
            QMessageBox.warning(self, "Couldn't save seed vault", str(e))
            return False

    def _on_save_clicked(self):
        if self.save_to_disk():
            self.status_label.setText(f"Saved seed vault to {DEFAULT_SETTINGS_PATH}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("raron's TTS Reader v0.45 (2026.07.08)")
        self.resize(820, 600)

        self.chunks: list[Chunk] = []
        self.chunk_word_counts: list[int] = []
        self.total_words = 0
        self.total_chars = 0
        self.synth_worker: SynthWorker | None = None
        self.speaker_worker: SpeakerFetchWorker | None = None

        # Synthesis-speed tracking, for the "rendering ~X left" estimate --
        # separate from playback progress, since KoboldCpp can run faster
        # or slower than real-time speech.
        self._synth_start_time: float | None = None
        self._synth_done_count = 0
        self._synth_total_elapsed: float | None = None

        # Trailing stats (words/time/render/memory) from the last status
        # update, kept around so "Finished" and error states can still
        # show them instead of wiping the line blank.
        self._last_stats_line = ""

        # Wall-clock "how long has narration actually been running" stopwatch
        # -- accumulates only while actively playing, so pauses don't count.
        # Rewind/forward are fine to leave uncorrected: they change how much
        # ground gets covered, not whether the clock is ticking.
        self._narration_start_time: float | None = None
        self._narration_elapsed: float = 0.0

        self.bridge = EngineBridge()
        self.bridge.chunk_started.connect(self._on_chunk_started)
        self.bridge.chunk_ended.connect(self._on_chunk_ended)
        self.bridge.playback_finished.connect(self._on_playback_finished)
        self.bridge.chunk_error.connect(self._on_chunk_error)

        self.engine = AudioEngine(
            on_chunk_start=lambda i: self.bridge.chunk_started.emit(i),
            on_chunk_end=lambda i: self.bridge.chunk_ended.emit(i),
            on_finished=lambda: self.bridge.playback_finished.emit(),
            on_error=lambda i, msg: self.bridge.chunk_error.emit(i, msg),
        )

        self.status_timer = QTimer(self)
        self.status_timer.setInterval(300)
        self.status_timer.timeout.connect(self._update_playing_status)

        self._build_ui()
        self._refresh_voices()  # populate the dropdown on startup

    # ---- UI construction --------------------------------------------------

    def _build_ui(self):
        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        reader_tab = QWidget()
        layout = QVBoxLayout(reader_tab)

        # Connection settings
        form = QFormLayout()
        self.url_edit = QLineEdit("http://127.0.0.1:5001")
        form.addRow("KoboldCpp URL:", self.url_edit)

        voice_row = QHBoxLayout()
        self.voice_combo = QComboBox()
        self.voice_combo.setEditable(True)
        self.voice_combo.lineEdit().setPlaceholderText(
            "Pick a voice, or type a custom one…"
        )
        self.refresh_voices_btn = QPushButton("⟳")
        self.refresh_voices_btn.setToolTip(
            "Fetch the voice list from KoboldCpp (/api/extra/speakers_list)"
        )
        self.refresh_voices_btn.setFixedWidth(32)
        self.refresh_voices_btn.clicked.connect(self._refresh_voices)

        # Seed field: pins the TTS voice for the whole playthrough when
        # filled in (0 to 2^31-1 -- larger values seem to make the voice
        # random again). KoboldCpp doesn't report back whatever seed it
        # auto-picks (its /api/extra/perf endpoint doesn't instrument TTS
        # requests), so it starts pre-filled with a random value -- same
        # effect as leaving it blank, but visible and reusable.
        seed_label = QLabel("Seed:")
        self.seed_edit = QLineEdit()
        self.seed_edit.setPlaceholderText("Seed (blank = auto)")
        self.seed_edit.setValidator(QIntValidator(0, 2**31 - 1, self))
        # Sized to comfortably fit "2147483647" (2^31-1), the largest seed,
        # plus a little slack for variable-width fonts.
        seed_width = QFontMetrics(self.seed_edit.font()).horizontalAdvance("2147483647") + 24
        self.seed_edit.setFixedWidth(seed_width)
        self.seed_edit.setText(str(random.randint(0, 2**31 - 1)))
        self.randomize_seed_btn = QPushButton("🎲")
        self.randomize_seed_btn.setToolTip("Fill in a random seed (0 to 2^31-1)")
        self.randomize_seed_btn.setFixedWidth(32)
        self.randomize_seed_btn.setStyleSheet(_btn_style(_COLOR_DARK_BLUE))
        self.randomize_seed_btn.clicked.connect(self._on_randomize_seed_clicked)
        self.store_seed_btn = QPushButton("Store seed")
        self.store_seed_btn.setToolTip(
            "Save the current voice + seed as a row in the Seed Vault tab"
        )
        self.store_seed_btn.setStyleSheet(_btn_style(_COLOR_DARK_GREEN))
        self.store_seed_btn.clicked.connect(self._on_store_seed_clicked)

        voice_row.addWidget(self.voice_combo, stretch=1)
        voice_row.addWidget(self.refresh_voices_btn)
        voice_row.addWidget(seed_label)
        voice_row.addWidget(self.seed_edit)
        voice_row.addWidget(self.randomize_seed_btn)
        voice_row.addWidget(self.store_seed_btn)
        form.addRow("Voice:", voice_row)

        layout.addLayout(form)

        # Text area (input before playback, highlighted "subtitle" view during)
        self.text_edit = ZoomableTextEdit()
        self.text_edit.setPlaceholderText("Paste or type the text you want read aloud... (Ctrl+scroll to resize text)")
        self.text_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.text_edit)

        # Status label
        self.status_label = QLabel("0 Chars")
        self.status_label.setStyleSheet(_STATUS_STYLE_NORMAL)
        layout.addWidget(self.status_label)

        # Transport controls
        controls = QHBoxLayout()
        self.play_btn = QPushButton("▶ Play")
        self.rewind_btn = QPushButton("⏮ Rewind")
        self.forward_btn = QPushButton("⏭ Forward")
        self.stop_btn = QPushButton("⏹ Stop")
        self.save_btn = QPushButton("💾 Save Audio")
        self.play_btn.setStyleSheet(_btn_style(_COLOR_DARK_BLUE))

        self.play_btn.clicked.connect(self._on_play_pause_clicked)
        self.rewind_btn.clicked.connect(self._on_rewind_clicked)
        self.forward_btn.clicked.connect(self._on_forward_clicked)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.save_btn.clicked.connect(self._on_save_clicked)
        self._set_save_audio_ready(False)

        for btn in (self.play_btn, self.rewind_btn,
                    self.forward_btn, self.stop_btn, self.save_btn):
            controls.addWidget(btn)
        layout.addLayout(controls)

        self._set_controls_enabled(playing=False)

        self.settings_tab = SettingsTab()
        self.seed_vault_tab = SeedVaultTab()
        self.seed_vault_tab.seed_requested.connect(self._on_seed_requested_from_vault)
        tabs.addTab(reader_tab, "Narration")
        tabs.addTab(self.seed_vault_tab, "Seed Vault")
        tabs.addTab(self.settings_tab, "Settings")

    def _set_controls_enabled(self, playing: bool):
        self.rewind_btn.setEnabled(playing)
        self.forward_btn.setEnabled(playing)
        self.stop_btn.setEnabled(playing)
        amber_style = _btn_style(_COLOR_DARK_AMBER) if playing else ""
        self.rewind_btn.setStyleSheet(amber_style)
        self.forward_btn.setStyleSheet(amber_style)
        self.stop_btn.setStyleSheet(_btn_style(_COLOR_DARK_RED) if playing else "")
        self.text_edit.setReadOnly(playing)

    def _set_save_audio_ready(self, ready: bool):
        """Enables/disables Save Audio and colors it to match: dark green
        once there's audio ready to save, standard grayed-out look while
        there isn't (rather than a distracting color for "not ready")."""
        self.save_btn.setEnabled(ready)
        self.save_btn.setStyleSheet(_btn_style(_COLOR_DARK_GREEN) if ready else "")

    # ---- voice discovery ----------------------------------------------------

    def _refresh_voices(self):
        base_url = self.url_edit.text().strip() or "http://127.0.0.1:5001"
        client = KoboldTTSClient(base_url=base_url)
        self.refresh_voices_btn.setEnabled(False)
        self.status_label.setText("Fetching voices…")

        self.speaker_worker = SpeakerFetchWorker(client)
        self.speaker_worker.fetched.connect(self._on_voices_fetched)
        self.speaker_worker.failed.connect(self._on_voices_fetch_failed)
        self.speaker_worker.start()

    def _on_voices_fetched(self, speakers: list):
        current = self.voice_combo.currentText().strip()
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        self.voice_combo.addItems(speakers)
        if current:
            idx = self.voice_combo.findText(current)
            if idx >= 0:
                self.voice_combo.setCurrentIndex(idx)
            else:
                self.voice_combo.setCurrentText(current)
        elif speakers:
            # "default"/"random" is what causes the voice to change every
            # sentence -- steer the initial pick away from it if possible.
            preferred = [s for s in speakers if s.lower() not in ("default", "random")]
            self.voice_combo.setCurrentText((preferred or speakers)[0])
        self.voice_combo.blockSignals(False)
        self.refresh_voices_btn.setEnabled(True)
        self.status_label.setText(f"Found {len(speakers)} voice(s)" if speakers else "No voices found")

    def _on_voices_fetch_failed(self, message: str):
        self.refresh_voices_btn.setEnabled(True)
        self.status_label.setText(f"Couldn't fetch voices ({message}) — type one manually")

    def _on_randomize_seed_clicked(self):
        self.seed_edit.setText(str(random.randint(0, 2**31 - 1)))

    def _on_store_seed_clicked(self):
        seed = self.seed_edit.text().strip()
        if not seed:
            QMessageBox.information(self, "No seed", "The seed field is empty -- nothing to store.")
            return
        voice = self.voice_combo.currentText().strip() or "default"
        self.seed_vault_tab.add_row(voice, seed, "")
        if self.seed_vault_tab.save_to_disk():
            self.status_label.setText(f"Stored & saved seed {seed} ({voice}) in Seed Vault")
        else:
            self.status_label.setText(f"Stored seed {seed} ({voice}) in Seed Vault (save failed)")

    def _on_seed_requested_from_vault(self, voice: str, seed: str):
        if voice:
            self.voice_combo.setCurrentText(voice)
        self.seed_edit.setText(seed)
        self.status_label.setText(f"Loaded seed {seed} from Seed Vault")

    # ---- transport handlers -----------------------------------------------

    def _on_play_pause_clicked(self):
        state = self.engine.state
        if state == PlaybackState.STOPPED:
            self._start_playback()
        else:
            self.engine.toggle_pause()
            if self.engine.state == PlaybackState.PAUSED:
                self._narration_mark_paused()
            elif self.engine.state == PlaybackState.PLAYING:
                self._narration_mark_playing()
        self._sync_play_button()

    def _start_playback(self):
        text = self.text_edit.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "No text", "Paste some text first.")
            return

        self.chunks = chunk_text(text)
        if not self.chunks:
            QMessageBox.information(self, "Nothing to read", "Couldn't find any readable text.")
            return

        self.chunk_word_counts = [len(c.text.split()) for c in self.chunks]
        self.total_words = sum(self.chunk_word_counts)
        self.total_chars = len(text)
        self._synth_start_time = time.monotonic()
        self._synth_done_count = 0
        self._synth_total_elapsed = None
        self._last_stats_line = ""

        voice = self.voice_combo.currentText().strip() or "default"

        seed_text = self.seed_edit.text().strip()
        seed = None
        if seed_text:
            try:
                seed = int(seed_text)
            except ValueError:
                QMessageBox.warning(self, "Bad seed", f"'{seed_text}' isn't a whole number.")
                return

        client = KoboldTTSClient(
            base_url=self.url_edit.text().strip() or "http://127.0.0.1:5001",
            voice=voice,
            seed=seed,
        )

        self.engine.set_chunks(self.chunks)
        self._set_controls_enabled(playing=True)
        self._set_save_audio_ready(False)
        self._set_error_style(False)
        self.status_label.setText(f"Synthesizing… 0/{len(self.chunks)}")

        if self.synth_worker is not None:
            self.synth_worker.stop()
            self.synth_worker.wait(500)

        self.synth_worker = SynthWorker(client, self.chunks)
        self.synth_worker.chunk_ready.connect(self.engine.feed_audio)
        self.synth_worker.chunk_ready.connect(self._on_chunk_synthesized)
        self.synth_worker.error.connect(self._on_synth_error)
        self.synth_worker.finished_all.connect(self._on_synthesis_finished)
        self.synth_worker.start()

        self._narration_reset()
        self._narration_mark_playing()
        self.engine.play_from(0)
        self.status_timer.start()

    def _on_rewind_clicked(self):
        self.engine.rewind(1)
        self._after_seek()

    def _on_forward_clicked(self):
        self.engine.skip_forward(1)
        self._after_seek()

    def _after_seek(self):
        """Rewind/skip while paused only move where playback will resume
        from -- the engine won't fire on_chunk_start for that (nothing's
        actually playing), so the highlight/status need updating here
        directly instead of waiting on a callback that isn't coming."""
        self._sync_play_button()
        self._highlight_chunk(self.engine.current_index)
        self._update_playing_status()

    def _sync_play_button(self):
        """Re-reads engine.state rather than tracking a local flag, so a
        rewind/skip during a pause is reflected correctly instead of
        leaving the button stuck on the wrong label."""
        if self.engine.state == PlaybackState.PLAYING:
            self.play_btn.setText("⏸ Pause")
        elif self.engine.state == PlaybackState.PAUSED:
            self.play_btn.setText("▶ Resume")
        else:
            self.play_btn.setText("▶ Play")

    def _on_stop_clicked(self):
        self.engine.stop()
        if self.synth_worker is not None:
            self.synth_worker.stop()
        self.status_timer.stop()
        self._narration_mark_paused()
        self._clear_highlight()
        self._set_controls_enabled(playing=False)
        self._set_error_style(False)
        self._sync_play_button()
        self.status_label.setText("Idle")

    # ---- engine callbacks (run on the GUI thread via EngineBridge) -------

    def _on_chunk_started(self, idx: int):
        self._set_error_style(False)  # we're making progress again
        self._highlight_chunk(idx)
        self._update_playing_status()

    def _on_chunk_ended(self, idx: int):
        pass  # reserved for future use (e.g. progress bar)

    def _on_playback_finished(self):
        self.status_timer.stop()
        self._clear_highlight()
        self._set_controls_enabled(playing=False)
        self._sync_play_button()
        self._narration_mark_paused()
        stats = self._build_stats_line(narration_elapsed=self._narration_total_elapsed())
        self.status_label.setText(f"Finished \u2022 {stats}")

    def _on_chunk_error(self, idx: int, message: str):
        """From AudioEngine's on_error -- a chunk's audio never showed up
        in time, or the output device couldn't be opened. Playback has
        already stopped by the time this fires."""
        self.status_timer.stop()
        self._narration_mark_paused()
        self._set_error_style(True)
        suffix = f" \u2022 {self._last_stats_line}" if self._last_stats_line else ""
        self.status_label.setText(f"Playback error on chunk {idx + 1}: {message}{suffix}")
        self._clear_highlight()
        self._set_controls_enabled(playing=False)
        self._sync_play_button()

    def _on_synth_error(self, idx: int, message: str):
        """From SynthWorker -- one chunk failed to synthesize. Playback
        keeps going; this just flags it. If the AudioEngine ends up
        waiting on this exact chunk it'll time out and raise its own
        on_error, which fully stops playback."""
        self._set_error_style(True)
        self.status_label.setText(f"TTS error on chunk {idx + 1}: {message}")

    def _on_chunk_synthesized(self, idx: int, wav_bytes: bytes):
        self._synth_done_count += 1

    def _on_synthesis_finished(self):
        if self._synth_start_time is not None:
            self._synth_total_elapsed = time.monotonic() - self._synth_start_time
        if self.engine.is_fully_synthesized():
            self._set_save_audio_ready(True)

    # ---- status label -------------------------------------------------------

    def _set_error_style(self, is_error: bool):
        self.status_label.setStyleSheet(_STATUS_STYLE_ERROR if is_error else _STATUS_STYLE_NORMAL)

    def _on_text_changed(self):
        """Character count while idle. During/after playback the text box
        is read-only and this won't fire from user input, so it's safe to
        just always show the count when the engine isn't playing."""
        if self.engine.state != PlaybackState.STOPPED:
            return
        self._set_save_audio_ready(False)  # text no longer matches any synthesized audio
        self._set_error_style(False)
        n = len(self.text_edit.toPlainText())
        self.status_label.setText(f"{n} Char{'s' if n != 1 else ''}")

    def _render_time_str(self, total: int) -> str:
        """'Rendering' here means KoboldCpp synthesizing the remaining
        chunks -- independent of playback, since the server can run
        faster or slower than real-time speech."""
        if self.engine.is_fully_synthesized():
            if self._synth_total_elapsed is not None:
                return f"Rendering done in {_fmt_duration(self._synth_total_elapsed)}"
            return "Rendering done!"
        if self._synth_start_time is not None and self._synth_done_count > 0:
            elapsed = time.monotonic() - self._synth_start_time
            rate = elapsed / self._synth_done_count  # seconds per chunk
            remaining_chunks = max(0, total - self._synth_done_count)
            render_remaining = rate * remaining_chunks
            return f"Rendering: ~{_fmt_duration(render_remaining)} left"
        return "Rendering…"

    def _build_stats_line(
        self,
        cur_words: int | None = None,
        remaining: float | None = None,
        narration_elapsed: float | None = None,
    ) -> str:
        """Builds the trailing metrics (characters/words/time/render/mem).
        `cur_words` includes the current chunk's word count when given
        (live playback); omitted for a finished/idle summary. `remaining`
        is the 'time left' figure in seconds, or omitted entirely if not
        meaningful yet. `narration_elapsed`, when given, takes priority over
        `remaining` and shows "Narration done in X" instead -- used once
        playback has actually finished."""
        total = len(self.chunks)
        mem_str = _fmt_bytes(self.engine.get_audio_memory_bytes())
        render_str = self._render_time_str(total) if total else "Rendering done"
        parts = [""]
        if cur_words is not None:
            parts.append(f"{cur_words} Word chunk / {self.total_words} Words Total")
        else:
            parts.append(f"{self.total_words}words total")
        if narration_elapsed is not None:
            parts.append(f"Narration done in {_fmt_duration(narration_elapsed)}")
        elif remaining is not None:
            parts.append(f"Narration: ~{_fmt_duration(remaining)} left")
        parts.append(render_str)
        parts.append(f"Audio mem {mem_str}")
        ## Character count at the end, next to audio memory for at a glance comparison
        parts.append(f"{self.total_chars} Chars")
        return " \u2022 ".join(parts)

    def _narration_reset(self):
        self._narration_start_time = None
        self._narration_elapsed = 0.0

    def _narration_mark_playing(self):
        """Call whenever playback becomes actively playing (start or
        resume-from-pause). Idempotent -- safe to call even if already
        counting."""
        if self._narration_start_time is None:
            self._narration_start_time = time.monotonic()

    def _narration_mark_paused(self):
        """Call whenever playback stops actively playing (pause, stop,
        finish, or error). Folds the just-elapsed stretch into the running
        total and stops the clock until _narration_mark_playing resumes it."""
        if self._narration_start_time is not None:
            self._narration_elapsed += time.monotonic() - self._narration_start_time
            self._narration_start_time = None

    def _narration_total_elapsed(self) -> float:
        total = self._narration_elapsed
        if self._narration_start_time is not None:
            total += time.monotonic() - self._narration_start_time
        return total

    def _update_playing_status(self):
        """Recomputed from scratch on every timer tick (and on chunk-start)
        rather than incrementally, so rewind/skip/pause are automatically
        reflected without any extra bookkeeping."""
        if not self.chunks:
            return
        idx, frame_pos, samplerate, durations = self.engine.get_progress_snapshot()
        total = len(self.chunks)
        if idx < 0 or idx >= total:
            return

        elapsed = 0.0
        for i in range(idx):
            elapsed += (durations[i] or 0.0) + self.chunks[i].pause_ms / 1000.0
        if samplerate:
            elapsed += frame_pos / samplerate

        known_total = 0.0
        known_words = 0
        for i, d in enumerate(durations):
            if d is not None:
                known_total += d + self.chunks[i].pause_ms / 1000.0
                known_words += self.chunk_word_counts[i]
        rate = (known_total / known_words) if known_words else _FALLBACK_SECONDS_PER_WORD
        unknown_words = max(0, self.total_words - known_words)
        projected_total = known_total + unknown_words * rate
        remaining = max(0.0, projected_total - elapsed)

        cur_words = self.chunk_word_counts[idx]
        self._last_stats_line = self._build_stats_line(cur_words=cur_words, remaining=remaining)
        state_word = "Paused" if self.engine.state == PlaybackState.PAUSED else "TTS chunk"
        self.status_label.setText(f"{state_word} {idx + 1}/{total} {self._last_stats_line}")

    # ---- saving -------------------------------------------------------------

    def _on_save_clicked(self):
        result = self.engine.render_full_audio()
        if result is None:
            QMessageBox.warning(
                self, "Not ready",
                "Not all chunks have finished synthesizing yet (or one of them errored out).",
            )
            return
        data, samplerate = result

        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Save audio", "reading.wav", "WAV Audio (*.wav);;MP3 Audio (*.mp3)"
        )
        if not path:
            return

        # Qt doesn't reliably rewrite the typed filename's extension when
        # the user only changes the filter dropdown, so the selected
        # filter -- not whatever extension happens to already be in the
        # text box -- decides the actual format and gets forced onto path.
        want_mp3 = "mp3" in selected_filter.lower()
        path = _with_extension(path, ".mp3" if want_mp3 else ".wav")

        if want_mp3:
            self._save_as_mp3(data, samplerate, path)
        else:
            sf.write(path, data, samplerate)
            self.status_label.setText(f"Saved {path}")

    def _save_as_mp3(self, data: np.ndarray, samplerate: int, path: str):
        try:
            import lameenc
        except ImportError:
            wav_path = _with_extension(path, ".wav")
            sf.write(wav_path, data, samplerate)
            QMessageBox.information(
                self, "MP3 support not installed",
                "Saving as MP3 needs the 'lameenc' package "
                "(pip install lameenc). Saved as WAV instead:\n" + wav_path,
            )
            self.status_label.setText(f"Saved {wav_path} (mp3 support not installed)")
            return

        pcm = np.clip(data, -1.0, 1.0)
        pcm16 = (pcm * 32767.0).astype(np.int16)
        channels = pcm16.shape[1] if pcm16.ndim > 1 else 1

        encoder = lameenc.Encoder()
        encoder.set_bit_rate(128)
        encoder.set_in_sample_rate(samplerate)
        encoder.set_channels(channels)
        encoder.set_quality(2)  # 2 = high quality (slower); 7 = fastest
        mp3_bytes = encoder.encode(pcm16.tobytes())
        mp3_bytes += encoder.flush()

        with open(path, "wb") as f:
            f.write(mp3_bytes)
        self.status_label.setText(f"Saved {path}")

    # ---- highlighting -------------------------------------------------------

    def _highlight_spans(self, spans):
        selections = []
        for start, end in spans:
            cursor = QTextCursor(self.text_edit.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(QColor(255, 213, 79))
            selection.format.setForeground(QColor(0, 0, 0))
            selections.append(selection)
        self.text_edit.setExtraSelections(selections)

    def _highlight_chunk(self, idx: int):
        if idx < 0 or idx >= len(self.chunks):
            return
        chunk = self.chunks[idx]
        self._highlight_spans(chunk.spans)
        if chunk.spans:
            cursor = QTextCursor(self.text_edit.document())
            cursor.setPosition(chunk.spans[-1][1])
            self.text_edit.setTextCursor(cursor)
            self.text_edit.ensureCursorVisible()

    def _clear_highlight(self):
        self.text_edit.setExtraSelections([])

    def closeEvent(self, event):
        self.engine.shutdown()
        if self.synth_worker is not None:
            self.synth_worker.stop()
            self.synth_worker.wait(1000)
        super().closeEvent(event)


# The default theme's scrollbar handle renders darker than its track,
# which reads oddly -- this swaps it: a darker track with a lighter handle.
_SCROLLBAR_QSS = """
QScrollBar:vertical {
    background: #2b2b2b;
    width: 14px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: #808080;
    min-height: 24px;
    border-radius: 5px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    background: #2b2b2b;
    height: 14px;
    margin: 0px;
}
QScrollBar::handle:horizontal {
    background: #808080;
    min-width: 24px;
    border-radius: 5px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
"""


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(_SCROLLBAR_QSS)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
