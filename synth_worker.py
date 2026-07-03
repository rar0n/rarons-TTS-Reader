"""
Runs ahead of playback, synthesizing each chunk in order on a background
thread so that by the time the AudioEngine wants chunk N, it's usually
already sitting in the cache. Runs independently of play/pause state, so
pausing playback doesn't pause synthesis (it just gets further ahead).
"""

from PySide6.QtCore import QThread, Signal

from chunker import Chunk
from tts_client import KoboldTTSClient, TTSError


class SynthWorker(QThread):
    chunk_ready = Signal(int, bytes)
    error = Signal(int, str)
    finished_all = Signal()

    def __init__(self, client: KoboldTTSClient, chunks: list[Chunk], parent=None):
        super().__init__(parent)
        self.client = client
        self.chunks = chunks
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        for i, chunk in enumerate(self.chunks):
            if self._stop_requested:
                return
            try:
                wav_bytes = self.client.synthesize(chunk.text)
            except TTSError as e:
                self.error.emit(i, str(e))
                continue
            if self._stop_requested:
                return
            self.chunk_ready.emit(i, wav_bytes)
        self.finished_all.emit()
