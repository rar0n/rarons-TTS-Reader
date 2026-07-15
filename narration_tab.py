"""
NarrationTab -- the "Narration" tab of raron's TTS Reader: text entry,
voice/seed controls, playback transport, live status/progress, and
saving to WAV/MP3/SRT. Extracted out of MainWindow so MainWindow only
has to deal with top-level window/tab plumbing.
"""

import time
import random

import numpy as np
import soundfile as sf

from PySide6.QtCore import QObject, Signal, Qt, QThread, QTimer
from PySide6.QtGui import QTextCursor, QColor, QIntValidator, QFontMetrics
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLineEdit, QLabel, QFormLayout, QMessageBox,
    QComboBox, QFileDialog, QCheckBox, QSizePolicy, QProgressBar,
)

from chunker import chunk_text, Chunk
from tts_client import KoboldTTSClient, TTSError
from synth_worker import SynthWorker
from tts_audio_engine import TTSAudioEngine, PlaybackState
from ui_common import (
    STATUS_STYLE_NARRATION, STATUS_STYLE_ERROR,
    COLOR_DARK_BLUE, COLOR_DARK_GREEN, COLOR_DARK_RED, COLOR_DARK_AMBER,
    COLOR_DISABLED_BLUE, COLOR_DISABLED_GREEN,
    btn_style, progress_style, fmt_duration, fmt_bytes, with_extension,
    srt_timestamp, print_error, ElidingLabel, ZoomableTextEdit,
)

# The transport row's middle buttons (Rewind/Forward/Stop) get this
# height; the two end buttons (Play, Save) get the taller one --
# see the comment where these are applied for why.
_TRANSPORT_BTN_HEIGHT = 30
_TRANSPORT_END_BTN_HEIGHT = 38

# Narration/rendering progress bars: muted while in progress, switching
# to the same normal (brighter) blue/green used elsewhere in the app
# once each one actually reaches 100%.
_PROGRESS_STYLE_NARRATION = progress_style(COLOR_DISABLED_BLUE, COLOR_DARK_BLUE)
_PROGRESS_STYLE_NARRATION_FULL = progress_style(COLOR_DARK_BLUE, COLOR_DARK_BLUE)
_PROGRESS_STYLE_RENDER = progress_style(COLOR_DISABLED_GREEN, COLOR_DARK_GREEN)
_PROGRESS_STYLE_RENDER_FULL = progress_style(COLOR_DARK_GREEN, COLOR_DARK_GREEN)

# Used to project remaining time before any chunk has actually been
# synthesized yet (roughly 150 words/minute of speech).
_FALLBACK_SECONDS_PER_WORD = 0.4


