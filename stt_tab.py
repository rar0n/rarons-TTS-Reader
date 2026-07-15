"""
STTTab: Speech-To-Text tab. Pick an audio file, send it to KoboldCpp's
Whisper endpoint (/api/extra/transcribe), and show the returned text.

Two modes (toggle lives on the STT Settings tab now, along with language
code and suppress-non-speech):
- Single-shot: whole file sent in one request. Fine for short clips, but
  whisper.cpp only looks at ~30s per internal window, so longer files get
  silently truncated/garbled.
- Chunked (default): splits the file at silence boundaries into pieces
  first (speaker-speed-preset-dependent target/range), transcribes each
  in turn, and stitches the results back together with real timestamps --
  required for anything over roughly 30s, and what makes SRT export
  possible.

On top of chunked transcription, two independent formatting passes are
available:
- Sentence/paragraph newlines in the live text box and plain-text save
  (never in SRT, where each entry is already its own block). Naive: a
  newline after '.'/'!'/'?', a blank line where the gap between two
  chunks was long enough to count as a paragraph break.
- Subtitle segmentation (STT Settings tab): splits an over-long chunk's
  text into several shorter SRT entries sized for on-screen reading.

Accepts WAV directly; anything else (mp3, m4a, mp4/video, flac, ...) gets
transcoded to 16kHz mono WAV via ffmpeg first (audio_convert.py), always
to a real seekable file rather than piped through stdout -- piping means
ffmpeg can't seek back to patch the WAV header's data-size field once
done, which was producing wildly wrong reported durations (and, past the
real audio, empty/garbage frames) for mp3/mp4 input specifically.

No microphone capture -- file-based only, since testing so far has no
mic available.
"""

import os
import time
import wave
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton,
    QFileDialog, QMessageBox, QProgressBar,
)

from stt_client import KoboldSTTClient, STTError
from audio_convert import to_wav_file, AudioConvertError
from audio_chunker import (
    ChunkerConfig, Gap, chunk_audio_file, iter_gaps, wav_duration_s,
    split_chunk_for_subtitles, apply_subtitle_linger,
)

from ui_common import (
    STATUS_STYLE_STT, COLOR_DARK_BLUE, COLOR_DARK_GREEN, COLOR_DARK_RED,
    COLOR_DARK_PURPLE, STANDARD_BTN_HEIGHT, btn_style, progress_style,
    print_error, fmt_duration, ElidingLabel, ZoomableTextEdit, srt_timestamp,
)


class TranscribeWorker(QThread):
    """Single-shot: sends the whole file in one request. No timestamps."""

    finished = Signal(str)   # transcribed text
    failed = Signal(str)     # error message

    def __init__(self, client: KoboldSTTClient, wav_bytes: bytes,
                 langcode: str, suppress_non_speech: bool, parent=None):
        super().__init__(parent)
        self._client = client
        self._wav_bytes = wav_bytes
        self._langcode = langcode
        self._suppress_non_speech = suppress_non_speech

    def run(self):
        try:
            text = self._client.transcribe(
                self._wav_bytes,
                langcode=self._langcode,
                suppress_non_speech=self._suppress_non_speech,
            )
        except STTError as e:
            self.failed.emit(str(e))
            return
        self.finished.emit(text)


