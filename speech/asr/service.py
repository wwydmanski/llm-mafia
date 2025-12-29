from __future__ import annotations

import os
from typing import Tuple

from fastapi import UploadFile
import httpx


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


async def transcribe_file(file: UploadFile) -> Tuple[str, dict]:
    provider = _env("ASR_PROVIDER", "stub")
    if provider == "openai" and _env("OPENAI_API_KEY"):
        return await _openai_transcribe(file)
    # Fallback stub
    content = await file.read()
    meta = {"bytes": len(content), "filename": file.filename}
    return f"transcribed: {file.filename}", meta


async def _openai_transcribe(file: UploadFile) -> Tuple[str, dict]:
    api_key = _env("OPENAI_API_KEY")
    url = "https://api.openai.com/v1/audio/transcriptions"
    model = _env("ASR_MODEL", "whisper-1")
    headers = {"Authorization": f"Bearer {api_key}"}

    # Build multipart form
    form = {
        "model": (None, model),
        "file": (file.filename, await file.read(), file.content_type or "audio/wav"),
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(url, headers=headers, files=form)
        r.raise_for_status()
        data = r.json()
        text = data.get("text") or data.get("text", "")
        return text or "", {"provider": "openai", "model": model}

