"""
AboutTab: what the program is for, a link to its GitHub page, and the
MIT license text (read from License.txt next to the program).
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QGroupBox, QPlainTextEdit

GITHUB_URL = "https://github.com/rar0n/rarons-TTS-Reader"
_LICENSE_PATH = Path(__file__).resolve().parent / "License.txt"

_DESCRIPTION = (
    "A desktop front end for KoboldCpp's text-to-speech and speech-to-text "
    "API, made for <b>long-form</b> work:<br><br>"
    "&bull; <b>TTS (Narration)</b> &ndash; reads long texts aloud, one chunk at a "
    "time, with live highlighting. Save the result as audio (wav / mp3) and "
    "optionally subtitles (.srt).<br>"
    "&bull; <b>STT (Transcription)</b> &ndash; transcribes long audio or video files "
    "by splitting them at silences into pieces Whisper can handle. Save as "
    "plain text or subtitles (.srt).<br><br>"
    "It's just a wrapper: all the actual speech work is done by KoboldCpp, "
    "which has to be running with the TTS and / or Whisper models loaded."
)


class AboutTab(QWidget):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        heading = QLabel(f"<h2>{title}</h2>")
        heading.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(heading)

        desc = QLabel(_DESCRIPTION)
        desc.setTextFormat(Qt.TextFormat.RichText)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        link = QLabel(f'GitHub: <a href="{GITHUB_URL}">{GITHUB_URL}</a>')
        link.setTextFormat(Qt.TextFormat.RichText)
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        layout.addWidget(link)

        license_group = QGroupBox("License")
        license_layout = QVBoxLayout(license_group)
        self.license_edit = QPlainTextEdit()
        self.license_edit.setReadOnly(True)
        self.license_edit.setPlainText(self._read_license())
        license_layout.addWidget(self.license_edit)
        layout.addWidget(license_group, 1)

    @staticmethod
    def _read_license() -> str:
        try:
            return _LICENSE_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            return f"MIT License. License.txt wasn't found next to the program; see {GITHUB_URL}"
