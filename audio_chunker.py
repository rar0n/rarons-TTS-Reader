"""
Splits a WAV file into Whisper-sized chunks at silence boundaries, using
plain stdlib RMS-based silence detection (audioop + wave). No Qt, no
network calls -- this module only knows about audio in and (offsets,
WAV bytes) out, so it can be developed and tested against a saved file
before it's ever wired into the STT tab.

Terminology, matching the design discussion:
- "gap"            a stretch of audio whose RMS stays below the
                    amplitude threshold for at least `min_gap_ms`
                    continuously. Shorter dips (a plosive consonant, a
                    quick breath) are NOT gaps -- they're bridged over.
- "soft gap"        a gap that's short enough to just be a candidate cut
                    point *inside* a chunk -- the audio on both sides of
                    it still gets sent to Whisper, just as two chunks
                    instead of one.
- "hard gap"        a gap at or beyond `long_silence_ms` (defaults to the
                    paragraph pause tier) -- treated as dead air. The
                    chunk before it always ends at the gap's start, the
                    next chunk always starts at the gap's end, and the
                    silence itself is trimmed out entirely and never sent
                    to Whisper (avoids paying for transcription of
                    nothing, and avoids Whisper's tendency to hallucinate
                    text when fed pure silence).

Chunk sizing target, per chunk (i.e. per continuous-speech segment left
after hard gaps are removed):
1. Prefer a soft-gap midpoint inside [target - range, target + range]
   (default 15s +/- 5s, i.e. 10-20s) closest to target itself.
2. If none, force a cut at target + range -- the segment is just
   continuous speech (or non-speech noise) longer than we're willing to
   wait for a natural break.
"""

import audioop
import io
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


# ---- configuration ---------------------------------------------------

@dataclass
class ChunkerConfig:
    # VAD -- only one implementation for now (this module IS the "RMS
    # (stdlib audioop)" VAD type); a `vad_type` string field would live
    # on the settings tab / a dispatch layer above this, not here.

    # Raw RMS threshold. For 16-bit PCM, RMS ranges roughly 0-32767.
    # Very recording-dependent -- quiet-room narration vs. a compressed
    # podcast rip need very different values. No auto-calibration yet;
    # start simple and tune by ear/testing.
    amplitude_threshold: int = 500

    # Size of each analysis window used to sample RMS while scanning.
    analysis_window_ms: int = 50

    # Minimum continuous below-threshold duration to count as a real
    # gap at all (below this: a plosive/breath dip, not a pause).
    min_gap_ms: int = 180

    # Elastic chunk-length target, in seconds -- search for a natural cut
    # within [target - range, target + range], preferring whichever
    # candidate lands closest to target itself. If nothing qualifies,
    # force a cut at target + range.
    chunk_target_s: float = 15.0
    chunk_range_s: float = 5.0

    # Pause tiers -- used for transcript text formatting (comma / sentence
    # / paragraph breaks) elsewhere; paragraph's duration doubles as the
    # "trim this silence out entirely" (hard gap) threshold below, unless
    # long_silence_ms overrides it.
    pause_comma_enabled: bool = True
    pause_comma_ms: int = 250
    pause_sentence_enabled: bool = True
    pause_sentence_ms: int = 500
    pause_paragraph_enabled: bool = True
    pause_paragraph_ms: int = 1200

    # Gaps at/above this duration are hard gaps (trimmed out entirely).
    # None -> falls back to pause_paragraph_ms.
    long_silence_ms: Optional[int] = None

    def resolved_long_silence_ms(self) -> int:
        return self.long_silence_ms if self.long_silence_ms is not None else self.pause_paragraph_ms


# ---- data types ---------------------------------------------------

@dataclass
class Gap:
    start_s: float
    end_s: float

    @property
    def duration_ms(self) -> float:
        return (self.end_s - self.start_s) * 1000.0

    @property
    def midpoint_s(self) -> float:
        return (self.start_s + self.end_s) / 2.0


@dataclass
class Chunk:
    index: int
    start_s: float
    end_s: float
    audio_bytes: bytes  # standalone WAV bytes for just this span, ready to POST

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


# ---- silence scanning ---------------------------------------------------

def _open_wave(source):
    """Opens `source` for reading with the wave module -- source can be a
    path (str/Path) or raw WAV bytes (used when re-scanning a single
    chunk's already-extracted audio, e.g. for subtitle segmentation,
    without writing it back out to disk first)."""
    if isinstance(source, (bytes, bytearray)):
        return wave.open(io.BytesIO(source), "rb")
    return wave.open(str(source), "rb")


