# rarons TTS Reader - Read long-form text aloud (KoboldCpp API)

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

 ( Btw I have no idea exactly - how - long a text it can take, depends on
   your system memory I think, as the app gets the audio continuously ahead
   of the speech, unless it's inferencing speech slower than real-time )

 Oh, btw #2 - The thing is 99% vibe coded with Claude Sonnet 5
 (free tier over a few sessions. So that's awesome imho).

    2026 raron ( But mostly Claude :) )


## License

  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

  Use at your own risk, modify as you see fit.

  That's all.


## Setup

1. In KoboldCpp: **Settings → Media → Text To Speech →
   "OpenAI-Compat. API Server"** (load a TTS model first, e.g. Qwen3TTS).
2. Install dependencies into a venv (virtual environment):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
   (On Linux you may also need the PortAudio runtime: `sudo apt install libportaudio2`)
3. Run it:
   ```bash
   python main.py
   ```
4. Set the KoboldCpp URL (default `http://127.0.0.1:5001`), pick a voice
   from the dropdown (hit ⟳ to (re)fetch the list from KoboldCpp), paste in
   some text, and hit **▶ Play**.

   If leaving the voice field empty (or `default`), KoboldCpp will just
   pick a random speaker for each sentence. Might not be what you want.
   Pick an actual named voice (e.g. `kobo`, `cheery`) for a consistent voice.


## Usage:

  1. Run KoboldCpp in terminal / CLI from its folder (easier to exit by ctrl-C).
     In KoboldCpp: Settings -> Media -> Text To Speech ->
       "OpenAI-Compat. API Server" (load Qwen3TTS or whichever model first).
  2. Run python3 main.py from its folder (activate the venv first):
     ```bash
     source .venv/bin/activate
     python3 main.py
     ```
  3. Paste text, check the KoboldCpp URL/voice fields, hit Play.
     It can take a few seconds before it starts reading the first time.

Actually I found point 1 isn't needed, at least not for KoboldCpp v1.116.
But you do need to load a voice model first:

  In KoboldCpp's Audio tab (vertical tabs in left panel), set:
  - "TTS model (Text-to-speech)"
  - "WavTokenizer model (Required for some models)"
  - "TTS Voice Dir" (only if you have custom voices, set folder/directory here)
  - Might also want to tick "Use GPU" if you have a suitable one.

  (For simplicity later, save the KoboldCpp config. Reload on next use).

No need to use the KoboldCpp web page GUI that auto starts. Just exit it.


## Controls
Pretty self explanatory, but:

| Button | Action |
|---|---|
| ▶ Play | Start fresh (or resume if paused) |
| ⏸ Pause / ▶ Resume | Pause/resume mid-sentence without losing your place |
| ⏮ Rewind | Jump back one sentence (chunk) and replay |
| ⏭ Skip | Jump forward one sentence (chunk) |
| ⏹ Stop | Stop and reset |
| ⟳ (next to Voice) | Re-fetch the voice list from KoboldCpp |


## How it works

(This is just Claude explaining the inner workings, might be useful)

- **`chunker.py`** splits the input text into small chunks at sentence and
  clause boundaries (periods, commas, semicolons, colons, em/en-dashes),
  each tagged with how long a pause should follow it. Very short fragments
  get merged forward, and common abbreviations (Dr., Mr., e.g., etc.) are
  protected from being treated as sentence boundaries.

  It's also aware of plain-text line-wrapping conventions:
  - A single newline is *not* a sentence end — it's folded into the same
    sentence as a word-space, so a paragraph that's hard-wrapped at 80
    columns still reads as one continuous sentence instead of one per line.
  - Two or more consecutive newlines *are* a break (a paragraph end), with
    a longer pause than a normal sentence.
  - A hyphen immediately followed by a newline and then a word character
    (`mes-\nsage`) is treated as a soft hyphen and stitched back into one
    word (`message`), rather than being read as "mes, dash, sage".
  - A very long run of text with no punctuation at all (100+ words) gets
    force-split near its midpoint on a word boundary, so KoboldCpp never
    gets handed one giant request.

  Because the text sent to KoboldCpp is a normalized version of the source
  (newlines swapped for spaces, hyphens removed, etc.), each `Chunk` tracks
  a list of source-text spans (`Chunk.spans`) rather than one `start`/`end`
  pair — a chunk built from wrapped/hyphenated lines maps back to several
  separate ranges of the original text, which is what the highlighter uses
  to select exactly what was spoken.
- **`tts_client.py`** is a thin wrapper that POSTs each chunk to KoboldCpp's
  OpenAI-compatible endpoint (`/v1/audio/speech`) and gets back WAV bytes.
  It also queries `/api/extra/speakers_list` to discover available voices.
- **`synth_worker.py`** runs in a background `QThread`, synthesizing chunks
  in order *ahead* of playback so audio is usually ready by the time it's
  needed — synthesis keeps running even while playback is paused.
- **`audio_engine.py`** plays the synthesized chunks back to back using
  `sounddevice`. A single `OutputStream` is opened once and kept open for
  the app's lifetime; a dedicated feeder thread writes chunk audio and
  inter-chunk silence into it in small blocks. Pause/resume is
  sample-accurate (pausing just stops feeding the stream; resuming
  continues from the exact frame it left off), and rewind/skip are cheap
  since each chunk is a separate clip — no seeking inside one long stream.
- **`main.py`** is the GUI: paste text, hit Play, and the currently-spoken
  chunk gets highlighted directly in the text box as it's read (your
  "subtitle"/visual-aid effect). The Voice field is a dropdown populated
  from KoboldCpp's speaker list, with a refresh button and the ability to
  type a custom voice name that isn't in the list.


