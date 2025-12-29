from fastapi import APIRouter, UploadFile, File, HTTPException
from . import service


router = APIRouter(prefix="/asr", tags=["asr"])


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...)) -> dict:
    try:
        text, meta = await service.transcribe_file(file)
        return {"filename": file.filename, "text": text, "meta": meta}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