def iter_gaps(source, config: ChunkerConfig):
    """Scans the whole file once, yielding every Gap that meets
    min_gap_ms, in time order. Streams via wave.readframes rather than
    loading the file whole. `source` is a path or raw WAV bytes -- see
    _open_wave. Gap offsets are relative to the start of `source`."""
    with _open_wave(source) as wf:
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        window_frames = max(1, int(framerate * config.analysis_window_ms / 1000))

        run_start: Optional[float] = None
        pos_frames = 0
        window_end_s = 0.0

        while True:
            frames = wf.readframes(window_frames)
            if not frames:
                break
            n_frames_read = len(frames) // (sampwidth * wf.getnchannels())
            if n_frames_read == 0:
                break

            window_start_s = pos_frames / framerate
            window_end_s = (pos_frames + n_frames_read) / framerate
            rms = audioop.rms(frames, sampwidth)

            if rms < config.amplitude_threshold:
                if run_start is None:
                    run_start = window_start_s
            else:
                if run_start is not None:
                    gap = Gap(run_start, window_start_s)
                    if gap.duration_ms >= config.min_gap_ms:
                        yield gap
                    run_start = None

            pos_frames += n_frames_read

        if run_start is not None:
            gap = Gap(run_start, window_end_s)
            if gap.duration_ms >= config.min_gap_ms:
                yield gap


def wav_duration_s(source) -> float:
    with _open_wave(source) as wf:
        return wf.getnframes() / float(wf.getframerate())


# ---- chunk planning (pure -- offsets only, no audio bytes yet) --------

def _plan_segment(seg_start: float, seg_end: float, soft_gaps: List[Gap],
                   config: ChunkerConfig) -> List[tuple]:
    """Elastic chunking within one continuous-speech segment (no hard
    silence inside it). Returns a list of (start_s, end_s) tuples."""
    result = []
    cursor = seg_start

    def best_gap(lo: float, hi: float, center: float) -> Optional[Gap]:
        candidates = [g for g in soft_gaps if g.start_s > cursor and lo <= g.midpoint_s <= hi]
        if not candidates:
            return None
        candidates.sort(key=lambda g: abs(g.midpoint_s - center))
        return candidates[0]

    while cursor < seg_end:
        remaining = seg_end - cursor
        target = cursor + config.chunk_target_s
        lo = cursor + max(0.0, config.chunk_target_s - config.chunk_range_s)
        hi = cursor + config.chunk_target_s + config.chunk_range_s

        gap = best_gap(lo, hi, target)

        if gap is not None:
            cut = gap.midpoint_s
        elif remaining <= config.chunk_target_s + config.chunk_range_s:
            # No natural cut found, but what's left fits within range
            # anyway -- take it rather than forcing an arbitrary split.
            cut = seg_end
        else:
            # No natural cut, and there's more than target+range left --
            # forced cut, no way around it.
            cut = hi

        cut = min(cut, seg_end)
        result.append((cursor, cut))
        cursor = cut

    return result


def plan_chunks(gaps: List[Gap], total_duration_s: float,
                 config: ChunkerConfig) -> List[tuple]:
    """Turns the full list of detected gaps into a final list of
    (start_s, end_s) speech-chunk spans, with hard gaps trimmed out
    entirely and soft gaps used only as optional cut points."""
    hard_threshold = config.resolved_long_silence_ms()
    hard_gaps = [g for g in gaps if g.duration_ms >= hard_threshold]
    soft_gaps = [g for g in gaps if g.duration_ms < hard_threshold]

    segments = []
    cursor = 0.0
    for g in hard_gaps:
        if g.start_s > cursor:
            segments.append((cursor, g.start_s))
        cursor = g.end_s
    if cursor < total_duration_s:
        segments.append((cursor, total_duration_s))

    spans = []
    for seg_start, seg_end in segments:
        if seg_end - seg_start <= 0:
            continue
        seg_soft_gaps = [g for g in soft_gaps if seg_start <= g.start_s and g.end_s <= seg_end]
        spans.extend(_plan_segment(seg_start, seg_end, seg_soft_gaps, config))
    return spans


# ---- audio extraction --------------------------------------------------

