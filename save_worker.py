"""
Writes the fully rendered audio to a WAV or MP3 file on a background
thread, in blocks, reporting progress as it goes -- so the GUI stays
responsive (and can show a progress bar) while a long narration is
being encoded/written.
"""

import os

import numpy as np
import soundfile as sf

from PySide6.QtCore import QThread, Signal

# Frames per write/encode block. Small enough for smooth progress and
# quick cancellation, large enough that per-block overhead is negligible.
_BLOCK_FRAMES = 32768


class SaveWorker(QThread):
    progress = Signal(float)  # 0.0 - 1.0
    saved = Signal(str)       # path written
    failed = Signal(str)      # error message

    def __init__(self, data: np.ndarray, samplerate: int, path: str, as_mp3: bool, parent=None):
        super().__init__(parent)
        self.data = data
        self.samplerate = samplerate
        self.path = path
        self.as_mp3 = as_mp3
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True

    def run(self):
        try:
            completed = self._write_mp3() if self.as_mp3 else self._write_wav()
        except Exception as e:  # report anything (disk full, bad path, encoder error) to the GUI
            self._remove_partial_file()
            self.failed.emit(str(e))
            return
        if not completed:
            self._remove_partial_file()
            return
        self.progress.emit(1.0)
        self.saved.emit(self.path)

    def _remove_partial_file(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def _write_wav(self) -> bool:
        total = len(self.data)
        channels = self.data.shape[1] if self.data.ndim > 1 else 1
        last_percent = -1
        # Same defaults as sf.write() for a .wav path (16-bit PCM).
        with sf.SoundFile(self.path, mode="w", samplerate=self.samplerate,
                          channels=channels, format="WAV") as f:
            for start in range(0, total, _BLOCK_FRAMES):
                if self._cancel_requested:
                    return False
                f.write(self.data[start:start + _BLOCK_FRAMES])
                last_percent = self._emit_progress(start + _BLOCK_FRAMES, total, last_percent)
        return True

    def _write_mp3(self) -> bool:
        import lameenc  # caller has already checked it's installed

        pcm = np.clip(self.data, -1.0, 1.0)
        pcm16 = (pcm * 32767.0).astype(np.int16)
        channels = pcm16.shape[1] if pcm16.ndim > 1 else 1
        total = len(pcm16)

        encoder = lameenc.Encoder()
        encoder.set_bit_rate(128)
        encoder.set_in_sample_rate(self.samplerate)
        encoder.set_channels(channels)
        encoder.set_quality(2)  # 2 = high quality (slower); 7 = fastest

        last_percent = -1
        with open(self.path, "wb") as f:
            for start in range(0, total, _BLOCK_FRAMES):
                if self._cancel_requested:
                    return False
                block = np.ascontiguousarray(pcm16[start:start + _BLOCK_FRAMES])
                f.write(encoder.encode(block.tobytes()))
                last_percent = self._emit_progress(start + _BLOCK_FRAMES, total, last_percent)
            f.write(encoder.flush())
        return True

    def _emit_progress(self, done_frames: int, total: int, last_percent: int) -> int:
        """Emits only when the whole-percent value changes, to avoid
        flooding the GUI thread with signals on very long audio."""
        percent = min(99, int(done_frames * 100 / total)) if total else 99
        if percent != last_percent:
            self.progress.emit(percent / 100.0)
        return percent
