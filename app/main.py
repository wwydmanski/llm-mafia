from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv

from app.routers import health
from app.routers import game
from app.routers import agents as agents_router
from app.routers import settings as settings_router
from speech.asr.router import router as asr_router
from speech.tts.router import router as tts_router


def create_app() -> FastAPI:
    load_dotenv(override=False)
    app = FastAPI(title="Mafia AI", version="0.1.0")

    origins = os.getenv("ALLOWED_ORIGINS", "*")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins.split(",") if origins != "*" else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(game.router)
    app.include_router(agents_router.router)
    app.include_router(settings_router.router)
    app.include_router(asr_router)
    app.include_router(tts_router)

    return app


app = create_app()
