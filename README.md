<img width="820" height="628" alt="image" src="https://github.com/user-attachments/assets/0e78531a-5a6c-4060-b7ff-bfa3d1a610a9" />

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


    2026 raron ( But mostly Claude :) )


## License

  Basically, there's no license.
  If you use it somewhere or improve it, I would appreciate a mention,
  but you don't have to.

  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

  Use at your own risk, modify as you see fit.

  That's all.


## Setup / Install

1. In KoboldCpp: **Settings → Media → Text To Speech →
   "OpenAI-Compat. API Server"** (load a TTS model first, e.g. Qwen3TTS).
2. Save/extract the TTS Reader into a folder, `cd to that folder` and install dependencies
   into a venv (virtual environment):
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
| Save Audio | Save as wav or mp3 (when finished rendering) |

Also, Ctrl + mouse scrollwheel = Zoom text in/out.


## Tuning

(In the source, not from GUI)

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


## Known Issues

 - In some instances, multiple spaces at the beginning of a line might be
   highlighted as well as any words following on the same line.
 - Lines of repeating punctuation might make weird sounds, but it should
   mostly ignore those (except ellipses (...) etc.).


## Version history

  2026.07.05 rarons TTS Reader v0.3  - Save audio, "zoomable" text.
  2026.07.04 rarons TTS Reader v0.25 - Improved pauses and highlights.
  2026.07.03 rarons TTS Reader v0.2  - Initial release