class EngineBridge(QObject):
    """Relays TTSAudioEngine callbacks (which fire on background threads)
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




class NarrationTab(QWidget):
    """The Narration tab's UI and all playback/synthesis/saving logic.

    Takes the app's SettingsTab and SeedVaultTab instances (owned by
    MainWindow) so it can read KoboldCpp connection settings and push
    seeds into/pull seeds from the Seed Vault -- it doesn't own either
    of those tabs itself.
    """

    def __init__(self, settings_tab, seed_vault_tab, parent=None):
        super().__init__(parent)
        self.settings_tab = settings_tab
        self.seed_vault_tab = seed_vault_tab
        self.seed_vault_tab.seed_requested.connect(self._on_seed_requested_from_vault)

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
        # Tracks the highest narration-progress fraction shown so far in
        # the current session -- the elapsed/projected-total estimate
        # _update_playing_status computes isn't strictly monotonic (early
        # on, with few chunks' real durations known yet, the projected
        # total can jump around), so the displayed bar only ever moves
        # forward instead of visibly dipping back down mid-playback.
        self._narration_progress_peak: float = 0.0

        # Lets a Play press replay already-synthesized audio instead of
        # re-rendering from scratch, as long as nothing that would change
        # the audio (voice, seed, instruction, URL, or the text itself) has
        # been touched since. Set True once every chunk has finished
        # rendering ("render-through") -- independent of whether playback
        # itself has reached the end, so stopping partway through a
        # finished render still leaves it replayable. Cleared by a fresh
        # Play with something changed, by Stop while rendering is still
        # incomplete, or by a chunk/playback error. _last_play_signature
        # snapshots the fields that must stay unchanged for the buffer to
        # still be valid to replay.
        self._buffered_playable: bool = False
        self._last_play_signature: tuple | None = None
        # True once Stop has been pressed on a render that wasn't fully
        # synthesized yet. SynthWorker.stop() can't cut off a chunk
        # request that's already in flight, so the worker can still limp
        # to completion and fire finished_all a moment *after* Stop was
        # clicked -- without this flag, _on_synthesis_finished would see
        # "fully synthesized" and mark the (stale-seed) buffer replayable
        # again, so the next Play would silently replay old audio instead
        # of reseeding and re-rendering fresh. Reset at the top of every
        # _start_playback() call.
        self._render_was_interrupted: bool = False

        # Which chunk was highlighted last, and which direction the
        # highlight most recently moved -- used by
        # _scroll_to_keep_highlight_margins to tell forward playback from
        # a rewind when "Clamp Highlight Distance" is enabled.
        self._last_highlighted_idx: int | None = None

        # Mirrors the `playing` argument passed to _set_controls_enabled --
        # kept separately because _sync_randomize_btn_enabled() also needs
        # to react to the Lock checkbox alone, and engine.state isn't
        # updated yet at the point _set_controls_enabled(playing=True) is
        # called from _start_playback (that happens a couple of lines
        # before engine.play_from() actually flips it).
        self._transport_playing: bool = False

        self.bridge = EngineBridge()
        self.bridge.chunk_started.connect(self._on_chunk_started)
        self.bridge.chunk_ended.connect(self._on_chunk_ended)
        self.bridge.playback_finished.connect(self._on_playback_finished)
        self.bridge.chunk_error.connect(self._on_chunk_error)

        self.engine = TTSAudioEngine(
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

        #self._build_ui()
        #self._refresh_voices()  # populate the dropdown on startup

    # ---- UI construction --------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Instruction override -- usually left blank. Overrides the voice
        # setting when non-empty (the voice picked below still influences
        # narration even then). The KoboldCpp URL field that used to sit
        # here has moved to the top of the Settings tab.
        form = QFormLayout()
        self.instruction_edit = QLineEdit()
        self.instruction_edit.setPlaceholderText("Optional instruction. PS! Random voice each line, overrides voice for QwenTTS afaik")
        form.addRow("Instructions:", self.instruction_edit)

        voice_row = QHBoxLayout()
        self.voice_combo = QComboBox()
        self.voice_combo.setEditable(True)
        self.voice_combo.lineEdit().setPlaceholderText(
            "Pick a voice, or type a custom one…"
        )
        # Expanding (rather than a capped max width) + the stretch=1 given
        # to it below is what makes this the *only* widget in the row that
        # grows when the window is resized -- every other widget here gets
        # setSizePolicy(Fixed, ...), so 100% of any extra width the row
        # gains goes to the combo box, one-for-one with the window's own
        # growth, while everything to its right stays pinned at a fixed
        # distance from the window's right edge.
        self.voice_combo.setMinimumWidth(120)
        self.voice_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.refresh_voices_btn = QPushButton("⟳")
        self.refresh_voices_btn.setToolTip(
            "Fetch the voice list from KoboldCpp (/api/extra/speakers_list)"
        )
        self.refresh_voices_btn.setFixedWidth(32)
        self.refresh_voices_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.refresh_voices_btn.setStyleSheet(btn_style(COLOR_DARK_BLUE))
        self.refresh_voices_btn.clicked.connect(self._refresh_voices)

        # Seed field: pins the TTS voice for the whole playthrough when
        # filled in (0 to 2^31-1 -- larger values seem to make the voice
        # random again). KoboldCpp doesn't report back whatever seed it
        # auto-picks (its /api/extra/perf endpoint doesn't instrument TTS
        # requests), so it starts pre-filled with a random value -- same
        # effect as leaving it blank, but visible and reusable.
        seed_label = QLabel("Seed:")
        seed_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.seed_edit = QLineEdit()
        self.seed_edit.setPlaceholderText("Seed (blank = auto)")
        self.seed_edit.setValidator(QIntValidator(0, 2**31 - 1, self))
        # Sized to comfortably fit "2147483647" (2^31-1), the largest seed,
        # plus a little slack for variable-width fonts.
        seed_width = QFontMetrics(self.seed_edit.font()).horizontalAdvance("2147483647") + 24
        self.seed_edit.setFixedWidth(seed_width)
        self.seed_edit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.seed_edit.setText(str(random.randint(0, 2**31 - 1)))
        self.randomize_seed_btn = QPushButton("🎲 RND ")
        self.randomize_seed_btn.setToolTip("Fill in a random seed (0 to 2^31-1)")
        self.randomize_seed_btn.setFixedWidth(56)
        self.randomize_seed_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.randomize_seed_btn.setStyleSheet(btn_style(COLOR_DARK_RED))
        self.randomize_seed_btn.clicked.connect(self._on_randomize_seed_clicked)
        self.lock_seed_checkbox = QCheckBox("Lock")
        self.lock_seed_checkbox.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.lock_seed_checkbox.setToolTip(
            "Keep the current seed across new plays instead of randomizing\n"
            "it each time (resuming from pause never re-randomizes it either way)"
        )
        self.lock_seed_checkbox.toggled.connect(lambda _checked: self._sync_randomize_btn_enabled())
        self.store_seed_btn = QPushButton("Store seed")
        self.store_seed_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.store_seed_btn.setToolTip(
            "Save the current voice + seed + instruction as a row in the Seed Vault tab"
        )
        self.store_seed_btn.setStyleSheet(btn_style(COLOR_DARK_GREEN))
        self.store_seed_btn.clicked.connect(self._on_store_seed_clicked)

        voice_row.addWidget(self.voice_combo, stretch=1)
        voice_row.addWidget(self.refresh_voices_btn)
        voice_row.addWidget(seed_label)
        voice_row.addWidget(self.seed_edit)
        voice_row.addWidget(self.randomize_seed_btn)
        voice_row.addWidget(self.lock_seed_checkbox)
        voice_row.addWidget(self.store_seed_btn)
        form.addRow("Voice:", voice_row)

        layout.addLayout(form)

        # Text area (input before playback, highlighted "subtitle" view during)
        self.text_edit = ZoomableTextEdit()
        self.text_edit.setPlaceholderText("Paste or type the text you want read aloud... (Ctrl+scroll to resize text)")
        self.text_edit.textChanged.connect(self._on_text_changed)
        self.text_edit.file_dropped.connect(self._on_file_dropped)
        self.text_edit.play_requested.connect(self._on_play_pause_clicked)
        self.text_edit.pause_resume_requested.connect(self._on_play_pause_clicked)
        self.text_edit.rewind_requested.connect(self._on_rewind_clicked)
        self.text_edit.forward_requested.connect(self._on_forward_clicked)
        self.text_edit.stop_requested.connect(self._on_stop_clicked)
        layout.addWidget(self.text_edit)

        # Narration/rendering progress bars -- sit where the status label
        # used to, right above the transport row. Each starts out muted
        # and switches to its "normal" (brighter) color once it actually
        # reaches 100% -- see _set_progress().
        self.narration_progress = QProgressBar()
        self.narration_progress.setRange(0, 100)
        self.narration_progress.setValue(0)
        self.narration_progress.setTextVisible(False)
        self.narration_progress.setFixedHeight(10)
        self.narration_progress.setStyleSheet(_PROGRESS_STYLE_NARRATION)
        layout.addWidget(self.narration_progress)

        self.render_progress = QProgressBar()
        self.render_progress.setRange(0, 100)
        self.render_progress.setValue(0)
        self.render_progress.setTextVisible(False)
        self.render_progress.setFixedHeight(10)
        self.render_progress.setStyleSheet(_PROGRESS_STYLE_RENDER)
        layout.addWidget(self.render_progress)

        # Transport controls
        controls = QHBoxLayout()
        self.play_btn = QPushButton("▶ Play")
        self.rewind_btn = QPushButton("⏮ Rewind")
        self.forward_btn = QPushButton("⏭ Forward")
        self.stop_btn = QPushButton("⏹ Stop")
        self.save_btn = QPushButton("💾 Save")

        # Each button's full look (enabled color + its own muted disabled
        # color) is set once, here, via QPushButton:disabled -- so toggling
        # availability later is just a setEnabled() call, no restyling.
        self.play_btn.setStyleSheet(btn_style(COLOR_DARK_BLUE))
        self.rewind_btn.setStyleSheet(btn_style(COLOR_DARK_AMBER))
        self.forward_btn.setStyleSheet(btn_style(COLOR_DARK_AMBER))
        self.stop_btn.setStyleSheet(btn_style(COLOR_DARK_RED))
        self.save_btn.setStyleSheet(btn_style(COLOR_DARK_GREEN))

        # Fixed heights instead of leaving buttons to size themselves off
        # their own text: different glyphs (▶ ⏮ ⏭ ⏹ 💾, plus "Play" vs
        # "Pause" vs "Resume" on the same button) have different font
        # metrics, so auto-sizing made buttons wobble in height depending
        # on their current label. The transport buttons get a shorter
        # fixed height and the two end buttons (Play, Save) a
        # taller one; combined with AlignTop below, that's what makes the
        # end buttons sit a bit lower/taller than the middle ones -- on
        # purpose now, and consistent regardless of label.
        for btn in (self.rewind_btn, self.forward_btn, self.stop_btn):
            btn.setFixedHeight(_TRANSPORT_BTN_HEIGHT)
        for btn in (self.play_btn, self.save_btn):
            btn.setFixedHeight(_TRANSPORT_END_BTN_HEIGHT)

        self.play_btn.clicked.connect(self._on_play_pause_clicked)
        self.rewind_btn.clicked.connect(self._on_rewind_clicked)
        self.forward_btn.clicked.connect(self._on_forward_clicked)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.save_btn.clicked.connect(self._on_save_clicked)
        self._set_save_audio_ready(False)

        controls.setAlignment(Qt.AlignTop)
        for btn in (self.play_btn, self.rewind_btn,
                    self.forward_btn, self.stop_btn, self.save_btn):
            controls.addWidget(btn)
        layout.addLayout(controls)

        # Status label -- moved to the bottom of the GUI, below the
        # transport row, matching the Seed Vault and Settings tabs.
        self.status_label = ElidingLabel("0 Chars")
        self.status_label.setStyleSheet(STATUS_STYLE_NARRATION)
        layout.addWidget(self.status_label)

        self._set_controls_enabled(playing=False)

        # The text box is the natural place to start typing/pasting, so
        # it gets initial focus rather than whatever Qt's default tab
        # order would pick.
        self.text_edit.setFocus()

    def _set_controls_enabled(self, playing: bool):
        self._transport_playing = playing
        self.rewind_btn.setEnabled(playing)
        self.forward_btn.setEnabled(playing)
        self.stop_btn.setEnabled(playing)
        self.text_edit.setReadOnly(playing)
        # Voice/seed/instruction shouldn't change mid-render/playback --
        # that would desync them from whatever's actually being (or has
        # been) synthesized.
        self.voice_combo.setEnabled(not playing)
        self.refresh_voices_btn.setEnabled(not playing)
        self.seed_edit.setEnabled(not playing)
        self.instruction_edit.setEnabled(not playing)
        self._sync_randomize_btn_enabled()

    def _sync_randomize_btn_enabled(self):
        """RND is disabled either while playing (same reasoning as the
        voice/seed/instruction fields above) or whenever Lock is checked
        (randomizing would just get overwritten on the next play anyway)."""
        self.randomize_seed_btn.setEnabled(not self._transport_playing and not self.lock_seed_checkbox.isChecked())

    def _set_save_audio_ready(self, ready: bool):
        """Enables/disables Save. Its color (dark green once ready,
        a muted grey-green while there isn't audio to save yet) is baked
        into its stylesheet's :disabled state, so this only ever needs to
        flip setEnabled()."""
        self.save_btn.setEnabled(ready)

    def _set_progress(self, bar: QProgressBar, fraction: float, muted_style: str, full_style: str):
        """Sets `bar`'s value from a 0-1 `fraction`, swapping to
        `full_style` once it actually reaches 100% and back to
        `muted_style` otherwise -- avoids restyling on every single tick
        once it's already sitting at whichever style is current."""
        value = max(0, min(100, int(round(fraction * 100))))
        bar.setValue(value)
        target_style = full_style if value >= 100 else muted_style
        if bar.styleSheet() != target_style:
            bar.setStyleSheet(target_style)

    def _reset_progress_bars(self):
        self._narration_progress_peak = 0.0
        self._set_progress(self.narration_progress, 0.0, _PROGRESS_STYLE_NARRATION, _PROGRESS_STYLE_NARRATION_FULL)
        self._set_progress(self.render_progress, 0.0, _PROGRESS_STYLE_RENDER, _PROGRESS_STYLE_RENDER_FULL)

    # ---- voice discovery ----------------------------------------------------

    def _refresh_voices(self):
        base_url = self.settings_tab.url_edit.text().strip() or "http://127.0.0.1:5001"
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
        self._show_error_status(f"Couldn't fetch voices ({message}) — type one manually")

    def _on_randomize_seed_clicked(self):
        self.seed_edit.setText(str(random.randint(0, 2**31 - 1)))

    def _on_store_seed_clicked(self):
        seed = self.seed_edit.text().strip()
        if not seed:
            QMessageBox.information(self, "No seed", "The seed field is empty -- nothing to store.")
            return
        voice = self.voice_combo.currentText().strip() or "default"
        instruction = self.instruction_edit.text().strip()
        # Only adds the row in memory -- doesn't touch disk, so an
        # accidental/bad store can still be undone by just removing the
        # row (or reloading) before hitting "Save Table" on the Seed
        # Vault tab.
        self.seed_vault_tab.add_row(voice, seed, instruction, "")
        self.status_label.setText(
            f"Stored seed {seed} ({voice}) in Seed Vault -- click Save Table to keep it"
        )

    def _on_seed_requested_from_vault(self, voice: str, seed: str, instruction: str = ""):
        if voice:
            self.voice_combo.setCurrentText(voice)
        self.seed_edit.setText(seed)
        self.instruction_edit.setText(instruction)
        self.lock_seed_checkbox.setChecked(True)
        self.status_label.setText(f"Loaded seed {seed} from Seed Vault")

    # ---- transport handlers -----------------------------------------------

    def _on_play_pause_clicked(self):
        state = self.engine.state
        if state == PlaybackState.STOPPED:
            if self._can_replay_buffer():
                self._replay_from_buffer()
            else:
                self._start_playback()
        else:
            self.engine.toggle_pause()
            if self.engine.state == PlaybackState.PAUSED:
                self._narration_mark_paused()
            elif self.engine.state == PlaybackState.PLAYING:
                self._narration_mark_playing()
        self._sync_play_button()

    # ---- buffered-audio replay ---------------------------------------------

    def _current_playback_signature(self) -> tuple:
        """A snapshot of everything that would change the rendered audio if
        edited. As long as this matches what was used for the last
        completed playthrough, that playthrough's buffer can be replayed
        as-is instead of re-synthesizing."""
        return (
            self.settings_tab.url_edit.text().strip(),
            self.settings_tab.model_edit.text().strip(),
            self.voice_combo.currentText().strip(),
            self.seed_edit.text().strip(),
            self.instruction_edit.text().strip(),
            self.text_edit.toPlainText(),
        )

    def _can_replay_buffer(self) -> bool:
        return (
            self._buffered_playable
            and bool(self.chunks)
            and self.engine.is_fully_synthesized()
            and self._current_playback_signature() == self._last_play_signature
        )

    def _replay_from_buffer(self):
        """Plays the already-synthesized audio again from the top, with no
        re-chunking or re-synthesis and (per the Lock seed checkbox rule)
        no seed change either -- that only happens on a fresh render."""
        self._set_controls_enabled(playing=True)
        self._set_error_style(False)
        self._narration_reset()
        self._narration_progress_peak = 0.0
        self._set_progress(self.narration_progress, 0.0, _PROGRESS_STYLE_NARRATION, _PROGRESS_STYLE_NARRATION_FULL)
        self._narration_mark_playing()
        self.engine.play_from(0)
        self.status_timer.start()

    def _start_playback(self):
        text = self.text_edit.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "No text", "Paste some text first.")
            return

        self._buffered_playable = False
        self._render_was_interrupted = False
        self._reset_progress_bars()

        # A fresh play (as opposed to resuming from pause, which goes
        # through toggle_pause in _on_play_pause_clicked and never reaches
        # here) gets a new random seed unless the user's locked it in.
        if not self.lock_seed_checkbox.isChecked():
            self.seed_edit.setText(str(random.randint(0, 2**31 - 1)))

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
            base_url=self.settings_tab.url_edit.text().strip() or "http://127.0.0.1:5001",
            voice=voice,
            seed=seed,
            instruction=self.instruction_edit.text().strip(),
            model=self.settings_tab.model_edit.text().strip() or "kcpp",
        )
        self._last_play_signature = self._current_playback_signature()

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
        # If every chunk had already finished rendering, that complete
        # buffer is kept around -- Stop here just halts playback, and the
        # next Play (with nothing else changed) replays it instead of
        # re-synthesizing. Only a Stop mid-render (an incomplete buffer)
        # discards it, since there's nothing safe to replay yet.
        if not self.engine.is_fully_synthesized():
            self._buffered_playable = False
            self._render_was_interrupted = True
        self._set_controls_enabled(playing=False)
        self._set_error_style(False)
        self._sync_play_button()
        self.status_label.setText("Idle")
        self._narration_progress_peak = 0.0
        self._set_progress(self.narration_progress, 0.0, _PROGRESS_STYLE_NARRATION, _PROGRESS_STYLE_NARRATION_FULL)
        self.text_edit.setFocus()

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
        self._set_progress(self.narration_progress, 1.0, _PROGRESS_STYLE_NARRATION, _PROGRESS_STYLE_NARRATION_FULL)
        stats = self._build_stats_line(narration_elapsed=self._narration_total_elapsed())
        self.status_label.setText(f"Finished \u2022 {stats}")

    def _on_chunk_error(self, idx: int, message: str):
        """From TTSAudioEngine's on_error -- a chunk's audio never showed up
        in time, or the output device couldn't be opened. Playback has
        already stopped by the time this fires."""
        self.status_timer.stop()
        self._narration_mark_paused()
        self._buffered_playable = False
        self._render_was_interrupted = True
        suffix = f" \u2022 {self._last_stats_line}" if self._last_stats_line else ""
        self._show_error_status(f"Playback error on chunk {idx + 1}: {message}{suffix}")
        self._clear_highlight()
        self._set_controls_enabled(playing=False)
        self._sync_play_button()

    def _on_synth_error(self, idx: int, message: str):
        """From SynthWorker -- one chunk failed to synthesize. Playback
        keeps going; this just flags it. If the TTSAudioEngine ends up
        waiting on this exact chunk it'll time out and raise its own
        on_error, which fully stops playback."""
        self._show_error_status(f"TTS error on chunk {idx + 1}: {message}")

    def _on_chunk_synthesized(self, idx: int, wav_bytes: bytes):
        self._synth_done_count += 1
        total = len(self.chunks)
        if total:
            self._set_progress(self.render_progress, self._synth_done_count / total,
                                _PROGRESS_STYLE_RENDER, _PROGRESS_STYLE_RENDER_FULL)

    def _on_synthesis_finished(self):
        if self._synth_start_time is not None:
            self._synth_total_elapsed = time.monotonic() - self._synth_start_time
        if self.engine.is_fully_synthesized():
            self._set_save_audio_ready(True)
            self._set_progress(self.render_progress, 1.0, _PROGRESS_STYLE_RENDER, _PROGRESS_STYLE_RENDER_FULL)
            # Every chunk has now been rendered -- a "render-through" --
            # so the buffer is complete and safe to replay later even if
            # playback itself hasn't reached the end yet (e.g. the user
            # stops partway through, or pauses and never resumes).
            # _last_play_signature was already snapshotted in
            # _start_playback when this render was kicked off.
            #
            # Exception: if this render was interrupted by an earlier
            # Stop press before it finished, a SynthWorker chunk request
            # that was already in flight at Stop-time can still land here
            # a moment later -- the audio itself is complete and fine to
            # export, but treating it as instant-replayable would let the
            # next Play silently reuse this (possibly stale-seed) buffer
            # instead of reseeding and re-rendering fresh, which is what
            # the user actually asked for by stopping it early.
            if not self._render_was_interrupted:
                self._buffered_playable = True

    # ---- status label -------------------------------------------------------

    def _set_error_style(self, is_error: bool):
        self.status_label.setStyleSheet(STATUS_STYLE_ERROR if is_error else STATUS_STYLE_NARRATION)

    def _show_error_status(self, message: str):
        """Sets the status label's text and red error background together,
        and echoes the same line to stdout/terminal -- the single place
        every error-status update should go through, so a new error site
        can't accidentally forget one half of the pair (as previously
        happened with the "Couldn't fetch voices" message, which set the
        text but never switched the label to its red error style)."""
        self._set_error_style(True)
        self.status_label.setText(message)
        print_error(message)

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

    def _on_file_dropped(self, path: str):
        """Reads a dropped file as text and loads it into the box,
        replacing whatever was there. Decoding errors fall back to the
        replacement character rather than raising, since a stray
        non-UTF-8 byte shouldn't block loading an otherwise-readable file
        -- this is meant for plain-text notes/articles, not arbitrary
        binary formats."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            QMessageBox.warning(self, "Couldn't open file", f"{path}\n\n{e}")
            return
        self.text_edit.setPlainText(content)
        self.status_label.setText(f"Loaded {path}")

    def _render_time_str(self, total: int) -> str:
        """'Rendering' here means KoboldCpp synthesizing the remaining
        chunks -- independent of playback, since the server can run
        faster or slower than real-time speech."""
        if self.engine.is_fully_synthesized():
            if self._synth_total_elapsed is not None:
                return f"Rendering done in {fmt_duration(self._synth_total_elapsed)}"
            return "Rendering done!"
        if self._synth_start_time is not None and self._synth_done_count > 0:
            elapsed = time.monotonic() - self._synth_start_time
            rate = elapsed / self._synth_done_count  # seconds per chunk
            remaining_chunks = max(0, total - self._synth_done_count)
            render_remaining = rate * remaining_chunks
            return f"Rendering: ~{fmt_duration(render_remaining)} left"
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
        mem_str = fmt_bytes(self.engine.get_audio_memory_bytes())
        render_str = self._render_time_str(total) if total else "Rendering done"
        parts = [""]
        if cur_words is not None:
            parts.append(f"{cur_words} Word chunk / {self.total_words} Words Total")
        else:
            parts.append(f"{self.total_words}words total")
        if narration_elapsed is not None:
            parts.append(f"Narration done in {fmt_duration(narration_elapsed)}")
        elif remaining is not None:
            parts.append(f"Narration: ~{fmt_duration(remaining)} left")
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

        if projected_total > 0:
            fraction = max(self._narration_progress_peak, elapsed / projected_total)
            self._narration_progress_peak = min(1.0, fraction)
            self._set_progress(self.narration_progress, fraction,
                                _PROGRESS_STYLE_NARRATION, _PROGRESS_STYLE_NARRATION_FULL)

    # ---- saving -------------------------------------------------------------

    def _on_save_clicked(self):
        # Same readiness bar for every format below (audio or subtitles):
        # every chunk needs to have actually finished synthesizing, since
        # that's what both the mixed-down audio and the subtitle timings
        # are built from. Checked up front so a not-ready save fails fast
        # instead of only after the user's already picked a filename.
        if not self.engine.is_fully_synthesized():
            QMessageBox.warning(
                self, "Not ready",
                "Not all chunks have finished synthesizing yet (or one of them errored out).",
            )
            return

        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Save", "reading.wav",
            "WAV Audio (*.wav);;MP3 Audio (*.mp3);;SubRip Subtitles (*.srt)",
        )
        if not path:
            return

        # Qt doesn't reliably rewrite the typed filename's extension when
        # the user only changes the filter dropdown, so the selected
        # filter -- not whatever extension happens to already be in the
        # text box -- decides the actual format and gets forced onto path.
        if "srt" in selected_filter.lower():
            path = with_extension(path, ".srt")
            self._save_subtitles(path)
            return

        result = self.engine.render_full_audio()
        if result is None:
            QMessageBox.warning(
                self, "Not ready",
                "Not all chunks have finished synthesizing yet (or one of them errored out).",
            )
            return
        data, samplerate = result

        want_mp3 = "mp3" in selected_filter.lower()
        path = with_extension(path, ".mp3" if want_mp3 else ".wav")

        if want_mp3:
            self._save_as_mp3(data, samplerate, path)
        else:
            sf.write(path, data, samplerate)
            self.status_label.setText(f"Saved {path}")

    def _save_subtitles(self, path: str):
        """Writes one SRT cue per chunk. Timings come from the same
        per-chunk durations (plus each chunk's configured pause) that
        the narration status line accumulates during playback -- here
        just summed from the very start rather than from wherever
        playback currently sits, since accurate timing needs every
        chunk's *actual* rendered duration, not the word-count estimate
        used before rendering finishes."""
        _, _, _, durations = self.engine.get_progress_snapshot()
        if not durations or any(d is None for d in durations):
            QMessageBox.warning(
                self, "Not ready",
                "Not all chunks have finished synthesizing yet (or one of them errored out).",
            )
            return

        lines = []
        start = 0.0
        for i, chunk in enumerate(self.chunks):
            end = start + durations[i]
            lines.append(str(i + 1))
            lines.append(f"{srt_timestamp(start)} --> {srt_timestamp(end)}")
            lines.append(chunk.text.strip())
            lines.append("")
            start = end + chunk.pause_ms / 1000.0

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self.status_label.setText(f"Saved {path}")

    def _save_as_mp3(self, data: np.ndarray, samplerate: int, path: str):
        try:
            import lameenc
        except ImportError:
            wav_path = with_extension(path, ".wav")
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
            #selection.format.setBackground(QColor(255, 213, 79))
            selection.format.setBackground(QColor(133, 105, 75))
            #selection.format.setForeground(QColor(0, 0, 0))
            selection.format.setForeground(QColor(255, 255, 255))
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
            # A rewind/skip-back is the only case that moves the highlight
            # to a *lower* index than last time -- anything else (normal
            # forward playback, or repeating/replaying the same chunk)
            # counts as "forward" for margin purposes.
            forward = self._last_highlighted_idx is None or idx >= self._last_highlighted_idx
            self._scroll_to_keep_highlight_margins(forward)
            self._last_highlighted_idx = idx

    def _scroll_to_keep_highlight_margins(self, forward: bool = True):
        """ensureCursorVisible() only nudges the view just enough to keep
        the cursor on-screen at all, which tends to leave it hugging
        whichever edge it approached from.

        Default behavior (Clamp Highlight Distance off): once it's
        drifted past the 1/SD margin from the bottom, scroll further so
        it settles at 1/SD from the top instead -- giving more of the
        upcoming text room to be seen ahead of time while
        playing/forwarding. Symmetric the other way too: once it's
        drifted past the 1/SD margin from the top (e.g. after a rewind),
        scroll so it settles at 1/SD from the *bottom* instead, giving
        more of the preceding text room to be seen.

        With Clamp Highlight Distance on, the scroll is a minimal
        clamp instead of a jump to the opposite margin: while playing
        forward, once the highlight would drift closer than 1/SD to the
        *bottom* edge, it's held right at that 1/SD line rather than
        jumped up near the top; while rewinding, once it would drift
        closer than 1/SD to the *top* edge, it's held right at that line
        instead of jumped down near the bottom. Either way, this is left
        alone if there isn't enough text in that direction to actually
        scroll that far -- e.g. near the very start or end of the text,
        the highlight naturally ends up closer to the edge than the
        margin would normally allow.

        Does nothing at all unless "Highlight Margin" is enabled on the
        Settings tab."""
        if not self.settings_tab.highlight_margin_checkbox.isChecked():
            return
        sd = self.settings_tab.scroll_denominator_spin.value()
        if sd < 2:
            return
        viewport_height = self.text_edit.viewport().height()
        cursor_top = self.text_edit.cursorRect().top()
        target_top = viewport_height / sd
        target_bottom = viewport_height * (sd - 1) / sd

        if self.settings_tab.clamp_highlight_checkbox.isChecked():
            if forward:
                if cursor_top <= target_bottom:
                    return
                target = target_bottom
            else:
                if cursor_top >= target_top:
                    return
                target = target_top
        else:
            if cursor_top > target_bottom:
                target = target_top
            elif cursor_top < target_top:
                target = target_bottom
            else:
                return

        scrollbar = self.text_edit.verticalScrollBar()
        new_value = scrollbar.value() + (cursor_top - target)
        scrollbar.setValue(int(min(scrollbar.maximum(), max(scrollbar.minimum(), new_value))))

    def _clear_highlight(self):
        self.text_edit.setExtraSelections([])
        self._last_highlighted_idx = None

    def shutdown(self):
        """Called from MainWindow.closeEvent to cleanly tear down the
        audio engine and any in-flight synthesis worker."""
        self.engine.shutdown()
        if self.synth_worker is not None:
            self.synth_worker.stop()
            self.synth_worker.wait(1000)