## Tuning

- `chunker.PAUSE_MAP` — adjust how long each punctuation mark pauses for.
- `chunker.PARAGRAPH_PAUSE_MS` — pause length for a paragraph break (2+
  consecutive newlines) that isn't already followed by real punctuation.
- `chunker.MIN_CHUNK_CHARS` — raise this if chunks still sound choppy (more
  merging), lower it for more granular highlighting/rewind points.
- `chunker.LONG_CHUNK_WORD_LIMIT` / `FORCED_SPLIT_PAUSE_MS` — how many
  words trigger a forced mid-sentence split for punctuation-free walls of
  text, and how short a pause that artificial split gets.
- `chunker.ABBREVIATIONS` — add any other abbreviations you run into.
- `synth_worker.py` currently synthesizes one chunk at a time, sequentially.
  If your GPU has headroom, you could run a small thread pool there instead
  for faster lookahead — but most local TTS servers serialize generation on
  the GPU anyway, so this usually isn't a bottleneck.

## Known limitations

- Voice cloning / specific voice names depend entirely on how your KoboldCpp
  instance is configured (`--ttsdir` for Qwen3TTS clones) — the voice
  dropdown just reflects whatever `/api/extra/speakers_list` reports, and
  you can always type a name manually if it isn't listed there.
- Sentence splitting is regex-based, not a full NLP sentence tokenizer, so
  unusual punctuation (nested quotes, ellipses, etc.) may need extra rules
  in `chunker.py` if you hit edge cases. The hyphen-line-wrap join is a
  heuristic too (hyphen + newline + word-char = join) — it can't tell a
  genuine paginated word-wrap apart from a dash that just happens to fall
  at the end of a line, so it always treats that pattern as a join.


## Issues

 - In rare instances, multiple spaces at the beginning of a line might be
   highlighted instead of any words following on the same line.
   It should still speak the words though.
 - Lines of repeating punctuation sounds weird.
 - If the last chunk to speak is short, it might sound weird (should be rare).
 - Some times a paragraph title continues seemingly without a pause after it
   (IE a line of text, with at least 2 consequtive newlines after it).


## Version history

  2026.07.03 rarons TTS Reader v0.2 (let's say) - raron / Claude Sonnet 5
