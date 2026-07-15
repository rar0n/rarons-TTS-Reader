"""
Shared building blocks used across tabs: color/style constants, small
formatting/JSON helpers, and a couple of generic QWidget subclasses.

Nothing in here is specific to TTS -- a future STT tab should be able to
reuse all of it (styled buttons, status labels, the eliding label, the
zoomable text edit, settings.json read/merge helpers, etc.) rather than
duplicating it.
"""

import os
import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QTextEdit, QSizePolicy

# Where every tab's settings get saved to / auto-loaded from on startup.
# Each tab merges its own keys into this same file via _save_json_merged
# rather than owning separate files, so one settings.json covers everything.
DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"


# ---- status label colors -------------------------------------------------
# Each tab gets its own muted background so they're distinguishable at a
# glance; the error state (Narration tab only, so far) stays the
# brighter/more alarming dark red it always was, distinct from Settings'
# muted dark red "normal" state.
STATUS_STYLE_NARRATION = "background-color: #223655; color: white; padding: 4px;"
STATUS_STYLE_SEED_VAULT = "background-color: #2b3f2e; color: white; padding: 4px;"
STATUS_STYLE_SETTINGS = "background-color: #4a2c2c; color: white; padding: 4px;"
STATUS_STYLE_STT = "background-color: #4a3f5e; color: white; padding: 4px;"
STATUS_STYLE_ERROR = "background-color: #8b0000; color: white; padding: 4px;"

# Shared desaturated/darkened RGB (and amber) palette for buttons across all tabs.
COLOR_DARK_BLUE = "#3a4864"    # Play / pause / resume / refresh, Reset to Defaults
COLOR_DARK_GREEN = "#3f5442"   # "ready" / save / Store seed / save actions
COLOR_DARK_RED = "#5c3a3a"     # stop / remove / Randomize seed / destructive-ish actions
COLOR_DARK_AMBER = "#6b4d2c"   # active seek controls -- Rewind / Forward while playing
COLOR_DARK_PURPLE = "#4a3f5e"  # STT tab's Transcribe button -- matches STATUS_STYLE_STT's hue

# The same four hues again, pushed further toward the background -- used
# for the *disabled* look of a button instead of Qt's stock light-grey,
# which looks boring.
COLOR_DISABLED_BLUE = "#2a323f"
COLOR_DISABLED_GREEN = "#2c332d"
COLOR_DISABLED_RED = "#3a2c2c"
COLOR_DISABLED_AMBER = "#3c3122"
COLOR_DISABLED_PURPLE = "#2e2937"

DISABLED_TEXT_COLOR = "#787878"

# A plain uniform fixed height for button rows (Seed Vault, Settings, and
# similar) where all the buttons should just match -- some emoji glyphs
# (e.g. 🗑) render taller than others (⇦, 💾) in most fonts, so leaving
# height to auto-size off the text makes buttons in the same row mismatched.
STANDARD_BTN_HEIGHT = 30

# Maps each "on" color to its own muted "disabled" counterpart, so
# btn_style can be called with just the on-color and still pick a
# sensibly-matched disabled shade instead of a generic one.
_DISABLED_COLOR_MAP = {
    COLOR_DARK_BLUE: COLOR_DISABLED_BLUE,
    COLOR_DARK_GREEN: COLOR_DISABLED_GREEN,
    COLOR_DARK_RED: COLOR_DISABLED_RED,
    COLOR_DARK_AMBER: COLOR_DISABLED_AMBER,
    COLOR_DARK_PURPLE: COLOR_DISABLED_PURPLE,
}


def btn_style(color: str, disabled_color: str = None) -> str:
    """Builds a stylesheet that keeps `color` while the button is enabled
    and automatically swaps to a more muted `disabled_color` once it
    isn't -- via QPushButton:disabled -- so a button only ever needs
    setEnabled() calls to look "greyed out", no manual stylesheet
    toggling required to fake that look."""
    if disabled_color is None:
        disabled_color = _DISABLED_COLOR_MAP.get(color, "#2e2e2e")
    return (
        f"QPushButton {{ background-color: {color}; color: white; padding: 4px; }}"
        f"QPushButton:disabled {{ background-color: {disabled_color}; color: {DISABLED_TEXT_COLOR}; }}"
    )


def progress_style(background: str, chunk_color: str) -> str:
    """Builds a stylesheet for a QProgressBar with a flat `background`
    (the unfilled track) and `chunk_color` for the filled portion."""
    return (
        f"QProgressBar {{ background-color: {background}; color: white; "
        f"border: none; padding: 1px; text-align: center; }}"
        f"QProgressBar::chunk {{ background-color: {chunk_color}; }}"
    )


# ---- formatting helpers ---------------------------------------------------

