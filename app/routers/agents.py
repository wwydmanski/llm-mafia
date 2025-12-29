import os
from typing import Any, Dict, List

from fastapi import APIRouter

from agents.base import get_agents

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/models")
def list_models() -> Dict[str, Any]:
    # Determine if OpenRouter is active
    has_or = bool(os.getenv("OPENROUTER_API_KEY"))
    agent_objs = get_agents()
    names: List[str] = [a.name for a in agent_objs]

    # Resolve OpenRouter model mapping even if key is absent (to preview overrides)
    try:
        from agents.providers.openrouter import _model_for as resolve_model
    except Exception:
        def resolve_model(n: str) -> str:  # type: ignore
            return "openrouter/auto"

    agents = []
    for name in names:
        agents.append({
            "name": name,
            "provider": "openrouter" if has_or else "stub",
            "model": resolve_model(name) if has_or else "echo",
            "openrouter_model": resolve_model(name),
        })

    return {"openrouter_active": has_or, "agents": agents}