class ChunkedTranscribeWorker(QThread):
    """Splits wav_path via audio_chunker, transcribes each chunk in turn
    (sequentially -- KoboldCpp is one local model instance, no benefit to
    firing requests in parallel), and reports progress/timestamps as it
    goes. Chunks are emitted as they complete rather than all at the end,
    so the output box and progress bar update live.

    Also re-scans each chunk's own audio for internal gaps right after
    transcribing it, and includes those with the chunk_done signal --
    used later for subtitle segmentation timing. Cheap to do here (the
    chunk's audio is already in memory) versus re-opening/re-scanning
    from the SRT-save step."""

    chunk_done = Signal(int, float, float, str, object)  # index, start_s, end_s, text, local_gaps (List[Gap])
    progress = Signal(float)                              # 0.0 - 1.0
    failed = Signal(str)
    finished_all = Signal()

    def __init__(self, client: KoboldSTTClient, wav_path: Path,
                 total_duration_s: float, config: ChunkerConfig,
                 langcode: str, suppress_non_speech: bool, parent=None):
        super().__init__(parent)
        self._client = client
        self._wav_path = wav_path
        self._total_duration_s = max(total_duration_s, 0.001)
        self._config = config
        self._langcode = langcode
        self._suppress_non_speech = suppress_non_speech
        self._stop_requested = False

    def request_stop(self):
        """Cooperative cancel -- takes effect after the in-flight chunk's
        request returns, not instantly (no clean way to abort a request
        already sent to KoboldCpp mid-flight)."""
        self._stop_requested = True

    def run(self):
        try:
            chunks = chunk_audio_file(self._wav_path, self._config)
        except Exception as e:
            self.failed.emit(f"Chunking failed: {e}")
            return

        if not chunks:
            self.failed.emit("No speech detected in this file (all silence?).")
            return

        for chunk in chunks:
            if self._stop_requested:
                self.failed.emit("Cancelled.")
                return
            try:
                text = self._client.transcribe(
                    chunk.audio_bytes,
                    langcode=self._langcode,
                    suppress_non_speech=self._suppress_non_speech,
                )
            except STTError as e:
                self.failed.emit(f"Chunk {chunk.index} ({chunk.start_s:.1f}s-{chunk.end_s:.1f}s) failed: {e}")
                return

            try:
                local_gaps = list(iter_gaps(chunk.audio_bytes, self._config))
            except Exception:
                local_gaps = []  # non-fatal -- subtitle segmentation just falls back to word-count timing

            self.chunk_done.emit(chunk.index, chunk.start_s, chunk.end_s, text, local_gaps)
            self.progress.emit(min(1.0, chunk.end_s / self._total_duration_s))

        self.finished_all.emit()


def _format_sentence_breaks(text: str) -> str:
    """Naive sentence-break formatting for the live text box / plain-text
    save only -- inserts a newline after '.', '!', or '?' followed by a
    space. Doesn't understand abbreviations, decimals, or ellipses; a
    first pass, tune-able later. Never applied to SRT text."""
    out = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        out.append(ch)
        if ch in ".!?" and i + 1 < n and text[i + 1] == " ":
            out.append("\n")
            i += 1  # skip the space that followed -- the newline replaces it
        i += 1
    return "".join(out)


