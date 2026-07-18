<img width="820" height="648" alt="image" src="https://github.com/user-attachments/assets/095cc778-a4c8-4ba6-a00b-dcce3db714cf" />

<img width="820" height="648" alt="image" src="https://github.com/user-attachments/assets/33a0150b-a48a-4c60-891b-77f6a0a8d6fe" />


# rarons TTS Reader - Read long-form text aloud (KoboldCpp API)

- Built to work around limited context memory for KoboldCpp
    with long-form text generation and audio / video transcription.
- Now also with STT (Speech To Text) long-form transcription
    (TTS Reader is a bit of a misnomer now).

A small Python wrapper that reads pasted text aloud through KoboldCpp's
TTS API, with live highlighting, better handling of (some/most?)
punctuations and numbers.

Can now also do audio or video transcripts using Whisper in KoboldCpp.
Can save rendered audio and subtitles (for use in a player later).


### Features

Basically
  - **Long text TTS**  - Text to Speech (Narration)
  - **Long media STT** - Speech to Text (Transcription)
  - **Save subtitles** both for TTS and STT.

For TTS:
  - **Long text TTS** via KoboldCpp TTS (Only tested 45ish minute so far)
  - Live highligthing (Per TTS chunk) while speaking and rendering TTS from KoboldCpp.
  - **Save audio** as wav or mp3 (when it's finished rendering the TTS)
  - Better TTS handling of punctuations and numbers (----, 100,000, 200,000, 3.1415 etc).
  - Highlight margins (visible non-highlighted text around highlighted)
  - TTS seed value management (Rudimentary. Can reuse seeds)
  - Extra Pause settings (Maybe not so useful).
  - Keyboard controls in addition to GUI buttons:
    - Ctrl + Enter = Play / Speak. When speaking:
      - Space = Pause / resume
      - Arrow left / right = Rewind / Forward
      - ESC = Stop, return to textbox.

For STT:
  - **Long audio transcriptions** Also some video formats (tested 3 hour video).
  - **make subtitles**
  - Save as plain text too
    - Some rudimentary plain text formatting (newlines, basically)
  - Some settings are experimental and maybe not so useful.


### Vibe coded

- Kind of an experimental vibe coding project.
- Not all settings are necessarily that useful btw. Experiment!

Yes. Full disclosure: I don't know Python that well. Claude does though :-)
As I found KoboldCpp TTS flunked out on longer-form text  I vibe-coded this.
(due to limited context memory as far as I understand it - KoboldCpp is still
**awesome**!),
I started usingClaude Sonnet 5 on Extra / High  and Medium effort, over now
quite a few free sessions (Also awesome, so thanks to Anthropic for that!).

Note: Most of the source code comments is Claude's. Some (not all) of the
      reasoning behind _why_ something is done is Claude's assumption and
      a bit off. But a lot is on-point as well!


    2026 Ragnar Aronsen (raron) ( But mostly Claude :) )


## Links

