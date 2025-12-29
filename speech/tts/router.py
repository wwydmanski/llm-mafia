import io
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import service


class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None


router = APIRouter(prefix="/tts", tags=["tts"])


@router.post("/speak")
async def speak(req: TTSRequest):
    try:
        audio, content_type, meta = await service.synthesize(req.text, req.voice)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return StreamingResponse(io.BytesIO(audio), media_type=content_type, headers={"Content-Disposition": "inline; filename=tts.wav"})
