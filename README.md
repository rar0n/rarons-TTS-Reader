<img width="820" height="630" alt="image" src="https://github.com/user-attachments/assets/344b878f-7063-454b-a54f-3db6bf44939e" />

# rarons TTS Reader - Read long-form text aloud (KoboldCpp API)

- Built to work around limited context memory, making KoboldCpp's TTS
  stutter or stop on long single-shot TTS requests.

A small Python app that reads pasted text aloud through KoboldCpp's
TTS API, with live highlighting one sentence at a time, better pauses
(hopefully), and basic controls.

So KoboldCpp only gets one sentence or text chunk at a time, works much better!


### Features

- **Long text TTS** via KoboldCpp TTS (Only tested 45ish minute so far)
- **Save audio** as wav or mp3 (when it's finished rendering the TTS)
- **Live highligthing** (Per sentence / TTS chunk)
- Highlight margins (visible non-highlighted text around highlighted)
- TTS seed value management (Rudimentary. Can reuse seeds)
- Extra Pause settings (Maybe not so useful).
- Keyboard controls in addition to GUI buttons:
  - Ctrl + Enter = Play / Speak. When speaking:
    - Space = Pause / resume
    - Arrow left / right = Rewind / Forward
    - ESC = Stop, return to textbox.


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
2. In KoboldCpp, either load a previously saved config, or go to Audio tab (from the left panel vertical tabs), set:
    - "TTS model (Text-to-speech)"
    - "WavTokenizer model (Required for some models)"
    - "TTS Voice Dir" (only if you have custom voices, set folder/directory here)
    - Might also want to tick "Use GPU" if you have a suitable one.
3. (You may not need to) In KoboldCpp: Settings -> Media -> Text To Speech, select
      "OpenAI-Compat. API Server".

    (For simplicity later, save a KoboldCpp config. Reload on next use).

4. Launch (KoboldCpp)
5. Run TTS Reader by starting `python3 main.py` from its folder, in Terminal:
    Activate the venv first:
    ```bash
    source venv/bin/activate
    python3 main.py
    ```
6. Paste text (or drag'n'drop a text file - other files might have odd
   formatting), check the KoboldCpp URL/voice fields, hit **▶ Play**
    It can take a few seconds before it starts reading the first time.


#### Note

No need to use the KoboldCpp web page GUI that probably auto starts a web browser. Just exit it.


## Controls / notes

### Narration tab

Main tab for TTS
- After a complete playthrough, if you want another version (even with the
  same voice and text), click RND to make a new random seed value before Play.
  - AFAIK, this feature relies on an undocumented feature of KoboldCpp v1.116
    API. No guarantee it'll work in later versions of KoboldCpp.
  - Still works on KoboldCpp v.1.117
  - Range seems to be from 0 to 2^31-1, or 0 to 2147483647.
- If you happen upon a seed value you'd like to keep, click "Store seed".
  - It will be stored in the "Seed Vault", along with voice and instructions.
    - You can also make a note there, for your own reference.
- If the "Lock" checkbox is checked, seed value can't change.
- If deleting the voice field (or set as "Default"), KoboldCpp will just
  pick a random speaker for each sentence. Might not be what you want.
- Same-ish if you enter something in the "instructions" field:
  - It overrides Voice setting (Not really to be used with QwenTTS base
    afaik).
- Note save as mp3 may take a bit of time (depending on length etc.)
  - But shouldn't take more than a few seconds, depending on size, system etc.


| Button / Field | Action |
|---|---|
| Instructions | Optional instructions. NOTE: Overrides voice! |
| Voice drop-down list | Voice list fetched from KoboldCpp |
| ⟳ (Refresh) | Re-fetch the voice list from KoboldCpp |
| 🎲 RND | Randomize seed |
| Lock | Locks seed value, preventing changing it |
| Store seed | Store seed value, voice and instructions to Seed Vault |
| ▶ Play / Pause | Start new TTS narration (or Pause / Resume) |
| ⏮ Rewind | Jump back one sentence (chunk) and replay |
| ⏭ Forward | Jump forward one sentence (chunk) |
| ⏹ Stop | Stops playback and rendering |
| Save Audio | Save as wav or mp3 (when finished rendering) |

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

Just a simple table of stored seed values from the Narration tab.
 - Voice
 - Seed value
 - Any "instructions" text.
 - Optional notes

 After editing a note or instruction, click "Save Table".
 - It's stored in the same file as the settings (settings.json).
 - If you mess up and want to revert to the last saved settings, go to
   Settings tab and reload settings (don't click "Save Table" then...).

| Button | Action |
|---|---|
| Remove row | Deletes selected row |
| Copy row to Narration | Copies selected row's data to Narration tab |
| Save Table | Saves table to disk (settings file) |


### Settings tab

Settings are kind of experimental / tests. Some might not be that useful.

KoboldCpp default URL (`http://127.0.0.1:5001`) is already filled in, change
it if needed.


| Button | Action |
|---|---|
| Save Settings | Saves in settings.json to disk |
| Load Settings... | Loads settings.json from disk |
| Reset to Defaults | Just some arbitrary default values |


#### Scrolling

Narration highlight settings.

Just a quick setting for having more text visible around the currently highlighted
TTS chunk (Except at the beginning and end of the text).

- Highlight Margin (checkbox)
  - Enable or disable highlight margin / distance to top or bottom edge.
- Clamp Highlight Distance
  - Checked   : Keeps the highlight at a constant distance instead of autoscroll.
  - Unchecked : Auto-scrolls the highlight to the opposite edge (top / bottom).
- Scroll Denominator
  - Sets the margin size as ratio (1/SD) of textbox height.
    - Ex. SD = 4 means 1/4 of screen height. When the highlight reaches 1/4
      of the textbox height from its top or bottom edge, it will scroll the
      highlight to the other edge, with the same 1/4 distance.


#### Pauses (milliseconds)

Extra pauses at the end of each TTS sentence chunk (depending on punctuation).
Actually, you might want most of these to 0 (zero). Experiment.

- Speech might be a bit slow with default pauses, as all pauses will
  be **additional** to pauses that the TTS engine (KoboldCpp) makes, but
  only at each chunk's (sentence) end.
- Mid-chunk only the TTS engine determines how it's spoken, pauses and all.
- Unless a chunk is deemed too short to be by itself, any punctuation should
  only be at the end of a chunk sent to KoboldCpp.
  (Btw this is also a rule you might tune, see Chunk sizing below).


#### Chunk sizing

- Min chunk chars
  - Minimum size of a TTS chunk sent to TTS.
    - Lower it for more granular chunks / highlighting.
- Long-chunk word limit
  - How many words trigger a forced mid-sentence split
    for punctuation-free walls of text
  - Prevents "overloading" KoboldCpp.


#### Abbreviations list

Comma or newline separated list of typical abbreviations ending in a period,
that's not a sentence end (like "Dr.", "Mr.", etc).


## Known limitations

- Saving as mp3 might take a little while (a few seconds, depending on size),
  during which time it will be unresponsive. Be patient :)
- Unusual punctuation might cause issues (with speech rhytm, highlighting).
- TTS speech depends on KoboldCpp configuration.
  (Only between chunk pauses, chunk selection, and chunk preparation depends
   on this TTS Reader).
- One issue might be if your system renders speech slower than real-time,
  there will be longer pauses between sentences (TTS chunks sent to KoboldCpp).
  - You can just wait it out and save as wav or mp3 for later listen though.
    (just press Play, then again to Pause, wait, and save it).

Btw: At least partly the reason KoboldCpp stopped rendering voice, is probably
due to me not knowing how to use the KoboldCpp properly, like setting enough
context memory.

Still - I doubt **very much** it could do 40+ minutes (tested with this app)
to a few hours (not tested yet but I see no reason why it shouldn't work).

At least this way that shouldn't be a concern almost regardless of lenght
I think. At least not within your system's memory capacity to store audio
samples. Rouglyish 350 MB / hour, about half on disk as wav, even less as mp3.



## Known Issues

- Long runs of dashes (not so long even), like "------" may make KoboldCpp
  stutter, or crash / hang. (To be fixed...)
- There's no "continue from selection" function, for now just delete the
  preceding text in the textbox if need be after a full stop.
- (Linux Mint) You probaby will get a few warnings about ALSA underruns.
  (ALSA lib pcm.c:8568:(snd_pcm_recover) underrun occurred). Ignore it :)
- Scrolling in the Settings tab might inadvertedly change numeric values
  if your mouse cursor hovers over a field while scrolling.
  (I made them narrower so that's easier to avoid now)


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


Contact: Atm only on the [TTS Reader's github page](https://github.com/rar0n/rarons-TTS-Reader)


## Version history
(Removed unnecessary Dev procrastination versions)

- 2026.07.13 rarons TTS Reader v0.63
  - Lets call it initial release, though I have earlier beta versions here (that nobody dl'd). My versioning nr. is kinda arbitrary.
