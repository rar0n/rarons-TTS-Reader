"""
Plays a sequence of already-synthesized WAV chunks back to back, with:

  - sample-accurate pause/resume (pausing doesn't lose your place)
  - rewind/skip by chunk (jump back N sentences and replay)
  - automatic, per-chunk silence gaps (so commas/dashes/periods get the
    pause length the chunker assigned them)
  - tolerance for audio that isn't synthesized yet (it'll wait and poll,
    so playback can start as soon as chunk 0 is ready instead of waiting
    for the whole text to be synthesized)
  - an on_error callback fired if a chunk's audio never shows up in time,
    or if the output device can't be opened after several attempts --
    both cases stop playback, but previously did so completely silently,
    which looked indistinguishable from a genuine hang from the caller's
    side (nothing else ever told it playback had stopped).

This intentionally avoids gluing chunks into one long audio stream --
keeping them separate is what makes "go back one sentence" trivial.

Audio device handling: a single sd.OutputStream is opened once (lazily,
the first time it's needed) and kept open for the engine's lifetime. A
dedicated feeder thread writes audio to it in small blocks -- real chunk
data, then a block of silence for the chunk's pause, then the next
chunk's data, and so on -- so nothing about play/pause/rewind/next-chunk
ever tears down and reopens the device. (An earlier version opened a
fresh OutputStream per chunk, which -- if PulseAudio dropped and
re-established its connection between chunks -- meant the next
sd.OutputStream() call could land on a now-stale device index and raise
PaErrorCode -9993. Keeping one stream alive for the whole session avoids
that window entirely.)
"""

import queue
import threading
import time
import io
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import sounddevice as sd
import soundfile as sf

from chunker import Chunk

# Default format the persistent stream opens with before we've seen any
# real audio yet (Qwen3TTS via KoboldCpp outputs 24kHz mono WAV). If the
# first actual chunk turns out to differ, the stream is reopened once to
# match -- that's a rare, one-time cost, not the per-chunk churn this was
# built to avoid.
_DEFAULT_SAMPLERATE = 24000
_DEFAULT_CHANNELS = 1

_WRITE_BLOCK_SECONDS = 0.05  # ~50ms blocks -- keeps pause/stop latency low
# Give up on a chunk that never gets synthesized. Kept comfortably above
# tts_client's own default request timeout (60s) so we don't give up on a
# chunk while its HTTP request is still legitimately in flight -- if you
# change KoboldTTSClient's timeout, keep this one bigger than it.
_SYNTH_WAIT_TIMEOUT = 75.0
_STREAM_RETRY_DELAY = 0.3    # backoff between failed attempts to (re)open the device
_MAX_STREAM_OPEN_ATTEMPTS = 20  # ~6s of retries before giving up and reporting an error


