"""
Thin client for KoboldCpp's own Speech-To-Text endpoint (Whisper).

Enable this in KoboldCpp by loading a whisper*.bin model (Settings ->
Media -> Speech To Text, or --whispermodel on the command line). That
exposes POST /api/extra/transcribe on whatever host/port KoboldCpp is
running on (default http://127.0.0.1:5001).

Note this is the KoboldCpp-native endpoint, not the OpenAI-compatible
/v1/audio/transcriptions one -- it wants raw base64 WAV bytes rather than
a multipart file upload. Good enough for single short WAV snippets; long
files will need client-side chunking before this can be used on them,
since whisper.cpp only ever looks at ~30s of audio per internal window
regardless of which endpoint is used.
"""

import base64
from typing import Optional

import requests


class STTError(Exception):
    pass


class KoboldSTTClient:
    def __init__(self, base_url: str = "http://127.0.0.1:5001",
                 timeout: int = 120, langcode: str = "en",
                 suppress_non_speech: bool = False):
        self.base_url = base_url.rstrip("/")
        # Whisper on a slow/CPU backend can take a while even for short
        # clips -- default timeout is generous compared to tts_client's,
        # deliberately.
        self.timeout = timeout
        self.langcode = langcode or "en"
        self.suppress_non_speech = suppress_non_speech

    def transcribe(self, wav_bytes: bytes, prompt: str = "",
                    langcode: Optional[str] = None,
                    suppress_non_speech: Optional[bool] = None) -> str:
        """Sends raw WAV bytes to KoboldCpp and returns the transcribed
        text. `langcode`/`suppress_non_speech` override the constructor
        defaults for this call only, if given.

        `prompt` is passed through to KoboldCpp/whisper.cpp as-is -- an
        optional short piece of context text that can steer transcription
        (e.g. expected vocabulary), same idea as Whisper's initial_prompt.
        Leave empty if unused."""
        if not wav_bytes:
            raise STTError("Empty audio data passed to transcribe()")

        url = f"{self.base_url}/api/extra/transcribe"

        effective_langcode = self.langcode if langcode is None else langcode
        effective_suppress = (
            self.suppress_non_speech if suppress_non_speech is None else suppress_non_speech
        )

        payload = {
            "prompt": prompt or "",
            "suppress_non_speech": bool(effective_suppress),
            "langcode": effective_langcode,
            "audio_data": base64.b64encode(wav_bytes).decode("ascii"),
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise STTError(f"Request to {url} failed: {e}") from e

        try:
            data = resp.json()
        except ValueError as e:
            raise STTError(f"Unexpected response from {url}: {e}") from e

        text = data.get("text")
        if text is None:
            raise STTError(f"Unexpected response from {url}: no 'text' key in {data!r}")
        return text