class STTTab(QWidget):
    def __init__(self, settings_tab, stt_settings_tab=None, parent=None):
        super().__init__(parent)
        # Reuse the same KoboldCpp URL the Settings tab already has --
        # TTS and STT are the same running KoboldCpp instance, just
        # different endpoints on it, so a second URL field would just be
        # duplicate state to keep in sync.
        self._settings_tab = settings_tab
        # Language code, suppress-non-speech, chunked-mode, subtitle
        # segmentation, and the speaker-speed presets all live on this tab
        # now. Optional -- falls back to sane defaults everywhere below if
        # not wired in.
        self._stt_settings_tab = stt_settings_tab
        if self._stt_settings_tab is not None:
            self._stt_settings_tab.preset_changed.connect(self._on_preset_changed)

        self._worker = None  # TranscribeWorker | ChunkedTranscribeWorker | None
        self._source_path: Path | None = None  # last file transcribed, for Save's default name
        self._temp_wav_path: Path | None = None  # cleaned up after each run, if used
        self._active_config: ChunkerConfig = ChunkerConfig()
        self._last_chunk_end_s: float = 0.0
        # (start_s, end_s, text, local_gaps) per chunk -- chunked mode only
        self._chunk_results: list[tuple[float, float, str, list]] = []

        # Live metrics while transcribing.
        self._transcribe_start_time: float | None = None
        self._last_progress_fraction: float = 0.0
        self._total_duration_s: float = 0.0
        self._base_status: str = ""
        self._metrics_timer = QTimer(self)
        self._metrics_timer.setInterval(1000)
        self._metrics_timer.timeout.connect(self._refresh_status_display)

        self._build_ui()
        self._refresh_active_preset_label()

    # ---- UI construction --------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)

        file_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Path to an audio file…")
        self.path_edit.textChanged.connect(self._on_path_changed)
        file_row.addWidget(QLabel("Audio file:"))
        file_row.addWidget(self.path_edit, 1)
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setStyleSheet(btn_style(COLOR_DARK_BLUE))
        self.browse_btn.setFixedHeight(STANDARD_BTN_HEIGHT)
        self.browse_btn.clicked.connect(self._on_browse_clicked)
        file_row.addWidget(self.browse_btn)
        outer.addLayout(file_row)

        btn_row = QHBoxLayout()
        self.transcribe_btn = QPushButton("Transcribe")
        self.transcribe_btn.setStyleSheet(btn_style(COLOR_DARK_PURPLE))
        self.transcribe_btn.setFixedHeight(STANDARD_BTN_HEIGHT)
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.clicked.connect(self._on_transcribe_clicked)
        btn_row.addWidget(self.transcribe_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet(btn_style(COLOR_DARK_RED))
        self.cancel_btn.setFixedHeight(STANDARD_BTN_HEIGHT)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self.cancel_btn)

        self.save_txt_btn = QPushButton("Save Text…")
        self.save_txt_btn.setStyleSheet(btn_style(COLOR_DARK_BLUE))
        self.save_txt_btn.setFixedHeight(STANDARD_BTN_HEIGHT)
        self.save_txt_btn.setEnabled(False)
        self.save_txt_btn.clicked.connect(self._on_save_text_clicked)
        btn_row.addWidget(self.save_txt_btn)

        self.save_srt_btn = QPushButton("Save SRT…")
        self.save_srt_btn.setStyleSheet(btn_style(COLOR_DARK_BLUE))
        self.save_srt_btn.setFixedHeight(STANDARD_BTN_HEIGHT)
        self.save_srt_btn.setEnabled(False)
        self.save_srt_btn.setToolTip("Available after a chunked transcription -- needs per-chunk timestamps.")
        self.save_srt_btn.clicked.connect(self._on_save_srt_clicked)
        btn_row.addWidget(self.save_srt_btn)

        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setStyleSheet(progress_style("#2b2b2b", COLOR_DARK_PURPLE))
        self.progress_bar.setFixedHeight(18)
        progress_row.addWidget(self.progress_bar, 1)
        outer.addLayout(progress_row)

        # Ctrl+scroll zoom, same as the Narration tab's text box.
        self.output_edit = ZoomableTextEdit()
        self.output_edit.setPlaceholderText("Transcribed text will appear here…")
        self.output_edit.textChanged.connect(self._on_output_text_changed)
        outer.addWidget(self.output_edit, 1)

        status_row = QHBoxLayout()
        self.status_label = ElidingLabel("")
        self.status_label.setStyleSheet(STATUS_STYLE_STT)
        status_row.addWidget(self.status_label, 1)

        self.char_count_label = QLabel("0 characters")
        self.char_count_label.setStyleSheet(STATUS_STYLE_STT)
        status_row.addWidget(self.char_count_label)
        outer.addLayout(status_row)

    # ---- helpers --------------------------------------------------------

    def _make_client(self) -> KoboldSTTClient:
        base_url = self._settings_tab.url_edit.text().strip() or "http://127.0.0.1:5001"
        return KoboldSTTClient(base_url=base_url)

    def _current_chunker_config(self) -> ChunkerConfig:
        if self._stt_settings_tab is not None:
            return self._stt_settings_tab.get_active_config()
        return ChunkerConfig()

    def _current_langcode(self) -> str:
        if self._stt_settings_tab is not None:
            return self._stt_settings_tab.get_langcode()
        return "en"

    def _current_suppress_non_speech(self) -> bool:
        if self._stt_settings_tab is not None:
            return self._stt_settings_tab.get_suppress_non_speech()
        return False

    def _current_chunked_mode(self) -> bool:
        if self._stt_settings_tab is not None:
            return self._stt_settings_tab.get_chunked_mode()
        return True

    def _current_subtitle_segmentation_enabled(self) -> bool:
        if self._stt_settings_tab is not None:
            return self._stt_settings_tab.get_subtitle_segmentation_enabled()
        return True

    def _current_subtitle_snap_to_gaps(self) -> bool:
        if self._stt_settings_tab is not None:
            return self._stt_settings_tab.get_subtitle_snap_to_gaps()
        return True

    def _current_subtitle_max_chars(self) -> int:
        if self._stt_settings_tab is not None:
            return self._stt_settings_tab.get_subtitle_max_chars()
        from audio_chunker import SUBTITLE_MAX_CHARS
        return SUBTITLE_MAX_CHARS

    def _current_subtitle_linger_enabled(self) -> bool:
        if self._stt_settings_tab is not None:
            return self._stt_settings_tab.get_subtitle_linger_enabled()
        return True

    def _current_subtitle_linger_long_pause_s(self) -> float:
        if self._stt_settings_tab is not None:
            return self._stt_settings_tab.get_subtitle_linger_long_pause_ms() / 1000.0
        from audio_chunker import SUBTITLE_LINGER_LONG_PAUSE_S
        return SUBTITLE_LINGER_LONG_PAUSE_S

    def _refresh_active_preset_label(self):
        if self._stt_settings_tab is not None:
            description = self._stt_settings_tab.get_active_preset_description()
        else:
            description = "No STT Settings tab wired in -- using built-in defaults."
        self._set_status(f"Active speaker preset: {description}")

    def _on_preset_changed(self, description: str):
        self._set_status(f"Active speaker preset: {description}")

    def _set_status(self, text: str):
        self._base_status = text
        self._refresh_status_display()

    def _refresh_status_display(self):
        """Appends live elapsed/ETA/speed metrics to the base status
        message while a transcription is in flight. Called both on each
        status change and once a second by _metrics_timer, so the
        elapsed-time readout ticks even between chunk completions."""
        if self._transcribe_start_time is None:
            self.status_label.setText(self._base_status)
            return

        elapsed = time.monotonic() - self._transcribe_start_time
        elapsed_str = fmt_duration(elapsed)
        fraction = self._last_progress_fraction

        if fraction > 0.005:
            projected_total = elapsed / fraction
            remaining = max(0.0, projected_total - elapsed)
            audio_processed_s = fraction * self._total_duration_s
            speed = audio_processed_s / elapsed if elapsed > 0 else 0.0
            metrics = (f" | Elapsed {elapsed_str} | ETA ~{fmt_duration(remaining)} "
                       f"(total ~{fmt_duration(projected_total)}) | {speed:.1f}x realtime")
        else:
            metrics = f" | Elapsed {elapsed_str}"

        self.status_label.setText(self._base_status + metrics)

    def _start_metrics(self, total_duration_s: float):
        self._transcribe_start_time = time.monotonic()
        self._last_progress_fraction = 0.0
        self._total_duration_s = max(total_duration_s, 0.001)
        self._metrics_timer.start()

    def _stop_metrics(self) -> float:
        """Stops the live-metrics timer and returns final elapsed seconds."""
        self._metrics_timer.stop()
        elapsed = time.monotonic() - self._transcribe_start_time if self._transcribe_start_time else 0.0
        self._transcribe_start_time = None
        return elapsed

    def _set_busy(self, busy: bool):
        self.transcribe_btn.setEnabled(not busy)
        self.browse_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        if busy:
            self.save_txt_btn.setEnabled(False)
            self.save_srt_btn.setEnabled(False)

    def _cleanup_temp_wav(self):
        if self._temp_wav_path is not None:
            try:
                os.remove(self._temp_wav_path)
            except OSError:
                pass
            self._temp_wav_path = None

    def _update_char_count(self):
        n = len(self.output_edit.toPlainText())
        self.char_count_label.setText(f"{n:,} character{'s' if n != 1 else ''}")

    # ---- slots ------------------------------------------------------------

    def _on_path_changed(self, text: str):
        self.transcribe_btn.setEnabled(bool(text.strip()))

    def _on_browse_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an audio file", "",
            "Audio/video files (*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma *.mp4 *.mkv *.mov);;"
            "WAV audio (*.wav);;All files (*)",
        )
        if path:
            self.path_edit.setText(path)

    def _on_transcribe_clicked(self):
        path = Path(self.path_edit.text().strip())
        if not path.is_file():
            QMessageBox.warning(self, "File not found", f"No such file:\n{path}")
            return

        self._set_status("Initializing…")

        # Resolve to a usable WAV path/bytes, converting via ffmpeg first
        # if needed. WAV inputs pass through untouched. Non-WAV inputs are
        # always transcoded to a real temp *file* (never piped through
        # stdout -- see audio_convert.to_wav_file's docstring for why that
        # matters), cleaned up once this run finishes either way.
        if path.suffix.lower() == ".wav":
            wav_path_for_chunking = path
        else:
            self._set_status(f"Converting {path.name} to WAV…")
            self._cleanup_temp_wav()
            try:
                self._temp_wav_path = to_wav_file(path)
            except AudioConvertError as e:
                print_error(f"Couldn't convert {path.name}: {e}")
                QMessageBox.warning(self, "Couldn't convert audio", str(e))
                self._set_status("")
                return
            wav_path_for_chunking = self._temp_wav_path

        try:
            duration = wav_duration_s(wav_path_for_chunking)
            self._set_status(f"Sending {path.name} ({duration:.1f}s) for transcription…")
        except (wave.Error, OSError):
            duration = 0.0
            self._set_status(f"Sending {path.name} for transcription…")

        self._source_path = path
        self._chunk_results = []
        self._last_chunk_end_s = 0.0
        self._active_config = self._current_chunker_config()
        self.output_edit.clear()
        self.progress_bar.setValue(0)
        langcode = self._current_langcode()
        suppress_non_speech = self._current_suppress_non_speech()
        client = self._make_client()

        self._set_busy(True)
        self._start_metrics(duration)

        if self._current_chunked_mode():
            self._worker = ChunkedTranscribeWorker(
                client, wav_path_for_chunking, duration, self._active_config,
                langcode, suppress_non_speech, parent=self,
            )
            self._worker.chunk_done.connect(self._on_chunk_done)
            self._worker.progress.connect(self._on_progress)
            self._worker.finished_all.connect(self._on_chunked_finished)
            self._worker.failed.connect(self._on_transcribe_failed)
        else:
            try:
                wav_bytes = wav_path_for_chunking.read_bytes()
            except OSError as e:
                print_error(f"Couldn't read {wav_path_for_chunking}: {e}")
                QMessageBox.warning(self, "Couldn't read file", str(e))
                self._set_busy(False)
                self._stop_metrics()
                return
            self._worker = TranscribeWorker(client, wav_bytes, langcode, suppress_non_speech, parent=self)
            self._worker.finished.connect(self._on_transcribe_finished)
            self._worker.failed.connect(self._on_transcribe_failed)

        self._worker.start()

    def _on_cancel_clicked(self):
        if isinstance(self._worker, ChunkedTranscribeWorker):
            self._worker.request_stop()
            self._set_status("Cancelling after the current chunk finishes…")
        self.cancel_btn.setEnabled(False)

    def _append_to_output(self, index: int, start_s: float, text: str):
        """Appends one chunk's (formatted) text to the live output box,
        inserting a blank line instead of a single newline when the gap
        before this chunk was long enough to count as a paragraph break."""
        formatted = _format_sentence_breaks(text.strip())
        if index == 0:
            separator = ""
        else:
            gap_ms = (start_s - self._last_chunk_end_s) * 1000
            config = self._active_config
            if config.pause_paragraph_enabled and gap_ms >= config.pause_paragraph_ms:
                separator = "\n\n"
            else:
                separator = "\n"
        cursor = self.output_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(f"{separator}{formatted}")
        self.output_edit.setTextCursor(cursor)

    def _on_chunk_done(self, index: int, start_s: float, end_s: float, text: str, local_gaps: list):
        self._chunk_results.append((start_s, end_s, text, local_gaps))
        self._append_to_output(index, start_s, text)
        self._last_chunk_end_s = end_s
        self._set_status(f"Chunk {index + 1} done ({start_s:.1f}s-{end_s:.1f}s)…")

    def _on_progress(self, fraction: float):
        self._last_progress_fraction = fraction
        self.progress_bar.setValue(int(fraction * 1000))

    def _on_chunked_finished(self):
        elapsed = self._stop_metrics()
        self.progress_bar.setValue(1000)
        self._set_status(f"Transcription complete ({len(self._chunk_results)} chunk(s)) in {fmt_duration(elapsed)}.")
        self._set_busy(False)
        self.save_srt_btn.setEnabled(bool(self._chunk_results))
        self._cleanup_temp_wav()
        self._worker = None

    def _on_transcribe_finished(self, text: str):
        elapsed = self._stop_metrics()
        self.output_edit.setPlainText(_format_sentence_breaks(text.strip()))
        self.progress_bar.setValue(1000)
        self._set_status(f"Transcription complete in {fmt_duration(elapsed)}.")
        self._set_busy(False)
        self._cleanup_temp_wav()
        self._worker = None

    def _on_output_text_changed(self):
        self.save_txt_btn.setEnabled(bool(self.output_edit.toPlainText().strip()))
        self._update_char_count()

    def _on_save_text_clicked(self):
        text = self.output_edit.toPlainText()
        if not text.strip():
            return
        default_name = self._source_path.stem + ".txt" if self._source_path else "transcript.txt"
        default_dir = str(self._source_path.parent) if self._source_path else ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save transcript", str(Path(default_dir) / default_name),
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
            self._set_status(f"Saved transcript to {path}")
        except OSError as e:
            print_error(f"Couldn't save transcript: {e}")
            QMessageBox.warning(self, "Couldn't save transcript", str(e))

    def _build_srt(self) -> str:
        entries = []  # (start_s, end_s, text)
        use_segmentation = self._current_subtitle_segmentation_enabled()
        snap_to_gaps = self._current_subtitle_snap_to_gaps()
        max_chars = self._current_subtitle_max_chars()

        for start_s, end_s, text, local_gaps in self._chunk_results:
            if use_segmentation:
                entries.extend(split_chunk_for_subtitles(
                    text, start_s, end_s, local_gaps,
                    max_chars=max_chars, snap_to_gaps=snap_to_gaps,
                ))
            else:
                stripped = text.strip()
                if stripped:
                    entries.append((start_s, end_s, stripped))

        if self._current_subtitle_linger_enabled():
            linger_s = self._current_subtitle_linger_long_pause_s()
            entries = apply_subtitle_linger(
                entries,
                max_full_linger_s=linger_s,
                long_pause_linger_s=linger_s,
            )

        lines = []
        for i, (start_s, end_s, text) in enumerate(entries, start=1):
            lines.append(str(i))
            lines.append(f"{srt_timestamp(start_s)} --> {srt_timestamp(end_s)}")
            lines.append(text)
            lines.append("")
        return "\n".join(lines)

    def _on_save_srt_clicked(self):
        if not self._chunk_results:
            return
        default_name = self._source_path.stem + ".srt" if self._source_path else "transcript.srt"
        default_dir = str(self._source_path.parent) if self._source_path else ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save subtitles", str(Path(default_dir) / default_name),
            "SubRip subtitles (*.srt);;All files (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(self._build_srt(), encoding="utf-8")
            self._set_status(f"Saved subtitles to {path}")
        except OSError as e:
            print_error(f"Couldn't save subtitles: {e}")
            QMessageBox.warning(self, "Couldn't save subtitles", str(e))

    def _on_transcribe_failed(self, message: str):
        self._stop_metrics()
        print_error(f"Transcription failed: {message}")
        self._set_status(f"Transcription failed: {message}")
        if message != "Cancelled.":
            QMessageBox.warning(self, "Transcription failed", message)
        self._set_busy(False)
        self.save_srt_btn.setEnabled(bool(self._chunk_results))
        self._cleanup_temp_wav()
        self._worker = None

    def shutdown(self):
        """Called from MainWindow.closeEvent, matching narration_tab's
        pattern -- makes sure a still-running worker thread doesn't get
        torn out from under itself on app close."""
        if isinstance(self._worker, ChunkedTranscribeWorker):
            self._worker.request_stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(2000)
        self._metrics_timer.stop()
        self._cleanup_temp_wav()