def _extract_wav_segment(wav_path: Path, start_s: float, end_s: float) -> bytes:
    """Reads frames [start_s, end_s) from wav_path and returns them as a
    standalone WAV byte blob (own header), ready to send to KoboldCpp."""
    with wave.open(str(wav_path), "rb") as src:
        framerate = src.getframerate()
        start_frame = int(start_s * framerate)
        end_frame = int(end_s * framerate)
        src.setpos(max(0, start_frame))
        frames = src.readframes(max(0, end_frame - start_frame))

        buf = io.BytesIO()
        with wave.open(buf, "wb") as dst:
            dst.setnchannels(src.getnchannels())
            dst.setsampwidth(src.getsampwidth())
            dst.setframerate(framerate)
            dst.writeframes(frames)
        return buf.getvalue()


# ---- top-level entry point ---------------------------------------------

def chunk_audio_file(wav_path: Path, config: Optional[ChunkerConfig] = None) -> List[Chunk]:
    """Scans wav_path for silence, plans chunk boundaries, and extracts
    each chunk's audio -- the full pipeline in one call. wav_path must
    already be a WAV file (PCM); transcode with audio_convert.to_wav_bytes
    first if it isn't."""
    config = config or ChunkerConfig()
    wav_path = Path(wav_path)

    total_duration_s = wav_duration_s(wav_path)
    gaps = list(iter_gaps(wav_path, config))
    spans = plan_chunks(gaps, total_duration_s, config)

    chunks = []
    for i, (start_s, end_s) in enumerate(spans):
        audio_bytes = _extract_wav_segment(wav_path, start_s, end_s)
        chunks.append(Chunk(index=i, start_s=start_s, end_s=end_s, audio_bytes=audio_bytes))
    return chunks


# ---- subtitle segmentation (optional post-transcription pass) ----------

# Rough subtitle-length target, in characters -- not word count, since
# word length varies a lot and character count tracks on-screen reading
# time (and thus subtitle convention) more directly. No per-word
# timestamps are available from KoboldCpp's transcribe endpoint, so
# splitting is inherently approximate either way -- this just tries to
# be *less* wrong by snapping to a real detected gap near the target
# split point when one exists, rather than pure word-count proportion.
SUBTITLE_MAX_CHARS = 100

# A candidate gap only gets used to time a split if it falls within this
# many seconds of the proportional (word-count-based) estimate -- too far
# off and it's probably an unrelated pause, not the one we're looking for.
_SUBTITLE_GAP_SNAP_TOLERANCE_S = 1.5


def split_chunk_for_subtitles(text: str, start_s: float, end_s: float,
                                local_gaps: List[Gap],
                                max_chars: int = SUBTITLE_MAX_CHARS,
                                snap_to_gaps: bool = True) -> List[tuple]:
    """Splits one chunk's transcribed text into shorter (start_s, end_s,
    text) pieces sized for subtitle display, only at word boundaries.
    `local_gaps` are Gaps detected within this chunk's own audio (offsets
    0..chunk_duration, i.e. as returned by iter_gaps() on the chunk's own
    audio_bytes) -- used to snap split timing to a real pause where one
    exists nearby (if snap_to_gaps), falling back to word-count
    proportion of the chunk's duration otherwise. Approximate either
    way -- KoboldCpp doesn't give us real per-word timestamps to align
    to; snap_to_gaps just tries to be less wrong when a real pause is
    conveniently placed."""
    text = text.strip()
    if not text or len(text) <= max_chars:
        return [(start_s, end_s, text)] if text else []

    words = text.split()
    n_words = len(words)
    duration = end_s - start_s
    if n_words <= 1 or duration <= 0:
        return [(start_s, end_s, text)]

    entries = []
    cursor_word = 0
    seg_start_s = start_s

    while cursor_word < n_words:
        remaining_words = words[cursor_word:]
        remaining_text = " ".join(remaining_words)
        if len(remaining_text) <= max_chars:
            entries.append((seg_start_s, end_s, remaining_text))
            break

        # Find the word index where accumulated chars first reach the target.
        acc_chars = 0
        split_idx = None
        for wi in range(cursor_word, n_words):
            acc_chars += len(words[wi]) + 1  # +1 for the joining space
            if acc_chars >= max_chars:
                split_idx = wi + 1  # split AFTER this word
                break
        if split_idx is None or split_idx >= n_words:
            entries.append((seg_start_s, end_s, remaining_text))
            break

        proportion = split_idx / n_words
        est_time_local = proportion * duration  # chunk-local seconds

        best_gap = None
        if snap_to_gaps and local_gaps:
            best_gap = min(local_gaps, key=lambda g: abs(g.midpoint_s - est_time_local))
            if abs(best_gap.midpoint_s - est_time_local) > _SUBTITLE_GAP_SNAP_TOLERANCE_S:
                best_gap = None

        split_time_local = best_gap.midpoint_s if best_gap is not None else est_time_local
        split_time_abs = min(start_s + split_time_local, end_s)

        piece_text = " ".join(words[cursor_word:split_idx])
        entries.append((seg_start_s, split_time_abs, piece_text))

        seg_start_s = split_time_abs
        cursor_word = split_idx

    return entries


