"""
Tiny wrapper around the `ffmpeg` command-line tool for transcoding
arbitrary input audio/video (mp3, mp4, m4a, flac, ...) into the PCM WAV
KoboldCpp's /api/extra/transcribe endpoint and audio_chunker.py want.

Requires ffmpeg to be installed and on PATH -- it isn't vendored or
pip-installed here, just shelled out to, same as most audio-adjacent
tools do.
"""

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

# Whisper's own training/inference format -- mono, 16kHz. Feeding it
# anything else just means whisper.cpp (via KoboldCpp) resamples it
# internally anyway, so converting to this up front doesn't lose
# anything and keeps the base64 payload smaller than a higher-rate WAV
# would be.
WHISPER_SAMPLE_RATE = 16000
WHISPER_CHANNELS = 1


class AudioConvertError(Exception):
    pass


def to_wav_file(path: Path, sample_rate: int = WHISPER_SAMPLE_RATE,
                 channels: int = WHISPER_CHANNELS,
                 output_path: Optional[Path] = None) -> Path:
    """Transcodes the audio/video file at `path` to a PCM WAV file via
    ffmpeg, resampling to `sample_rate`/`channels`. Writes to a real,
    seekable file -- NOT piped through stdout -- because a WAV's data-size
    header field can only be filled in correctly once the whole file has
    been written, which requires ffmpeg to seek back into what it already
    wrote. On a non-seekable pipe it can't do that, and instead writes a
    placeholder/max-int size, which `wave.getnframes()` then trusts
    blindly -- silently producing wildly wrong durations (and, past the
    real audio, empty/garbage frames) for anything read back out of it.
    Always converting to an actual file sidesteps that entirely.

    If `output_path` isn't given, a new temp file is created and its path
    returned -- the caller is responsible for deleting it when done.
    Raises AudioConvertError if ffmpeg is missing or the file can't be
    decoded."""
    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        output_path = Path(tmp.name)

    cmd = [
        "ffmpeg",
        "-y",  # overwrite the (empty) temp file ffmpeg will see already exists
        "-i", str(path),
        "-vn",  # no video -- relevant for mp4/video sources, audio only
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-f", "wav",
        "-loglevel", "error",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=False)
    except FileNotFoundError as e:
        raise AudioConvertError(
            "ffmpeg isn't installed (or not on PATH) -- it's needed to "
            "convert non-WAV audio/video. Install it via your package "
            "manager, e.g. `sudo apt install ffmpeg`."
        ) from e

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise AudioConvertError(f"ffmpeg couldn't convert {path.name}: {stderr}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise AudioConvertError(f"ffmpeg produced no output for {path.name}")

    return output_path


def to_wav_bytes(path: Path, sample_rate: int = WHISPER_SAMPLE_RATE,
                  channels: int = WHISPER_CHANNELS) -> bytes:
    """Convenience wrapper for callers that just want bytes rather than a
    file on disk -- converts to a temp file (see to_wav_file's docstring
    for why that matters) then reads it back and cleans up."""
    tmp_path = to_wav_file(path, sample_rate=sample_rate, channels=channels)
    try:
        return tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
