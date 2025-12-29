from __future__ import annotations

import io
import math
import os
import wave
from typing import Tuple

import httpx


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


async def synthesize(text: str, voice: str | None = None) -> Tuple[bytes, str, dict]:
    provider = _env("TTS_PROVIDER", "stub")
    if provider == "openai" and _env("OPENAI_API_KEY"):
        return await _openai_tts(text, voice)
    if provider == "elevenlabs" and _env("ELEVENLABS_API_KEY") and _env("TTS_ELEVEN_VOICE_ID"):
        return await _elevenlabs_tts(text, voice)
    return _tone_stub(text)


def _tone_stub(text: str) -> Tuple[bytes, str, dict]:
    # Synthesize a short sine tone as placeholder
    seconds = 0.6
    freq = 440.0
    framerate = 16000
    nframes = int(seconds * framerate)
    amp = 0.3
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        for i in range(nframes):
            value = int(amp * 32767 * math.sin(2 * math.pi * freq * (i / framerate)))
            wf.writeframesraw(value.to_bytes(2, byteorder="little", signed=True))
    return buf.getvalue(), "audio/wav", {"provider": "stub"}


async def _openai_tts(text: str, voice: str | None) -> Tuple[bytes, str, dict]:
    api_key = _env("OPENAI_API_KEY")
    model = "gpt-4o-mini-tts"
    voice = voice or _env("TTS_VOICE", "alloy")
    url = "https://api.openai.com/v1/audio/speech"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "input": text, "voice": voice, "format": "wav"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.content, "audio/wav", {"provider": "openai", "model": model, "voice": voice}


async def _elevenlabs_tts(text: str, voice: str | None) -> Tuple[bytes, str, dict]:
    key = _env("ELEVENLABS_API_KEY")
    voice_id = voice or _env("TTS_ELEVEN_VOICE_ID")
    model_id = _env("ELEVEN_MODEL_ID", "eleven_multilingual_v2")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": key,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    payload = {"text": text, "model_id": model_id}
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.content, "audio/mpeg", {"provider": "elevenlabs", "model": model_id, "voice_id": voice_id}
