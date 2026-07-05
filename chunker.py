"""
Splits arbitrary text into small, speakable chunks for TTS.

Why this exists: feeding a longer text to KoboldCpp's TTS in one request
cause voice drift, speed changes, and cutoffs on long inputs.
Splitting at sentence/clause boundaries and re-inserting silence ourselves
(rather than relying on the model to imply a pause) fixes these problems,
gives us better control over speech pauses, and easy rewind/forward control.

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
  - Runs of spaces/tabs collapse to a single space, and a leading/trailing
    one on a wrapped line (i.e. indentation) is dropped entirely -- the
    newline-to-space normalization above already supplies the separator.
  - Runs of the same punctuation mark collapse to a single mark (e.g.
    "......" or ",,,,,,,,"), *except* runs that match a recognized
    narrative-emphasis pattern -- an ellipsis ("...") or a doubled/tripled
    "!"/"?" -- which are left alone since they're probably intentional.
  - If the first word after a single-newline wrap starts with a capital
    letter, it's treated as a new sentence even though the wrapped line
    didn't end in punctuation. This is a deliberately simple heuristic --
    it'll occasionally misfire on a line-leading capitalized name -- traded
    off against correctly catching the much more common case of prose that
    got hard-wrapped without care for where sentences end.
  - An http(s) URL is matched as one atomic token instead of being torn
    apart by the punctuation/word rules above. Any enclosing "<" "/" ">"
    (the classic "<https://example.com>" plain-text convention) are
    dropped, and "." within the URL is spelled out as " dot " so it reads
    naturally and doesn't get mistaken for a sentence end. A trailing
    sentence-punctuation character right after a URL (the period in
    "...see https://example.com.") is left for the normal tokenizer to
    handle as its own token, not swallowed into the URL.

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

# Pause used when we infer a sentence break from a capitalized word right
# after a single-newline wrap, even though there was no actual sentence-
# ending punctuation there. Same weight as a normal sentence-ender since
# that's what we're asserting it functionally is.
IMPLIED_SENTENCE_PAUSE_MS = PAUSE_MAP["."]

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

# Consecutive-punctuation run lengths that are treated as deliberate
# narrative emphasis rather than accidental repetition, and therefore kept
# as-is instead of being collapsed down to one mark. Anything not listed
# here (a run of 4+ exclamation points, two periods, eight commas, ...)
# collapses to a single instance of the mark.
NARRATIVE_PUNCT_RUN_LENGTHS = {
    ".": {3},        # ellipsis: "..."
    "!": {2, 3},      # "!!" / "!!!"
    "?": {2, 3},      # "??" / "???"
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
    # http(s) URL, optionally wrapped in <...> (the common plain-text
    # convention). Tried before punct/word so a URL's internal ":" "/" "."
    # never get carved up as ordinary sentence punctuation. The trailing
    # character class excludes common sentence punctuation/brackets so a
    # real sentence-ender or closing paren right after the URL is left for
    # the normal tokenizer to pick up instead of being swallowed here.
    r"|(?P<url><?(?:https?://)[^\s<>]*[^\s<>.,;:!?)\]]>?)"
    r"|(?P<punct>[,;:.!?\u2014\u2013])"
    # A run of ordinary characters -- but each step first checks it isn't
    # about to walk into a "-\n<word char>" wrap (left for hyphenjoin) or
    # the start of a URL (left for the url alternative above), so those
    # don't get eaten here as plain word characters.
    r"|(?P<word>(?:(?!-\n\w)(?!<?https?://)[^\n,;:.!?\u2014\u2013])+)"
)


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


def _normalize_word(raw: str) -> str:
    """Collapse runs of spaces/tabs in a matched `word` token down to a
    single space, then drop a leading or trailing one entirely.

    Inter-token spacing is already handled separately by `pending_glue`
    (set from punctuation and single-newline handling), so a leftover
    leading/trailing space here would just double it up. This is also what
    makes line-leading indentation disappear: spaces/tabs before the first
    real word of a wrapped line always land at the start of a `word` token
    (indentation right after a *paragraph* break is discarded even
    earlier, by `_TOKEN_RE`'s parabreak alternative itself)."""
    return re.sub(r"[ \t]+", " ", raw).strip(" ")


def _collapse_punct_run(char: str, count: int) -> str:
    """Decide what to actually send to the TTS engine for `count`
    consecutive copies of `char` found in the source text."""
    if count in NARRATIVE_PUNCT_RUN_LENGTHS.get(char, ()):
        return char * count
    return char


def _humanize_url(raw: str) -> str:
    """Strip a matched URL's enclosing <...> wrapper (if present) and
    spell out "." as " dot " so KoboldCpp reads a domain like "fsf.org" as
    "fsf dot org" instead of pausing on it like a sentence end."""
    core = raw
    if core.startswith("<"):
        core = core[1:]
    if core.endswith(">"):
        core = core[:-1]
    return re.sub(r"\s+", " ", core.replace(".", " dot ")).strip()


def _tokenize(text: str) -> List[Chunk]:
    """Return raw (pre-merge, pre-long-split) Chunks from source text, in
    reading order."""
    chunks: List[Chunk] = []
    builder = _Builder()
    pending_glue = ""
    pending_punct: Optional[dict] = None
    just_had_linebreak = False  # True right after a single (non-paragraph) \n

    def flush_punct() -> None:
        """Close out whatever punctuation run is in progress, if any,
        collapsing it to a single chunk-ending token."""
        nonlocal builder, pending_glue, pending_punct
        if pending_punct is None:
            return
        text_out = _collapse_punct_run(pending_punct["char"], pending_punct["count"])
        builder.add(text_out, pending_punct["start"], pending_punct["end"], pending_glue)
        chunks.append(builder.build(PAUSE_MAP.get(pending_punct["char"], DEFAULT_PAUSE_MS)))
        builder = _Builder()
        pending_glue = ""
        pending_punct = None

    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup

        if kind == "punct":
            ch = m.group()
            if (
                pending_punct is not None
                and pending_punct["char"] == ch
                and pending_punct["end"] == m.start()
            ):
                # Same mark, immediately contiguous with the run so far --
                # extend it rather than closing a chunk for every copy.
                pending_punct["end"] = m.end()
                pending_punct["count"] += 1
            else:
                flush_punct()
                pending_punct = {"char": ch, "start": m.start(), "end": m.end(), "count": 1}
            just_had_linebreak = False
            continue

        # Any non-punct token means a punctuation run (if any) is over.
        flush_punct()

        if kind == "parabreak":
            if not builder.is_empty():
                chunks.append(builder.build(PARAGRAPH_PAUSE_MS))
                builder = _Builder()
            elif chunks:
                # The chunk that would have carried this pause was already
                # closed out (e.g. by a preceding "." or punctuation run) --
                # upgrade its pause instead of silently losing the longer,
                # paragraph-break pause.
                chunks[-1].pause_ms = max(chunks[-1].pause_ms, PARAGRAPH_PAUSE_MS)
            pending_glue = ""
            just_had_linebreak = False
        elif kind == "hyphenjoin":
            pending_glue = ""  # next word glues directly onto this one, no space
            just_had_linebreak = False
        elif kind == "linebreak":
            pending_glue = " "  # a single newline just becomes a word-space
            just_had_linebreak = True
        elif kind == "url":
            humanized = _humanize_url(m.group())
            if not humanized:
                continue
            builder.add(humanized, m.start(), m.end(), pending_glue)
            pending_glue = ""
            just_had_linebreak = False
        elif kind == "word":
            raw_word = m.group()
            leading_ws = raw_word[:1] in (" ", "\t")
            trailing_ws = raw_word[-1:] in (" ", "\t")
            normalized = _normalize_word(raw_word)
            if not normalized:
                # Pure whitespace (only possible wedged between punctuation
                # and a following newline) -- nothing to add, and whatever
                # comes right after (always punctuation or a newline in
                # this case) sets the glue itself.
                continue
            # Normally pending_glue already carries the right separator
            # (from punctuation or a newline). But a word token can now
            # also sit directly next to a url token with nothing between
            # them, in which case pending_glue is empty and this word's own
            # leading whitespace (stripped out by _normalize_word) is the
            # only place that separator ever existed -- so fall back to it.
            glue = pending_glue or (" " if leading_ws else "")
            if (
                just_had_linebreak
                and not builder.is_empty()
                and normalized[0].isupper()
            ):
                # Capitalized word right after a line wrap: treat it as an
                # implied sentence break even without real punctuation.
                chunks.append(builder.build(IMPLIED_SENTENCE_PAUSE_MS))
                builder = _Builder()
                glue = ""  # fresh chunk -- no leading space wanted regardless
            builder.add(normalized, m.start(), m.end(), glue)
            # Same idea in reverse: hand off this word's own trailing
            # whitespace as glue for whatever comes next, since nothing
            # else will if the next token is a url with no punctuation or
            # newline in between.
            pending_glue = " " if trailing_ws else ""
            just_had_linebreak = False

    flush_punct()
    if not builder.is_empty():
        chunks.append(builder.build(DEFAULT_PAUSE_MS))
    return chunks


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


def _merge_short_and_abbrev(chunks: List[Chunk]) -> List[Chunk]:
    """Merge runs of short fragments (and abbreviation-truncated ones)
    forward into the next chunk, same behavior as before -- except a short
    chunk of *real content* that already carries a paragraph-level pause
    is left alone. That pause is deliberate (e.g. a standalone heading/
    title line followed by a blank line), not the "lone short utterance
    sounds choppy" problem this merge exists to fix, and merging would
    drag the pause away from right after the heading to wherever the
    *next* chunk happens to end. "Real content" (at least one letter or
    digit) is the key qualifier -- a lone stray punctuation mark that
    happens to have inherited a paragraph pause (see _tokenize's parabreak
    handling) should still merge forward rather than get sent to the TTS
    engine as a standalone one-character utterance."""
    merged: List[Chunk] = []
    buffer: Optional[Chunk] = None
    for c in chunks:
        if buffer is None:
            buffer = c
            continue
        words = buffer.text.split()
        last_word = words[-1].lower() if words else ""
        is_short_or_abbrev = len(buffer.text) < MIN_CHUNK_CHARS or last_word in ABBREVIATIONS
        protect_pause = (
            buffer.pause_ms >= PARAGRAPH_PAUSE_MS
            and any(ch.isalnum() for ch in buffer.text)
        )
        if is_short_or_abbrev and not protect_pause:
            offset = len(buffer.text) + 1  # +1 for the joining space below
            buffer = Chunk(
                text=f"{buffer.text} {c.text}",
                spans=buffer.spans + c.spans,
                # Normally c's own pause wins (that's where the merged chunk
                # actually ends) -- but take whichever is longer so a short
                # fragment carrying an upgraded paragraph-break pause (e.g. a
                # lone trailing quote mark right before a paragraph break)
                # doesn't just vanish into a shorter comma/word pause.
                pause_ms=max(buffer.pause_ms, c.pause_ms),
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
    raw = (_strip_chunk(c) for c in _tokenize(text))
    raw = [c for c in raw if c.text]
    merged = _merge_short_and_abbrev(raw)
    final: List[Chunk] = []
    for c in merged:
        final.extend(_split_if_long(c))

    # `_merge_short_and_abbrev` only merges short fragments *forward* into
    # the next chunk, so a too-short final chunk has nothing to merge into
    # and slips through as its own tiny chunk. Fold it backward instead --
    # unless the previous chunk is real content ending on a deliberate
    # paragraph pause, in which case leave it alone for the same reason as
    # above. Done here, after the long-chunk splitting above, so the
    # merged result never gets force-split again even if it now runs a bit
    # over LONG_CHUNK_WORD_LIMIT -- that's fine, it won't be by much.
    if len(final) >= 2 and len(final[-1].text) < MIN_CHUNK_CHARS:
        prev_protected = (
            final[-2].pause_ms >= PARAGRAPH_PAUSE_MS
            and any(ch.isalnum() for ch in final[-2].text)
        )
        if not prev_protected:
            last = final.pop()
            prev = final[-1]
            offset = len(prev.text) + 1  # +1 for the joining space below
            final[-1] = Chunk(
                text=f"{prev.text} {last.text}",
                spans=prev.spans + last.spans,
                pause_ms=last.pause_ms,
                text_ranges=prev.text_ranges + [(ts + offset, te + offset) for ts, te in last.text_ranges],
            )
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