class PlaybackState:
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class AudioEngine:
    def __init__(self,
                 on_chunk_start: Optional[Callable[[int], None]] = None,
                 on_chunk_end: Optional[Callable[[int], None]] = None,
                 on_finished: Optional[Callable[[], None]] = None,
                 on_error: Optional[Callable[[int, str], None]] = None):
        self.on_chunk_start = on_chunk_start
        self.on_chunk_end = on_chunk_end
        self.on_finished = on_finished
        self.on_error = on_error

        self._lock = threading.Lock()
        self._chunks: List[Chunk] = []
        self._audio: Dict[int, np.ndarray] = {}
        self._samplerate = _DEFAULT_SAMPLERATE

        self._index = -1
        self._frame_pos = 0
        self._state = PlaybackState.STOPPED
        self._generation = 0  # bumped on every transport call to invalidate stale in-flight work

        self._stream: Optional[sd.OutputStream] = None
        self._stream_format: Optional[Tuple[int, int]] = None
        self._stream_fail_gen: Optional[int] = None
        self._stream_fail_count = 0

        self._wake = threading.Event()
        self._shutdown = False
        self._events: "queue.Queue" = queue.Queue()

        self._feeder_thread = threading.Thread(target=self._feeder_loop, daemon=True)
        self._feeder_thread.start()
        self._notifier_thread = threading.Thread(target=self._notifier_loop, daemon=True)
        self._notifier_thread.start()

    # ---- setup ------------------------------------------------------------

    def set_chunks(self, chunks: List[Chunk]):
        self.stop()  # make sure any previous session is fully halted before swapping chunks out
        with self._lock:
            self._chunks = chunks
            self._audio.clear()
            self._index = -1
            self._frame_pos = 0
            self._generation += 1
        self._wake.set()

    def feed_audio(self, index: int, wav_bytes: bytes):
        """Called (from any thread) once a chunk's audio has been synthesized."""
        data, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        with self._lock:
            self._audio[index] = data
            self._samplerate = sr
        self._wake.set()

    def has_audio(self, index: int) -> bool:
        with self._lock:
            return index in self._audio

    @property
    def num_chunks(self) -> int:
        with self._lock:
            return len(self._chunks)

    # ---- transport controls -------------------------------------------------

    def play_from(self, index: int):
        with self._lock:
            if index < 0 or index >= len(self._chunks):
                self._state = PlaybackState.STOPPED
                self._index = -1
                self._generation += 1
                self._wake.set()
                return
            self._index = index
            self._frame_pos = 0
            self._state = PlaybackState.PLAYING
            self._generation += 1
        self._wake.set()

    def pause(self):
        with self._lock:
            if self._state == PlaybackState.PLAYING:
                self._state = PlaybackState.PAUSED
        self._wake.set()

    def resume(self):
        with self._lock:
            if self._state == PlaybackState.PAUSED:
                self._state = PlaybackState.PLAYING
        self._wake.set()

    def toggle_pause(self):
        with self._lock:
            if self._state == PlaybackState.PLAYING:
                self._state = PlaybackState.PAUSED
            elif self._state == PlaybackState.PAUSED:
                self._state = PlaybackState.PLAYING
        self._wake.set()

    def stop(self):
        with self._lock:
            self._state = PlaybackState.STOPPED
            self._index = -1
            self._frame_pos = 0
            self._generation += 1
        self._wake.set()

    def rewind(self, n_chunks: int = 1):
        with self._lock:
            target = max(0, self._index - n_chunks)
        self.play_from(target)

    def skip_forward(self, n_chunks: int = 1):
        with self._lock:
            target = min(len(self._chunks) - 1, self._index + n_chunks)
        self.play_from(target)

    def shutdown(self):
        """Fully release the audio device. Call this on app exit -- not
        between plays, since the whole point is to keep the device open
        across those."""
        with self._lock:
            self._shutdown = True
            self._state = PlaybackState.STOPPED
            self._generation += 1
        self._wake.set()
        self._feeder_thread.join(timeout=2.0)
        self._close_stream()
        self._events.put_nowait(("__stop__", None, None))
        self._notifier_thread.join(timeout=2.0)

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def current_index(self) -> int:
        with self._lock:
            return self._index

    # ---- feeder thread: owns the stream and pushes audio into it --------

    def _ensure_stream(self, samplerate: int, channels: int) -> bool:
        if self._stream is not None and self._stream_format == (samplerate, channels):
            return True
        self._close_stream()
        try:
            stream = sd.OutputStream(samplerate=samplerate, channels=channels, dtype="float32")
            stream.start()
        except Exception:
            return False
        self._stream = stream
        self._stream_format = (samplerate, channels)
        return True

    def _close_stream(self):
        stream = self._stream
        self._stream = None
        self._stream_format = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _wait_for_chunk_audio(self, idx: int, gen: int) -> Tuple[Optional[np.ndarray], bool]:
        """Poll for chunk `idx`'s audio. Returns (data, timed_out).

        `data` is None either because some other transport call took over
        (a normal, silent cancellation -- nothing to report) or because
        synthesis never showed up within _SYNTH_WAIT_TIMEOUT (an actual
        stall). `timed_out` tells the caller which case it was, so a real
        stall can be surfaced instead of just going quiet."""
        start = time.monotonic()
        while True:
            with self._lock:
                if gen != self._generation or self._state != PlaybackState.PLAYING:
                    return None, False
                data = self._audio.get(idx)
            if data is not None:
                return data, False
            if time.monotonic() - start > _SYNTH_WAIT_TIMEOUT:
                with self._lock:
                    if gen == self._generation:
                        self._state = PlaybackState.STOPPED
                return None, True
            self._wake.wait(timeout=0.05)
            self._wake.clear()

    def _write_audio(self, data: np.ndarray, gen: int, on_progress=None) -> bool:
        """Write `data` to the stream in blocks. Returns False if playback
        was paused-then-stopped, stopped outright, or superseded by a new
        transport call (gen change) partway through. While paused, this
        simply stops calling stream.write() -- the hardware buffer drains
        to silence on its own, and writing resumes exactly where it left
        off, which is what makes pause/resume sample-accurate."""
        pos = 0
        blocksize = max(1, int(self._samplerate * _WRITE_BLOCK_SECONDS))
        while pos < len(data):
            with self._lock:
                if gen != self._generation:
                    return False
                state = self._state
            if state == PlaybackState.STOPPED:
                return False
            if state == PlaybackState.PAUSED:
                self._wake.wait(timeout=0.05)
                self._wake.clear()
                continue
            block = data[pos:pos + blocksize]
            try:
                self._stream.write(block)
            except Exception:
                return False
            pos += len(block)
            if on_progress is not None:
                on_progress(pos)
        return True

    def _feeder_loop(self):
        while True:
            with self._lock:
                if self._shutdown:
                    return
                state = self._state
                gen = self._generation

            if state != PlaybackState.PLAYING:
                self._wake.wait(timeout=0.1)
                self._wake.clear()
                continue

            with self._lock:
                idx = self._index
                total = len(self._chunks)
            if idx < 0 or idx >= total:
                with self._lock:
                    if gen == self._generation:
                        self._state = PlaybackState.STOPPED
                        self._index = -1
                self._events.put_nowait(("finished", None, gen))
                continue

            data, timed_out = self._wait_for_chunk_audio(idx, gen)
            if timed_out:
                self._events.put_nowait((
                    "error", (idx, "Timed out waiting for chunk audio"), gen
                ))
            if data is None:
                continue
            with self._lock:
                still_current = gen == self._generation and self._state == PlaybackState.PLAYING
            if not still_current:
                continue

            with self._lock:
                samplerate = self._samplerate
            channels = data.shape[1]
            if not self._ensure_stream(samplerate, channels):
                if self._stream_fail_gen != gen:
                    self._stream_fail_gen = gen
                    self._stream_fail_count = 0
                self._stream_fail_count += 1
                if self._stream_fail_count > _MAX_STREAM_OPEN_ATTEMPTS:
                    with self._lock:
                        if gen == self._generation:
                            self._state = PlaybackState.STOPPED
                    self._events.put_nowait((
                        "error", (idx, "Could not open audio output device"), gen
                    ))
                    continue
                time.sleep(_STREAM_RETRY_DELAY)
                continue
            self._stream_fail_count = 0

            self._events.put_nowait(("chunk_start", idx, gen))

            def _progress(pos, _gen=gen):
                with self._lock:
                    if _gen == self._generation:
                        self._frame_pos = pos

            ok = self._write_audio(data, gen, on_progress=_progress)
            with self._lock:
                if gen == self._generation:
                    self._frame_pos = 0
            if not ok:
                continue

            self._events.put_nowait(("chunk_end", idx, gen))

            with self._lock:
                if gen != self._generation or self._state != PlaybackState.PLAYING:
                    continue
                pause_ms = self._chunks[idx].pause_ms if idx < len(self._chunks) else 0

            if pause_ms > 0:
                silence = np.zeros((max(1, int(pause_ms / 1000.0 * samplerate)), channels), dtype=data.dtype)
                if not self._write_audio(silence, gen):
                    continue

            with self._lock:
                if gen == self._generation and self._state == PlaybackState.PLAYING:
                    self._index += 1
                    self._frame_pos = 0

    def _notifier_loop(self):
        """Fires user callbacks off the feeder/audio thread so slow GUI
        work (Qt signal emits, etc.) can never cause an audio underrun."""
        while True:
            kind, payload, gen = self._events.get()
            if kind == "__stop__":
                return
            if gen is not None:
                with self._lock:
                    if gen != self._generation:
                        continue
            if kind == "chunk_start" and self.on_chunk_start:
                self.on_chunk_start(payload)
            elif kind == "chunk_end" and self.on_chunk_end:
                self.on_chunk_end(payload)
            elif kind == "finished" and self.on_finished:
                self.on_finished()
            elif kind == "error" and self.on_error:
                self.on_error(*payload)