[KoboldCpp](https://github.com/LostRuins/koboldcpp)

[TTS models for narration](https://huggingface.co/koboldcpp/tts/tree/main) (as also linked from KoboldCpp's page above)


For TTS (I've so far only tried, and use):
 - [Qwen3-TTS-12Hz-1.7B-Base-q8_0.gguf](https://huggingface.co/koboldcpp/tts/blob/main/Qwen3-TTS-12Hz-1.7B-Base-q8_0.gguf) (Can also do voice cloning)
 - [qwen3-tts-tokenizer-q8_0.gguf](https://huggingface.co/koboldcpp/tts/blob/main/qwen3-tts-tokenizer-q8_0.gguf)
Other TTS models should work as well.

For STT
 - [whisper-small-q5_1.bin](https://huggingface.co/koboldcpp/whisper/tree/main)

 As also linked from the amazing [KoboldCpp's Wiki page](https://github.com/LostRuins/koboldcpp/wiki#what-models-does-koboldcpp-support-what-architectures-are-supported), along with other models.



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


## Notes

Details, details...


### TTS (Narration) tab

Main tab for TTS
- After a complete playthrough, if you want another version (even with the
  same voice and text), click RND to make a new random seed value before Play.
- If you stop before rendering is finished, clicking Play again makes a new seed value
- AFAIK, the "seed value" feature relies on an undocumented feature of KoboldCpp v1.116
  API. No guarantee it'll work in later versions of KoboldCpp.
  - Still works on KoboldCpp v.1.117
  - Range seems to be from 0 to 2^31-1, or 0 to 2147483647.
- If you happen upon a seed value you'd like to keep, click "Store seed".
  - It will be stored in the "Seed Vault" tab, along with voice and instructions.
    - You can also make a note there, for your own reference.
- If the "Lock" checkbox is checked, seed value can't change.
- If deleting the voice field (or set as "Default"), KoboldCpp will just
  pick a random speaker for each sentence. Might not be what you want.
- Same-ish if you enter something in the "instructions" field:
  - It overrides Voice setting (Not really to be used with QwenTTS base
    afaik).


#### Notes

- Save as mp3 may take a bit of time (depending on length etc.).
  But shouldn't take more than a few seconds, depending on size, system specs etc.

- Saving a subtitle requires clicking the Save button, and selecting "SubRip subtitles" as file type (.srt).
  Thus you have to click "Save" twice to also save the audio. This can only be done after the audio has finished rendering.


#### Keyboard / mouse controls:

In addition to buttons, you can use keyboard for some things (when main text area is focused):

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


### Seed Vault tab (for TTS)

Just a simple table of stored seed values from the Narration tab.

After editing a note or instruction, click "Save Table".
- It's stored in the same file as the settings (settings.json).
- If you mess up and want to revert to the last saved settings, go to
  Settings tab and reload settings (don't click "Save Table" first then...).



### TTS settings tab

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

- Highlight Margin (checkbox) (Should be named "Auto scroll with margin")
  - When the highlight reaches the bottom minus a distance, it scrolls up to top minus the same distance.
- Clamp Highlight Distance (Should be named "Don't auto scroll").
  - Checked   : Keeps the highlight at a constant distance instead of autoscrolling.
  - Unchecked : Auto-scrolls the highlight to the opposite edge (top / bottom).
- Scroll Denominator
  - Sets the distance from top / bottom of text window, as a ratio (1/SD) of textbox height.
    - Ex. SD = 4 means 1/4 of screen height. When the highlight reaches 1/4
      of the textbox height from its top or bottom edge, it will scroll the
      highlight to the other edge, with the same 1/4 distance.


#### Pauses (milliseconds)

Extra pauses at the end of each TTS sentence chunk (depending on punctuation).
Actually, you might want most of these to 0 (zero). Though sometimes it seems useful to keep a few.
Experiment!

- Speech might be a bit slow with default pauses, as all pauses will
  be **additional** to pauses that the TTS engine (KoboldCpp) makes, but
  only at each chunk's (usually a sentence) end.
- Mid-chunk only the TTS engine determines how it's spoken, pauses and all.
- Unless a chunk is deemed too short to be by itself, any punctuation should
  only be at the end of a chunk sent to KoboldCpp.
  (Btw this is also a rule you might tune, see Chunk sizing below).


#### Chunk sizing

(This will also affect how short / long stretches of text are in subtitles, if saving as subtitles)

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


### STT (Transcription) tab

For Speech-To-Text transcriptions from audio or video files.

Can be saved as both plain text, and subtitles (.srt).

During transcription, there's also a running min/average/max WPM metric.
Clicking the button to its right sends the average WPM to the Speaker Presets table, along with other settings in effect.
Mostly just an experimental feature, to approximately gauge the WPM.
It's only use is to calculate approximate speech duration if the "Subtitle start adjustment" is on, to determine roughly if a chunk has less spoken words than there's time for.


### STT Settings tab


#### Transcription options

- Language setting (I'm not sure which languages Whipper supports, other than "en" (English).
- Suppress non-speech - whisper attempts to identify and write non-speech audio as "music", "tire screetch", etc.


#### Silence detection

Most important is probably Silence detection (only a simple RMS for now, and I guess that won't change for the time being - it works reasonably well imho).

- Amplitude threshold
- Analysis window
- Minimum gap duration


#### Subtitle segmentation
Split long chunks into multiple SRT entries
- Useful if used for subtitles, so the subtitles don't fill the entire screen.
- Splits the subtitle into segments within a chunk, at approximate time points (so it won't be exact).

Prioritize detected pauses new split points
- You know, this is a bit of a mysterious setting Claude came up with, I'm not entirely sure what it does :-)
  Apparently, it is used to find suitable split points for subtitle segmentation, if enabled, and if there are such gaps "near by".

#### Subtitle lingering
- Just a setting so a subtitle don't disappear at the moment it's spoken, if there's non-speech (silence etc) right after it.


#### Speaker Presets
Just a table of a few related ish settings
- Silence durations for different punctuations (comma, period, paragraph) for plain-text formatting.
- Target audio chunk length, with a plus / minus range.
  - KoboldCpp and Whisper can't transcribe more than 30 seconds, so keep below this absolute limit.
  - For subtitles, maybe a more suitable chunk size is way less. Arbitrarily I've chosen 10 seconds, give or take 5 s.
    - It's probably a bit much, but seems to work reasonably well.
- WPM is an approximately speech speed setting. It's only use is to calculate approximate speech duration if the "Subtitle start adjustment" above is on, to determine roughly if a chunk has less spoken words than there's time for. In which case, it's assumed the speech starts later in the auido chunk (which isn't given, and no way to tell), and it's delayed. Unless it's the last chunk, in which case it just ends earler.




## Known limitations

- Saving as mp3 might take a little while (a few seconds, depending on size),
  during which time it will be unresponsive. Be patient :)
- Unusual punctuation might cause issues (with speech rhytm, highlighting).
   (Though most(?) of this should be fixed now).
- This is justa wrapper for KoboldCpp's TTS and STT API's.
  - Function depends entirely on KoboldCpp and its configuration.
  (Only between chunk pauses, chunk selection, and chunk preparation depends
   on this TTS Reader).
- One issue might be if your system renders speech slower than real-time,
  there will be longer pauses between sentences (TTS chunks sent to KoboldCpp).
  - You can just wait it out and save as wav or mp3 for later listen though.
    (just press Play, then again to Pause, wait, and save it).



## Known Issues

- There's no "continue from selection" function, for now just delete the
  preceding text in the textbox if need be after a full stop.
- (Linux Mint) You probaby will get a few warnings about ALSA underruns.
  (ALSA lib pcm.c:8568:(snd_pcm_recover) underrun occurred). Ignore it :)
- Scrolling in any of the Settings tabs might inadvertedly change numeric
  values if your mouse cursor hovers over a field while scrolling.


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

- 2026.07.15 - v0.70
             - STT added!
