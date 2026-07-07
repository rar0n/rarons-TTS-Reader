<img width="831" height="628" alt="image" src="https://github.com/user-attachments/assets/b0a29022-213a-4d0a-bce8-99f389380c15" />

# rarons TTS Reader - Read long-form text aloud (KoboldCpp API)

- Built to work around KoboldCpp's tendency to drift in voice/speed
  and stop on long single-shot TTS requests.

A small Python app that reads pasted text aloud through KoboldCpp's
TTS API, with live highlighting one sentence at a time, better pauses
(hopefully), and basic Play, Pause/Resume, Rwd/Fwd and Stop controls.


### Vibe coded

Full disclosure, I don't know Python that well. Claude does though.
As I found KoboldCpp TTS flunked out on longer-form text I vibe-coded this
with Claude Sonnet 5 on Extra / High  and Medium effort, over a few free
sessions (which is awesome btw, so thanks to Anthropic for that!).

So KoboldCpp only gets one sentence at a time, which works much better.

At least partly the reason KoboldCpp stopped rendering voice, is probably
due to me not setting enough context memory. But I doubt very much it could
do 30+ minutes (tested with this app) to a few hours (not tested) anyway,
within my 16 GB VRAM when used as context memory (I'm no AI expert though).
At least this way that shouldn't be a concern almost regardless of lenght
I think. At least not within your system's memory capacity to store audio
samples (Rougly 200 MB / 30ish minutes, IIRC).

One issue might be if your system renders speech slower than real-time,
there will be longer pauses between sentences (or chunks).
You can just wait it out and save as wav or mp3 for later listen though.
(just press Play, then again to Pause, wait, and save it).

Note saving as mp3 might take a little while, depending on size.


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
   python3 -m venv .venv
   source .venv/bin/activate
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
    source .venv/bin/activate
    python3 main.py
    ```
5. Paste text, check the KoboldCpp URL/voice fields, hit **▶ Play**
    It can take a few seconds before it starts reading the first time.

KoboldCpp default URL (`http://127.0.0.1:5001`) is already filled in, change
it if needed.

If leaving the voice field empty (or "Default"), KoboldCpp will just
pick a random speaker for each sentence. Might not be what you want.
Pick an actual named voice (e.g. `kobo`, `cheery`) for a consistent voice.

No need to use the KoboldCpp web page GUI that auto starts. Just exit it.


## Controls

Pretty self explanatory, but:

### Reader tab

| Button | Action |
|---|---|
| ▶ Play / Pause | Start new TTS narration (or Pause / Resume) |
| ⏮ Rewind | Jump back one sentence (chunk) and replay |
| ⏭ Forward | Jump forward one sentence (chunk) |
| ⏹ Stop | Stop and reset |
| Save Audio | Save as wav or mp3 (when finished rendering) |
| ⟳ (next to Voice) | Re-fetch the voice list from KoboldCpp |

Also, Ctrl + mouse scrollwheel = Zoom text in/out.

### Settings tab

Save / Load settings and Reset to defaults. Nice to have.


## Tuning (Settings tab)

- Pauses (milliseconds) — adjust how long each punctuation mark pauses for.
- Chunk sizing
  - Min chunk chars — raise this if chunks still sound choppy (more
    merging), lower it for more granular chunks / highlighting.
  - Long-chunk word limit: — how many words trigger a forced mid-sentence
    split for punctuation-free walls of text.
- Abbreviations list — Comma or newline separated list of typical abbreviations
  ending in a period, that's not a sentence end.


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


## Known Issues

- There's no "continue from selection" function, for now just delete the
  preceding text in the textbox if need be after a full stop.
- Lines of repeating punctuation might make weird sounds, but it should
  mostly ignore those (except ellipses (...) etc.).


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

- 2026.07.07 rarons TTS Reader v0.40 - Settings tab, highlight tweaks,
                                        chunk rules, MIT License,
- 2026.07.05 rarons TTS Reader v0.30 - Save audio, "zoomable" text
- 2026.07.04 rarons TTS Reader v0.25 - Improved pauses and highlights
- 2026.07.03 rarons TTS Reader v0.20 - Initial release
