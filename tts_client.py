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
                 seed: Optional[int] = None):
        self.base_url = base_url.rstrip("/")
        self.voice = voice
        self.timeout = timeout
        # When set, every synthesize() call sends this exact seed, which
        # pins the voice so it no longer drifts between chunks. When left
        # None, KoboldCpp picks its own (effectively random) seed each call.
        self.seed = seed

    def synthesize(self, text: str) -> bytes:
        """Send `text` to KoboldCpp and return raw WAV audio bytes."""
        if not text.strip():
            raise TTSError("Empty text passed to synthesize()")

        url = f"{self.base_url}/v1/audio/speech"
        payload = {
            "input": text,
            "voice": self.voice,
            "response_format": "wav",
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
