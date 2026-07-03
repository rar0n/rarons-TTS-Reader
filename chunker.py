"""
Splits arbitrary text into small, speakable chunks for TTS.

Why this exists: feeding a whole paragraph to KoboldCpp's TTS in one request
is what causes voice drift, speed changes, and mid-paragraph cutoffs on long
inputs. Splitting at sentence/clause boundaries and re-inserting silence
ourselves (rather than relying on the model to imply a pause) fixes both
problems and gives us natural points to highlight, pause, and rewind.

Plain-text-specific handling on top of that:

  - A single newline is NOT treated as a sentence end -- it's normalized to
    a word-space and folded into whatever sentence is still being built.
    Real prose gets pasted with hard line wraps all the time; without this,
    every line would get spoken as if it were its own sentence.
  - Two or more consecutive newlines ARE treated as a break (a paragraph
    end), with a longer pause than a regular sentence.
  - A hyphen immediately followed by a newline, immediately followed by a
    word character (e.g. "mes-\\nsage", the classic paginated-text word
    wrap) is treated as a soft hyphen: the "-\\n" is removed entirely so
    the word reads as "message" instead of "mes dash sage".
  - A run of text with no punctuation and no whitespace-based break for a
    very long stretch (100+ words -- think a giant unpunctuated pasted
    paragraph) gets force-split near its midpoint on a word boundary, so
    we never hand KoboldCpp one enormous request.

Because the text sent to the TTS engine is now a *normalized* version of
the source (newlines swapped for spaces, hyphens removed, etc.), a chunk
no longer maps to one contiguous slice of the original text. Chunk.spans
holds the (possibly several) source-text ranges that were stitched
together to build Chunk.text, in order, so callers that want to highlight
"everything that was sent to KoboldCpp for this chunk" can select each
span individually.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# How long to pause (ms) after a chunk that ended on each punctuation mark.
# Commas/semicolons get a short breath, em/en-dashes (often used for a
# "trailing thought") get a bit more, sentence-enders get the most.
PAUSE_MAP = {
    ",": 150,
    ";": 250,
    ":": 250,
    "\u2014": 350,  # em-dash
    "\u2013": 350,  # en-dash
    ".": 450,
    "!": 450,
    "?": 450,
}
DEFAULT_PAUSE_MS = 200

# Pause after a paragraph break (2+ consecutive newlines) -- a bit longer
# than a plain sentence-ender, since it's a bigger structural break.
PARAGRAPH_PAUSE_MS = 600

# Chunks shorter than this (in characters, after stripping) get merged into
# the next chunk so the TTS engine never has to voice a lone word or a
# stray bit of punctuation on its own -- that's what tends to sound choppy.
MIN_CHUNK_CHARS = 12

# If a single chunk (after merging) is still this many words or longer with
# no natural break, force a split near the middle on a word boundary. This
# is a safety valve for giant unpunctuated pastes, not a "correct" sentence
# split -- so it gets a short, unobtrusive pause rather than a real one.
LONG_CHUNK_WORD_LIMIT = 100
FORCED_SPLIT_PAUSE_MS = 80

# Common abbreviations that end in a period but aren't actually sentence
# boundaries -- without this, "Dr." or "e.g." gets read as a full stop.
ABBREVIATIONS = {
    "dr.", "mr.", "mrs.", "ms.", "prof.", "st.", "jr.", "sr.",
    "vs.", "etc.", "e.g.", "i.e.", "no.", "vol.", "approx.", "ave.",
}

# Tokenizes the source text into: paragraph breaks, soft-hyphen line-wrap
# joins, plain single newlines, clause/sentence punctuation, and runs of
# ordinary text. Alternatives are tried in this order at each position, so
# more specific patterns (paragraph break, hyphen-join) win over the
# generic single-newline case.
_TOKEN_RE = re.compile(
    r"(?P<parabreak>\n[ \t]*\n[ \t\n]*)"
    r"|(?P<hyphenjoin>-\n(?=\w))"
    r"|(?P<linebreak>\n)"
    r"|(?P<punct>[,;:.!?\u2014\u2013])"
    # A run of ordinary characters -- but each step first checks it isn't
    # about to walk into a "-\n<word char>" wrap, so that sequence gets
    # left for the hyphenjoin alternative above instead of being eaten
    # here as a literal trailing hyphen.
    r"|(?P<word>(?:(?!-\n\w)[^\n,;:.!?\u2014\u2013])+)"
)

# Strip multiple internal whitespaces
_MULTI_SPACE_RE = re.compile(r" {2,}")


@dataclass
class Chunk:
    text: str                          # normalized text to send to the TTS engine
    spans: List[Tuple[int, int]]       # source (start, end) ranges that make up `text`, in order
    pause_ms: int                      # silence to insert after this chunk finishes playing
    text_ranges: List[Tuple[int, int]] = field(default_factory=list)
    # text_ranges[i] is where spans[i]'s characters land inside `text` --
    # used internally to split long chunks without corrupting the spans.

    @property
    def start(self) -> int:
        """First source offset covered by this chunk (for simple callers)."""
        return self.spans[0][0] if self.spans else 0

    @property
    def end(self) -> int:
        """Last source offset covered by this chunk (for simple callers)."""
        return self.spans[-1][1] if self.spans else 0


class _Builder:
    """Accumulates the pieces of one in-progress chunk."""

    def __init__(self):
        self.parts: List[str] = []
        self.spans: List[List[int]] = []
        self.text_ranges: List[List[int]] = []
        self._cur_len = 0

    def add(self, text: str, start: int, end: int, glue: str):
        if self.parts:
            self.parts.append(glue)
            self._cur_len += len(glue)
        self.parts.append(text)
        piece_start = self._cur_len
        self._cur_len += len(text)
        piece_end = self._cur_len
        if self.spans and self.spans[-1][1] == start:
            # Contiguous with the previous span in the source -- extend it
            # rather than starting a new one (keeps the common case, plain
            # text with no newlines/hyphenation involved, down to one span).
            self.spans[-1][1] = end
            self.text_ranges[-1][1] = piece_end
        else:
            self.spans.append([start, end])
            self.text_ranges.append([piece_start, piece_end])

    def is_empty(self) -> bool:
        return not self.spans

    def build(self, pause_ms: int) -> Chunk:
        return Chunk(
            text="".join(self.parts),
            spans=[tuple(s) for s in self.spans],
            pause_ms=pause_ms,
            text_ranges=[tuple(t) for t in self.text_ranges],
        )


def _tokenize(text: str):
    """Yield raw (pre-merge, pre-long-split) Chunks from source text."""
    builder = _Builder()
    pending_glue = ""
    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup
        if kind == "parabreak":
            if not builder.is_empty():
                yield builder.build(PARAGRAPH_PAUSE_MS)
                builder = _Builder()
            pending_glue = ""
        elif kind == "hyphenjoin":
            pending_glue = ""  # next word glues directly onto this one, no space
        elif kind == "linebreak":
            pending_glue = " "  # a single newline just becomes a word-space
        elif kind == "punct":
            builder.add(m.group(), m.start(), m.end(), pending_glue)
            yield builder.build(PAUSE_MAP.get(m.group(), DEFAULT_PAUSE_MS))
            builder = _Builder()
            pending_glue = ""
        elif kind == "word":
            builder.add(m.group(), m.start(), m.end(), pending_glue)
            pending_glue = ""
    if not builder.is_empty():
        yield builder.build(DEFAULT_PAUSE_MS)


def _strip_chunk(chunk: Chunk) -> Chunk:
    """Trim leading/trailing whitespace from chunk.text, keeping spans and
    text_ranges in sync (dropping spans that fall entirely in the trimmed
    region, shrinking the ones on the boundary)."""
    text = chunk.text
    lstripped = text.lstrip()
    lcut = len(text) - len(lstripped)
    stripped = lstripped.rstrip()
    keep_end = lcut + len(stripped)
    if lcut == 0 and keep_end == len(text):
        return chunk

    out_spans, out_ranges = [], []
    for (s, e), (ts, te) in zip(chunk.spans, chunk.text_ranges):
        rs, re_ = max(ts, lcut), min(te, keep_end)
        if rs >= re_:
            continue  # this span was entirely inside the stripped whitespace
        out_spans.append((s + (rs - ts), e - (te - re_)))
        out_ranges.append((rs - lcut, re_ - lcut))
    return Chunk(text=stripped, spans=out_spans, pause_ms=chunk.pause_ms, text_ranges=out_ranges)



def _remove_range(text, spans, ranges, drop_start, drop_end):
    """Remove text[drop_start:drop_end], re-basing text_ranges and shrinking
    any (span, range) pair that overlaps the removed region."""
    drop_len = drop_end - drop_start
    new_text = text[:drop_start] + text[drop_end:]

    def remap(pos):
        # Positions inside the dropped region collapse onto drop_start;
        # positions after it shift left by drop_len; positions before it
        # are untouched.
        return pos - max(0, min(pos, drop_end) - drop_start)

    out_spans, out_ranges = [], []
    for (s, e), (ts, te) in zip(spans, ranges):
        overlap = max(0, min(te, drop_end) - max(ts, drop_start))
        if overlap >= te - ts:
            continue  # range was entirely inside the dropped region
        out_spans.append((s, e - overlap))
        out_ranges.append((remap(ts), remap(te)))

    return new_text, out_spans, out_ranges


def _collapse_internal_spaces(chunk: Chunk) -> Chunk:
    """Collapse runs of 2+ interior spaces down to a single space, keeping
    spans/text_ranges in sync. Only literal spaces are touched -- tabs and
    newlines aren't, since the tokenizer already breaks on those elsewhere.
    """
    text = chunk.text
    matches = list(_MULTI_SPACE_RE.finditer(text))
    if not matches:
        return chunk

    # Keep the first space of each run, drop the rest.
    drops = [(m.start() + 1, m.end()) for m in matches]

    spans, ranges = list(chunk.spans), list(chunk.text_ranges)
    # Right-to-left so earlier drop indices stay valid as we mutate text.
    for drop_start, drop_end in reversed(drops):
        text, spans, ranges = _remove_range(text, spans, ranges, drop_start, drop_end)

    return Chunk(text=text, spans=spans, pause_ms=chunk.pause_ms, text_ranges=ranges)




def _merge_short_and_abbrev(chunks: List[Chunk]) -> List[Chunk]:
    """Merge runs of short fragments (and abbreviation-truncated ones)
    forward into the next chunk, same behavior as before."""
    merged: List[Chunk] = []
    buffer: Optional[Chunk] = None
    for c in chunks:
        if buffer is None:
            buffer = c
            continue
        words = buffer.text.split()
        last_word = words[-1].lower() if words else ""
        if len(buffer.text) < MIN_CHUNK_CHARS or last_word in ABBREVIATIONS:
            offset = len(buffer.text) + 1  # +1 for the joining space below
            buffer = Chunk(
                text=f"{buffer.text} {c.text}",
                spans=buffer.spans + c.spans,
                pause_ms=c.pause_ms,
                text_ranges=buffer.text_ranges + [(ts + offset, te + offset) for ts, te in c.text_ranges],
            )
        else:
            merged.append(buffer)
            buffer = c
    if buffer is not None:
        merged.append(buffer)
    return merged


def _split_chunk_at(chunk: Chunk, idx: int) -> Tuple[Chunk, Chunk]:
    """Split `chunk` into two chunks at local text index `idx`."""
    left_spans, left_ranges = [], []
    right_spans, right_ranges = [], []
    for (s, e), (ts, te) in zip(chunk.spans, chunk.text_ranges):
        if te <= idx:
            left_spans.append((s, e))
            left_ranges.append((ts, te))
        elif ts >= idx:
            right_spans.append((s, e))
            right_ranges.append((ts - idx, te - idx))
        else:
            cut = idx - ts
            left_spans.append((s, s + cut))
            left_ranges.append((ts, idx))
            right_spans.append((s + cut, e))
            right_ranges.append((0, te - idx))
    left = Chunk(text=chunk.text[:idx], spans=left_spans, pause_ms=FORCED_SPLIT_PAUSE_MS, text_ranges=left_ranges)
    right = Chunk(text=chunk.text[idx:], spans=right_spans, pause_ms=chunk.pause_ms, text_ranges=right_ranges)
    return left, right


def _split_if_long(chunk: Chunk, word_limit: int = LONG_CHUNK_WORD_LIMIT) -> List[Chunk]:
    words = chunk.text.split(" ")
    if len(words) <= word_limit or len(words) < 2:
        return [chunk]
    mid = len(words) // 2
    idx = sum(len(w) + 1 for w in words[:mid])
    idx = max(1, min(idx, len(chunk.text) - 1))
    left, right = _split_chunk_at(chunk, idx)
    if not left.spans or not right.spans:
        return [chunk]  # degenerate split (e.g. all one giant "word") -- bail out rather than loop
    return _split_if_long(left, word_limit) + _split_if_long(right, word_limit)


def chunk_text(text: str) -> List[Chunk]:
    """Split `text` into a list of Chunk objects in reading order."""
    raw = ( _collapse_internal_spaces( _strip_chunk(c) ) for c in _tokenize(text))
    #raw = (_strip_chunk(c) for c in _tokenize(text))
    raw = [c for c in raw if c.text]
    merged = _merge_short_and_abbrev(raw)
    final: List[Chunk] = []
    for c in merged:
        final.extend(_split_if_long(c))
    return final


if __name__ == "__main__":
    sample = (
        "The old house stood at the end of the lane—quiet, weathered, "
        "and, some said, watching. Dr. Aris paused. He wasn't sure; "
        "not yet. \"Well,\" he muttered, \"here goes nothing.\"\n\n"
        "He stepped inside, and found the mes-\nsage waiting on the\n"
        "table, just as she'd promised it would be."
    )
    for i, c in enumerate(chunk_text(sample)):
        print(f"[{i:02d}] pause={c.pause_ms:>3}ms  spans={c.spans}  {c.text!r}")
