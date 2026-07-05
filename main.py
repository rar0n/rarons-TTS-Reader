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
import os
import time

import numpy as np
import soundfile as sf

from PySide6.QtCore import QObject, Signal, Qt, QThread, QTimer
from PySide6.QtGui import QTextCursor, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLineEdit, QLabel, QFormLayout, QMessageBox,
    QComboBox, QFileDialog,
)

from chunker import chunk_text, Chunk
from tts_client import KoboldTTSClient, TTSError
from synth_worker import SynthWorker
from audio_engine import AudioEngine, PlaybackState

# Status label colors for the two states it can be in.
_STATUS_STYLE_NORMAL = "background-color: #204090; color: white; padding: 4px;"
_STATUS_STYLE_ERROR = "background-color: #8b0000; color: white; padding: 4px;"

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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("raron's TTS Reader v0.3 (2026.07.05)")
        self.resize(820, 600)

        self.chunks: list[Chunk] = []
        self.chunk_word_counts: list[int] = []
        self.total_words = 0
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
        self.text_edit = ZoomableTextEdit()
        self.text_edit.setPlaceholderText("Paste or type the text you want read aloud... (Ctrl+scroll to resize text)")
        self.text_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.text_edit)

        # Status label
        self.status_label = QLabel("0 characters")
        self.status_label.setStyleSheet(_STATUS_STYLE_NORMAL)
        layout.addWidget(self.status_label)

        # Transport controls
        controls = QHBoxLayout()
        self.play_btn = QPushButton("▶ Play")
        self.pause_btn = QPushButton("⏸ Pause")
        self.rewind_btn = QPushButton("⏮ Rewind")
        self.forward_btn = QPushButton("⏭ Skip")
        self.stop_btn = QPushButton("⏹ Stop")
        self.save_btn = QPushButton("💾 Save Audio")

        self.play_btn.clicked.connect(self._on_play_clicked)
        self.pause_btn.clicked.connect(self._on_pause_clicked)
        self.rewind_btn.clicked.connect(self._on_rewind_clicked)
        self.forward_btn.clicked.connect(self._on_forward_clicked)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.save_btn.clicked.connect(self._on_save_clicked)
        self.save_btn.setEnabled(False)

        for btn in (self.play_btn, self.pause_btn, self.rewind_btn,
                    self.forward_btn, self.stop_btn, self.save_btn):
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

        self.chunk_word_counts = [len(c.text.split()) for c in self.chunks]
        self.total_words = sum(self.chunk_word_counts)
        self._synth_start_time = time.monotonic()
        self._synth_done_count = 0
        self._synth_total_elapsed = None
        self._last_stats_line = ""

        voice = self.voice_combo.currentText().strip() or "default"
        client = KoboldTTSClient(
            base_url=self.url_edit.text().strip() or "http://127.0.0.1:5001",
            voice=voice,
        )

        self.engine.set_chunks(self.chunks)
        self._set_controls_enabled(playing=True)
        self.save_btn.setEnabled(False)
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

        self.engine.play_from(0)
        self.status_timer.start()

    def _on_pause_clicked(self):
        self.engine.toggle_pause()
        self._sync_pause_button()

    def _on_rewind_clicked(self):
        self.engine.rewind(1)
        self._sync_pause_button()

    def _on_forward_clicked(self):
        self.engine.skip_forward(1)
        self._sync_pause_button()

    def _sync_pause_button(self):
        """Re-reads engine.state rather than tracking a local flag, so a
        rewind/skip during a pause -- which resumes playback under the
        hood -- is reflected correctly instead of leaving the button
        stuck on 'Resume' while audio is actually playing again."""
        if self.engine.state == PlaybackState.PAUSED:
            self.pause_btn.setText("▶ Resume")
        else:
            self.pause_btn.setText("⏸ Pause")

    def _on_stop_clicked(self):
        self.engine.stop()
        if self.synth_worker is not None:
            self.synth_worker.stop()
        self.status_timer.stop()
        self._clear_highlight()
        self._set_controls_enabled(playing=False)
        self._set_error_style(False)
        self.pause_btn.setText("⏸ Pause")
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
        self.pause_btn.setText("⏸ Pause")
        suffix = f" \u2022 {self._last_stats_line}" if self._last_stats_line else ""
        self.status_label.setText(f"Finished{suffix}")

    def _on_chunk_error(self, idx: int, message: str):
        """From AudioEngine's on_error -- a chunk's audio never showed up
        in time, or the output device couldn't be opened. Playback has
        already stopped by the time this fires."""
        self.status_timer.stop()
        self._set_error_style(True)
        suffix = f" \u2022 {self._last_stats_line}" if self._last_stats_line else ""
        self.status_label.setText(f"Playback error on chunk {idx + 1}: {message}{suffix}")
        self._clear_highlight()
        self._set_controls_enabled(playing=False)
        self.pause_btn.setText("⏸ Pause")

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
            self.save_btn.setEnabled(True)

    # ---- status label -------------------------------------------------------

    def _set_error_style(self, is_error: bool):
        self.status_label.setStyleSheet(_STATUS_STYLE_ERROR if is_error else _STATUS_STYLE_NORMAL)

    def _on_text_changed(self):
        """Character count while idle. During/after playback the text box
        is read-only and this won't fire from user input, so it's safe to
        just always show the count when the engine isn't playing."""
        if self.engine.state != PlaybackState.STOPPED:
            return
        self.save_btn.setEnabled(False)  # text no longer matches any synthesized audio
        self._set_error_style(False)
        n = len(self.text_edit.toPlainText())
        self.status_label.setText(f"{n} character{'s' if n != 1 else ''}")

    def _render_time_str(self, total: int) -> str:
        """'Rendering' here means KoboldCpp synthesizing the remaining
        chunks -- independent of playback, since the server can run
        faster or slower than real-time speech."""
        if self.engine.is_fully_synthesized():
            if self._synth_total_elapsed is not None:
                return f"Rendering done in {_fmt_duration(self._synth_total_elapsed)}"
            return "Rendering done"
        if self._synth_start_time is not None and self._synth_done_count > 0:
            elapsed = time.monotonic() - self._synth_start_time
            rate = elapsed / self._synth_done_count  # seconds per chunk
            remaining_chunks = max(0, total - self._synth_done_count)
            render_remaining = rate * remaining_chunks
            return f"Rendering ~{_fmt_duration(render_remaining)} left"
        return "Rendering…"

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

        mem_str = _fmt_bytes(self.engine.get_audio_memory_bytes())
        render_str = self._render_time_str(total)
        prefix = "[Paused] " if self.engine.state == PlaybackState.PAUSED else ""
        cur_words = self.chunk_word_counts[idx]
        self._last_stats_line = (
            f"{cur_words}w chunk / {self.total_words}w total \u2022 "
            f"~{_fmt_duration(remaining)} left \u2022 "
            f"{render_str} \u2022 "
            f"audio mem {mem_str}"
        )
        self.status_label.setText(f"{prefix}Speaking {idx + 1}/{total} \u2022 {self._last_stats_line}")

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


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