# Default for apply_subtitle_linger, below.
SUBTITLE_LINGER_LONG_PAUSE_S = 0.5  # gaps get this much of a linger


def apply_subtitle_linger(entries: List[tuple],
                            max_full_linger_s: float = SUBTITLE_LINGER_LONG_PAUSE_S,
                            long_pause_linger_s: float = SUBTITLE_LINGER_LONG_PAUSE_S) -> List[tuple]:
    """Extends each SRT-style (start_s, end_s, text) entry's end time to
    reduce blank/no-subtitle stretches between consecutive entries --
    without this, a subtitle disappears the instant speech ends and
    reappears only when the next one's speech starts, which reads as
    flickery for short pauses (a breath, a beat) that a viewer wouldn't
    expect to blank the screen for.

    Every gap gets a linger of up to long_pause_linger_s past the original
    end, rather than disappearing instantly; a gap shorter than that is
    bridged completely (the subtitle stays on screen right up until the
    next one starts, no blank gap). The screen does go blank for the
    remainder of a genuinely long pause -- a subtitle sitting on screen
    through a multi-second silence reads as stale/wrong, not helpful.

    max_full_linger_s is accepted for backward compatibility but is no
    longer distinct from long_pause_linger_s -- both gap sizes use the
    same linger duration now.

    Entries must already be in time order; overlapping entries (end_s
    already >= the next entry's start_s) are left untouched."""
    if not entries:
        return entries
    result = list(entries)
    for i in range(len(result) - 1):
        start_s, end_s, text = result[i]
        next_start_s = result[i + 1][0]
        gap = next_start_s - end_s
        if gap <= 0:
            continue  # already contiguous or overlapping -- leave alone
        if gap <= max_full_linger_s:
            new_end = next_start_s
        else:
            new_end = min(end_s + long_pause_linger_s, next_start_s)
        result[i] = (start_s, new_end, text)
    return result




def _main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Chunk a WAV file at silence boundaries and print/save the result "
                    "for manual inspection -- no transcription, no network calls."
    )
    parser.add_argument("wav_path", type=Path, help="Path to a PCM WAV file")
    parser.add_argument("--out-dir", type=Path, default=None,
                         help="If given, write each chunk out as a numbered .wav file here")
    parser.add_argument("--threshold", type=int, default=ChunkerConfig.amplitude_threshold)
    parser.add_argument("--window-ms", type=int, default=ChunkerConfig.analysis_window_ms)
    parser.add_argument("--min-gap-ms", type=int, default=ChunkerConfig.min_gap_ms)
    args = parser.parse_args()

    config = ChunkerConfig(
        amplitude_threshold=args.threshold,
        analysis_window_ms=args.window_ms,
        min_gap_ms=args.min_gap_ms,
    )

    total_duration_s = wav_duration_s(args.wav_path)
    gaps = list(iter_gaps(args.wav_path, config))
    print(f"File duration: {total_duration_s:.2f}s -- {len(gaps)} gap(s) detected "
          f"(threshold={config.amplitude_threshold}, window={config.analysis_window_ms}ms, "
          f"min_gap={config.min_gap_ms}ms)\n")

    hard_ms = config.resolved_long_silence_ms()
    for g in gaps:
        tag = "HARD (trimmed)" if g.duration_ms >= hard_ms else "soft (candidate cut)"
        print(f"  gap {g.start_s:7.2f}s - {g.end_s:7.2f}s  ({g.duration_ms:6.0f}ms)  {tag}")

    chunks = chunk_audio_file(args.wav_path, config)
    print(f"\n{len(chunks)} chunk(s) planned:")
    for c in chunks:
        print(f"  chunk {c.index:3d}: {c.start_s:7.2f}s - {c.end_s:7.2f}s  ({c.duration_s:5.2f}s)")

    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for c in chunks:
            out_path = args.out_dir / f"chunk_{c.index:03d}.wav"
            out_path.write_bytes(c.audio_bytes)
        print(f"\nWrote {len(chunks)} chunk file(s) to {args.out_dir}")


if __name__ == "__main__":
    _main()