def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 ** 2:.1f} MB"


def with_extension(path: str, ext: str) -> str:
    """Returns `path` with its extension forced to `ext` (e.g. '.mp3'),
    replacing whatever extension (if any) is already there."""
    base, _ = os.path.splitext(path)
    return base + ext


def srt_timestamp(seconds: float) -> str:
    """Formats a seconds offset as an SRT timestamp: HH:MM:SS,mmm."""
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def print_error(message: str):
    """Echoes an error-status line to stdout/terminal, in addition to
    wherever it's also shown in the GUI -- useful when running from a
    console and the error message is longer than can be shown in the GUI"""
    print(f"[ERROR] {message}")


# ---- settings.json helpers -------------------------------------------------

def load_json_dict(path: Path) -> dict:
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


def save_json_merged(path: Path, updates: dict):
    """Merges `updates` into whatever's already on disk at `path` instead
    of overwriting the whole file, so different tabs can all save to the
    same settings.json without erasing each other's top-level keys."""
    data = load_json_dict(path)
    data.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---- shared widgets ---------------------------------------------------

class ElidingLabel(QLabel):
    """A QLabel that elides overly-long text with a trailing "…" instead
    of forcing the window to grow to fit it -- a plain QLabel's minimum
    size hint tracks its full (unwrapped) text width, which was making
    the window balloon out on long status lines (lots of stats packed
    onto one line, or a long file path). The full text is still always
    available via the tooltip.

    A fixed/expanding-width sibling widget in the same layout row would
    normally stretch to claim the horizontal size Ignored below gives up,
    but the status label always sits alone in its own row here, so that's
    not a concern."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._full_text = ""
        # Ignored (rather than the QLabel default of Preferred) means this
        # widget's size hint no longer sets a floor on the window's
        # minimum width -- it can be squeezed down to elide instead of
        # forcing the layout to grow.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setText(self, text):
        self._full_text = text
        self.setToolTip(text)
        super().setText(self._elided(text))

    def _elided(self, text: str) -> str:
        return self.fontMetrics().elidedText(text, Qt.ElideRight, max(self.width(), 0))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._full_text:
            super().setText(self._elided(self._full_text))


class ZoomableTextEdit(QTextEdit):
    """A QTextEdit where holding Ctrl while scrolling changes the font size
    instead of scrolling the view -- Qt doesn't expose this as a setting,
    so it's done by hand here, one point size per notch, clamped so it
    can't be scrolled down to unreadable or up to absurd.

    Also accepts a dropped file: instead of Qt's default of inserting the
    dropped text/URL literally, dropping a local file here loads that
    file's contents into the box (replacing whatever was there), same as
    using a File > Open would if this app had one."""

    MIN_POINT_SIZE = 6
    MAX_POINT_SIZE = 48

    file_dropped = Signal(str)  # emits the dropped file's path

    # Transport shortcuts usable while focus is in the text box: Ctrl+Enter
    # to start playback, and -- once playback has made the box read-only --
    # Space/Left/Right/Esc for pause-resume/rewind/forward/stop. Emitted as
    # signals rather than acted on directly here, so the owning tab's
    # existing button handlers stay the single source for what each action
    # does.
    play_requested = Signal()
    pause_resume_requested = Signal()
    rewind_requested = Signal()
    forward_requested = Signal()
    stop_requested = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.play_requested.emit()
            event.accept()
            return
        if self.isReadOnly():
            # Read-only only while playback is active -- so these keys are
            # transport shortcuts here rather than the text-editing keys
            # they'd normally be (space inserting a space, arrows moving
            # the cursor, etc.), which don't apply while the box is just a
            # read-only "subtitle" view anyway.
            if key == Qt.Key_Space:
                self.pause_resume_requested.emit()
                event.accept()
                return
            if key == Qt.Key_Left:
                self.rewind_requested.emit()
                event.accept()
                return
            if key == Qt.Key_Right:
                self.forward_requested.emit()
                event.accept()
                return
            if key == Qt.Key_Escape:
                self.stop_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)

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

    def _droppable_local_file(self, mime_data) -> str | None:
        """Returns the path of the first local-file URL in `mime_data`, or
        None if there isn't one (or the box is read-only, e.g. mid-
        playback -- dropping a new file out from under an active reading
        would be more surprising than helpful)."""
        if self.isReadOnly() or not mime_data.hasUrls():
            return None
        for url in mime_data.urls():
            if url.isLocalFile():
                return url.toLocalFile()
        return None

    def dragEnterEvent(self, event):
        if self._droppable_local_file(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._droppable_local_file(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        path = self._droppable_local_file(event.mimeData())
        if path:
            event.acceptProposedAction()
            self.file_dropped.emit(path)
            return
        super().dropEvent(event)
