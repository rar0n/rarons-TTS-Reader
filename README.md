<img width="820" height="628" alt="image" src="https://github.com/user-attachments/assets/af7e7098-b5ac-4107-bc75-c1b79d7ecf46" />

# rarons TTS Reader - Read long-form text aloud (KoboldCpp API)

- Built to work around limited context memory, making KoboldCpp's TTS
  stutter or stop on long single-shot TTS requests.

A small Python app that reads pasted text aloud through KoboldCpp's
TTS API, with live highlighting one sentence at a time, better pauses
(hopefully), and basic controls.

So KoboldCpp only gets one sentence or text chunk at a time, works much better!


### Features

- **Long text** can be spoken via KoboldCpp TTS (Only tested 45ish minute so far)
- **Save audio** as wav or mp3 (when it's finished rendering the TTS)
  - Note save as mp3 may take a bit of time (depending on length etc.)
- **Live highligthing** of spoken sentences (or TTS chunks)
- **highlight margins** (optional), you can set the ratio of screen margin as
  a "Scroll Denominator" (Ex. 4 means 1/4 of screen height. When the highlight
  reaches 1/4 textbox height from its top or bottom edge, it will scroll the
  highlight to the other edge, with the same 1/4 distance.
- **TTS seed value management** (Rudimentary. Can reuse seeds):
  - Save / load to / from "Seed Vault" tab table.
  - Optional notes in the "Seed Vault" table.
- **Extra Pause settings** Add custom pause lengths for various punctuations
  (in addition to KoboldCpp's TTS engine's pauses. Maybe not so useful).
  - These only applies to punctuations _between_ chunks sent to KoboldCpp TTS.
- **Keyboard controls** in addition to GUI:
  - Ctrl + Enter = Play / Speak. When speaking:
    - Space = Pause / resume
    - Arrow left / right = Rewind / Forward
    - ESC = Stop, return to textbox.

One issue might be if your system renders speech slower than real-time,
there will be longer pauses between sentences (TTS chunks sent to KoboldCpp).
You can just wait it out and save as wav or mp3 for later listen though.
(just press Play, then again to Pause, wait, and save it).


### Vibe coded

Yes. Full disclosure: I don't know Python that well. Claude does though :-)
As I found KoboldCpp TTS flunked out on longer-form text I vibe-coded this
with Claude Sonnet 5 on Extra / High  and Medium effort, over now quite a
few free sessions (which is awesome btw, so thanks to Anthropic for that!).

Note 2: Most of the source code comments is Claude's. Some (not all) of the
        reasoning behind _why_ something is done is Claude's assumption and
        a bit off. But a lot is on-point as well!


    2026 Ragnar Aronsen (raron) ( But mostly Claude :) )


## Links

[KoboldCpp](https://github.com/LostRuins/koboldcpp)

[TTS models for narration](https://huggingface.co/koboldcpp/tts/tree/main) (as also linked from KoboldCpp's page above)


Me I've so far only tried, and use:
 - [Qwen3-TTS-12Hz-1.7B-Base-q8_0.gguf](https://huggingface.co/koboldcpp/tts/blob/main/Qwen3-TTS-12Hz-1.7B-Base-q8_0.gguf) (Can also do voice cloning)
 - [qwen3-tts-tokenizer-q8_0.gguf](https://huggingface.co/koboldcpp/tts/blob/main/qwen3-tts-tokenizer-q8_0.gguf)

Other TTS models should work as well.


## Install

1. Save/extract TTS Reader into a folder, `cd to that folder` or just
   right-click the folder and select "Open in Terminal" (Linux).
2. Install dependencies into a venv (virtual environment):
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
   (On Linux you may also need the PortAudio runtime: `sudo apt install libportaudio2`)


## Usage:

1. Run KoboldCpp, I suggest from a terminal / CLI  folder
    (easier to exit by ctrl-C).
2. In KoboldCpp, Audio tab (from the left panel vertical tabs), set:
    - "TTS model (Text-to-speech)"
    - "WavTokenizer model (Required for some models)"
    - "TTS Voice Dir" (only if you have custom voices, set folder/directory here)
    - Might also want to tick "Use GPU" if you have a suitable one.
3. (You may not need to) In KoboldCpp: Settings -> Media -> Text To Speech, select
      "OpenAI-Compat. API Server".

    (For simplicity later, save a KoboldCpp config. Reload on next use).
4. Run TTS Reader by starting `python3 main.py` from its folder
    Activate the venv first:
    ```bash
    source venv/bin/activate
    python3 main.py
    ```
5. Paste text (or drag'n'drop a text file - other files might have odd
   formatting), check the KoboldCpp URL/voice fields, hit **▶ Play**
    It can take a few seconds before it starts reading the first time.

KoboldCpp default URL (`http://127.0.0.1:5001`) is already filled in, change
it if needed.

- No need to use the KoboldCpp web page GUI that auto starts. Just exit it.
- If deleting the voice field (or as "Default"), KoboldCpp will just
  pick a random speaker for each sentence. Might not be what you want.
  Pick an actual named voice (e.g. `kobo`, `cheery`) for a consistent voice.
- If the TTS Reader's "Lock seed" option is unticked, the TTS Reader will
  make one at random on each new speech (play).
- Clicking the dice button next to it makes a random seed value.
- If you happen upon a value you'd like to keep, click "Store seed", and it's
  saved to the Seed Vault tab (as well as in the settings file; settings.json).
  (AFAIK, this feature relies on an undocumented feature of KoboldCpp v1.116
  or its API. No guarantee it'll work in later versions of KoboldCpp)


## Controls

### Narration tab

| Button | Action |
|---|---|
| ▶ Play / Pause | Start new TTS narration (or Pause / Resume) |
| ⏮ Rewind | Jump back one sentence (chunk) and replay |
| ⏭ Forward | Jump forward one sentence (chunk) |
| ⏹ Stop | Stop and reset |
| Save Audio | Save as wav or mp3 (when finished rendering) |
| ⟳ (next to Voice) | Re-fetch the voice list from KoboldCpp |
| 🎲 (next to Seed value) | Randomize seed |
| Lock seed | Stops TTS Reader from making a new seed value on next play |
| Store seed | Store seed in Seed Vault (and disk) |

#### Keyboard / mouse controls:

| Keys | Action |
|---|---|
| Ctrl + mouse scrollwheel | Zoom text in/out |
| Ctrl + Enter | Play (speak) |

##### While speaking

| Keys | Action |
|---|---|
| Space | Pause / Resume |
| Arrow Right | Forward |
| Arrow Left | Rewind |
| ESC | Stop playing |


### Seed Vault tab

It's a table of stored voices and seed values, if saved from Narration tab.
Click on a row's Comment cell to edit a note for your own reference about it.

| Button | Action |
|---|---|
| Remove row | Deletes selected row |
| Copy seed to Narration | Copies selected row's seed and voice |
| Save Table | Updates the saved settings.json file with the table |


### Settings tab

Save / Load settings and Reset to defaults.
Nice to have if you want settins (and Seed Vault) to persist, or if you
want to reload the settings.


## Tuning (Settings tab)

### Note
Settings are kind of experimental / tests. Some might not be that useful.

You might want to set all types of pauses to 0 (maybe except
paragraph pauses?)
- Speech might be a bit slow with default pauses, as all pauses will
  be **additional** to pauses that the TTS engine (KoboldCpp) makes, but
  only at each chunk (sentence) ends.
- Mid-chunk only the TTS engine determines how it's spoken, pauses and all.
- Unless a chunk is deemed too short to be by itself, any punctuation should
  only be at the end of a chunk sent to KoboldCpp.
  (Btw this is also a rule you might tune, see Chunk sizing below).

### Settings

- Pauses (milliseconds) — adjust how long each punctuation mark pauses for.
- Chunk sizing
  - Min chunk chars — raise this if chunks still sound choppy (more
    merging), lower it for more granular chunks / highlighting.
  - Long-chunk word limit: — how many words trigger a forced mid-sentence
    split for punctuation-free walls of text (prevents "overloading" KoboldCpp).
- Abbreviations list — Comma or newline separated list of typical abbreviations
  ending in a period, that's not a sentence end (like "Dr.", "Mr.", etc).


## Known limitations

- Saving as mp3 might take a little while (a few seconds, depending on size),
  during which time it will be unresponsive. Be patient :)
- Sentence splitting is regex-based, not a full NLP sentence tokenizer, so
  unusual punctuation might cause issues (with speech rhytm, highlighting).
- Voice cloning / specific voice names depend entirely on how your KoboldCpp
  instance is configured (`--ttsdir` for Qwen3TTS clones) — the voice
  dropdown just reflects whatever `/api/extra/speakers_list` reports.
  You can always type a name if it isn't listed there (they all should be).
- `synth_worker.py` currently synthesizes one chunk at a time, sequentially.
  If your GPU has headroom, you could run a small thread pool there instead
  for faster lookahead — but most local TTS servers serialize generation on
  the GPU anyway, so this usually isn't a bottleneck.

Btw: At least partly the reason KoboldCpp stopped rendering voice, is probably
due to me not knowing how to use the KoboldCpp properly, like setting enough
context memory.

Still - I doubt **very much** it could do 40+ minutes (tested with this app)
to a few hours (not tested yet but I see no reason why it shouldn't work).

At least this way that shouldn't be a concern almost regardless of lenght
I think. At least not within your system's memory capacity to store audio
samples. Rouglyish 350 MB / hour, about half on disk as wav, even less as mp3.


## Known Issues

- There's no "continue from selection" function, for now just delete the
  preceding text in the textbox if need be after a full stop.
- Lines of repeating punctuation might make weird sounds, but it should
  mostly ignore those (except ellipses (...) etc.).
- Some issues reading URL's at the moment, probably more, I haven't tested
  everything
- (Linux Mint) You probaby will get a few warnings about ALSA underruns.
  (ALSA lib pcm.c:8568:(snd_pcm_recover) underrun occurred). Ignore it :)
  (I'll get it fixed eventually. Probably)
- Scrolling in the Settings tab might inadvertedly change numeric values
  if your mouse cursor hovers over a field while scrolling.


## License

MIT License

Copyright (c) 2026 Ragnar Aronsen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.


Contact: On my github page://github.com/rar0n/rarons-TTS-Reader/


## Version history

- 2026.07.09 rarons TTS Reader v0.55 - Tweaked chunk rules again (not perfect)
                                       (numbers, URL's), Keyboard controls,
                                       Auto scroll highlight margin settings,
                                       GUI tweaks, Error prints to terminal.
- 2026.07.08 rarons TTS Reader v0.50 - More narration rules (numbers, URL's)
                                       Drag'n'drop files, auto scroll,
                                       other tweaks.
- 2026.07.08 rarons TTS Reader v0.45 - TTS seed value, Seed Vault, color tweaks
- 2026.07.07 rarons TTS Reader v0.40 - Settings tab, highlight tweaks,
                                       chunk rules, MIT License,
- 2026.07.05 rarons TTS Reader v0.30 - Save audio, "zoomable" text
- 2026.07.04 rarons TTS Reader v0.25 - Improved pauses and highlights
-
- 2026.07.03 rarons TTS Reader v0.20 - Initial release
