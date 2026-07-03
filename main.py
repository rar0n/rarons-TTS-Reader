"""
TTS Reader - Read long-form text aloud (KoboldCpp API)

- Built to work around KoboldCpp's tendency to drift in voice/speed
  and outright stop on long single-shot TTS requests.

A small Python app that reads pasted text aloud through KoboldCpp's
TTS API, with live highlighting one sentence at a time, better pauses
(hopefully), and basic Play, Pause/Resume, Rwd/Fwd and Stop controls.

There's no "continue from selection" function, for now just
delete the preceding text in the textbox if need be after a stop.

As I found KoboldCpp TTS flunked out on longer-form text, I vibe-coded this
with Claude Sonnet 5 on Extra / High  and Medium effort, over a few free
sessions (which is awesome btw, so thanks to Anthropic for that!).

So KoboldCpp only gets one sentence at a time, which works much better.

 ( Btw, I have no idea exactly - how - long a text it can take, depends on  )
 ( your system memory I think, as the app gets the audio continuously ahead )
 ( of the speech, unless it's inferencing speech slower than real-time       )

    2026 raron ( But mostly Claude :) )

  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

  Use at your own risk, modify as you see fit.

  That's all.

"""

import sys

from PySide6.QtCore import QObject, Signal, Qt, QThread
from PySide6.QtGui import QTextCursor, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLineEdit, QLabel, QFormLayout, QMessageBox,
    QComboBox,
)

from chunker import chunk_text, Chunk
from tts_client import KoboldTTSClient, TTSError
from synth_worker import SynthWorker
from audio_engine import AudioEngine, PlaybackState


class EngineBridge(QObject):
    """Relays AudioEngine callbacks (which fire on background threads)
    into Qt signals, so the GUI thread can safely react to them."""
    chunk_started = Signal(int)
    chunk_ended = Signal(int)
    playback_finished = Signal()


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TTS Reader (KoboldCpp)")
        self.resize(820, 600)

        self.chunks: list[Chunk] = []
        self.synth_worker: SynthWorker | None = None
        self.speaker_worker: SpeakerFetchWorker | None = None

        self.bridge = EngineBridge()
        self.bridge.chunk_started.connect(self._on_chunk_started)
        self.bridge.chunk_ended.connect(self._on_chunk_ended)
        self.bridge.playback_finished.connect(self._on_playback_finished)

        self.engine = AudioEngine(
            on_chunk_start=lambda i: self.bridge.chunk_started.emit(i),
            on_chunk_end=lambda i: self.bridge.chunk_ended.emit(i),
            on_finished=lambda: self.bridge.playback_finished.emit(),
        )

        self._build_ui()
        self._refresh_voices()  # populate the dropdown on startup

    # ---- UI construction --------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

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
        voice_row.addWidget(self.voice_combo, stretch=1)
        voice_row.addWidget(self.refresh_voices_btn)
        form.addRow("Voice:", voice_row)

        layout.addLayout(form)

        # Text area (input before playback, highlighted "subtitle" view during)
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Paste or type the text you want read aloud...")
        layout.addWidget(self.text_edit)

        # Status label
        self.status_label = QLabel("Idle")
        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet("background-color: #204090; color: white; padding: 4px;")
        layout.addWidget(self.status_label)

        # Transport controls
        controls = QHBoxLayout()
        self.play_btn = QPushButton("▶ Play")
        self.pause_btn = QPushButton("⏸ Pause")
        self.rewind_btn = QPushButton("⏮ Rewind")
        self.forward_btn = QPushButton("⏭ Skip")
        self.stop_btn = QPushButton("⏹ Stop")

        self.play_btn.clicked.connect(self._on_play_clicked)
        self.pause_btn.clicked.connect(self._on_pause_clicked)
        self.rewind_btn.clicked.connect(lambda: self.engine.rewind(1))
        self.forward_btn.clicked.connect(lambda: self.engine.skip_forward(1))
        self.stop_btn.clicked.connect(self._on_stop_clicked)

        for btn in (self.play_btn, self.pause_btn, self.rewind_btn,
                    self.forward_btn, self.stop_btn):
            controls.addWidget(btn)
        layout.addLayout(controls)

        self._set_controls_enabled(playing=False)

    def _set_controls_enabled(self, playing: bool):
        self.pause_btn.setEnabled(playing)
        self.rewind_btn.setEnabled(playing)
        self.forward_btn.setEnabled(playing)
        self.stop_btn.setEnabled(playing)
        self.text_edit.setReadOnly(playing)

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

    # ---- transport handlers -----------------------------------------------

    def _on_play_clicked(self):
        if self.engine.state == PlaybackState.PAUSED:
            self.engine.resume()
            self.pause_btn.setText("⏸ Pause")
            return

        text = self.text_edit.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "No text", "Paste some text first.")
            return

        self.chunks = chunk_text(text)
        if not self.chunks:
            QMessageBox.information(self, "Nothing to read", "Couldn't find any readable text.")
            return

        voice = self.voice_combo.currentText().strip() or "default"
        client = KoboldTTSClient(
            base_url=self.url_edit.text().strip() or "http://127.0.0.1:5001",
            voice=voice,
        )

        self.engine.set_chunks(self.chunks)
        self._set_controls_enabled(playing=True)
        self.status_label.setText(f"Synthesizing… 0/{len(self.chunks)}")

        if self.synth_worker is not None:
            self.synth_worker.stop()
            self.synth_worker.wait(500)

        self.synth_worker = SynthWorker(client, self.chunks)
        self.synth_worker.chunk_ready.connect(self.engine.feed_audio)
        self.synth_worker.error.connect(self._on_synth_error)
        self.synth_worker.start()

        self.engine.play_from(0)

    def _on_pause_clicked(self):
        self.engine.toggle_pause()
        if self.engine.state == PlaybackState.PAUSED:
            self.pause_btn.setText("▶ Resume")
            self.status_label.setText(self.status_label.text() + "  [paused]")
        else:
            self.pause_btn.setText("⏸ Pause")

    def _on_stop_clicked(self):
        self.engine.stop()
        if self.synth_worker is not None:
            self.synth_worker.stop()
        self._clear_highlight()
        self._set_controls_enabled(playing=False)
        self.pause_btn.setText("⏸ Pause")
        self.status_label.setText("Idle")

    # ---- engine callbacks (run on the GUI thread via EngineBridge) -------

    def _on_chunk_started(self, idx: int):
        total = len(self.chunks)
        self.status_label.setText(f"Speaking {idx + 1}/{total}")
        self._highlight_chunk(idx)

    def _on_chunk_ended(self, idx: int):
        pass  # reserved for future use (e.g. progress bar)

    def _on_playback_finished(self):
        self._clear_highlight()
        self._set_controls_enabled(playing=False)
        self.pause_btn.setText("⏸ Pause")
        self.status_label.setText("Finished")

    def _on_synth_error(self, idx: int, message: str):
        self.status_label.setText(f"TTS error on chunk {idx}: {message}")

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


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
