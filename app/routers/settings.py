from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


router = APIRouter(prefix="/settings", tags=["settings"])


class Settings(BaseModel):
    agent_timeout_s: float = Field(default_factory=lambda: float(os.getenv("AGENT_TIMEOUT_S", "120")))


_settings: Settings = Settings()


def get_agent_timeout() -> float:
    return _settings.agent_timeout_s


@router.get("")
def read_settings() -> Settings:
    return _settings


class UpdateSettings(BaseModel):
    agent_timeout_s: Optional[float] = None


@router.post("")
def write_settings(update: UpdateSettings) -> Settings:
    if update.agent_timeout_s is not None:
        if update.agent_timeout_s <= 0:
            raise HTTPException(status_code=400, detail="agent_timeout_s must be > 0")
        _settings.agent_timeout_s = float(update.agent_timeout_s)
    return _settings
