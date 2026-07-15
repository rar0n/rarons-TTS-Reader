"""
Thin client for KoboldCpp's OpenAI-compatible TTS endpoint.

Enable this in KoboldCpp via Settings -> Media -> Text To Speech ->
"OpenAI-Compat. API Server". That exposes POST /v1/audio/speech on
whatever host/port KoboldCpp is running on (default http://127.0.0.1:5001).
"""

from typing import List, Optional

import requests


class TTSError(Exception):
    pass


class KoboldTTSClient:
    def __init__(self, base_url: str = "http://127.0.0.1:5001",
                 voice: str = "default", timeout: int = 60,
                 seed: Optional[int] = None, instruction: str = "",
                 model: str = "kcpp"):
        self.base_url = base_url.rstrip("/")
        self.voice = voice
        self.timeout = timeout
        # Sent as the "model" field of the /v1/audio/speech payload.
        # KoboldCpp's OpenAI-compat endpoint doesn't actually route on
        # this, but some other OpenAI-compatible TTS backends do -- left
        # configurable (Settings tab) rather than hardcoded so those work too.
        self.model = model or "kcpp"
        # When set, every synthesize() call sends this exact seed, which
        # pins the voice so it no longer drifts between chunks. When left
        # None, KoboldCpp picks its own (effectively random) seed each call.
        # Also, afaik one can't retrieve KoboldCpp's own seed value via API.
        self.seed = seed
        # Free-text instruction sent alongside the voice on every
        # synthesize() call. Left empty by default (matching the previous
        # hardcoded behaviour); when non-empty, KoboldCpp treats it as an
        # override that can affect/replace the specific voice selected.
        self.instruction = instruction or ""

    def synthesize(self, text: str, instruction: Optional[str] = None) -> bytes:
        """Send `text` to KoboldCpp and return raw WAV audio bytes.

        `instruction` overrides self.instruction for this call only, if
        given; otherwise self.instruction (set at construction time) is
        used."""
        if not text.strip():
            raise TTSError("Empty text passed to synthesize()")

        url = f"{self.base_url}/v1/audio/speech"
        #url = f"{self.base_url}/api/extra/tts" # test. KoboldCpp specific API alt.

        effective_instruction = self.instruction if instruction is None else instruction

        payload = {
            "model": self.model,
            "input": text,
            "voice": self.voice,
            "response_format": "wav",
            "instruction": effective_instruction, # if non-empty = Random voice, specific voice overridden
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise TTSError(f"Request to {url} failed: {e}") from e

        if not resp.content:
            raise TTSError("KoboldCpp returned an empty response")
        return resp.content

    def list_speakers(self) -> List[str]:
        """Query KoboldCpp for the available TTS speaker/voice names,
        including any custom clones it has loaded (GET /api/extra/speakers_list)."""
        url = f"{self.base_url}/api/extra/speakers_list"
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise TTSError(f"Request to {url} failed: {e}") from e

        try:
            # print(resp.text)
            data = resp.json()
        except ValueError as e:
            raise TTSError(f"Unexpected response from {url}: {e}") from e

        if isinstance(data, dict):
            results = data.get("results")
            if results is None:
                raise TTSError(f"Unexpected response from {url}: no 'results' key in {data!r}")
        elif isinstance(data, list):
            # Some KoboldCpp builds return the bare list instead of {"results": [...]}
            results = data
        else:
            raise TTSError(f"Unexpected response from {url}: {data!r}")

        return list(results)

    def get_perf(self) -> dict:
        """Query KoboldCpp's recent performance info (GET /api/extra/perf).
        Includes `last_seed`, the seed actually used for the most recent
        generation -- useful for discovering what seed KoboldCpp picked
        on its own when none was sent explicitly."""
        url = f"{self.base_url}/api/extra/perf"
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise TTSError(f"Request to {url} failed: {e}") from e

        try:
            return resp.json()
        except ValueError as e:
            raise TTSError(f"Unexpected response from {url}: {e}") from e
