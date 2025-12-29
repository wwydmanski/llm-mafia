from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol, Dict, Any, List


class Agent(Protocol):
    name: str

    def generate(self, state: Dict[str, Any]) -> str:
        ...


@dataclass
class EchoAgent:
    name: str

    def generate(self, state: Dict[str, Any]) -> str:
        phase = state.get("phase", "?")
        rnd = state.get("round", 0)
        return f"[{phase}#{rnd}] {self.name} is thinking…"


def get_agents() -> List[Agent]:
    names = [
        "gpt-5.2",
        "claude-4.5-opus",
        "sonnet-4.5",
        "llama-405b",
        "mixtral-8x22b",
        "gemini-2.0",
        "deepseek-r1",
        "deepseek-v3.2",
        "glm-4.7",
    ]
    if os.getenv("OPENROUTER_API_KEY"):
        from agents.providers.openrouter import OpenRouterAgent

        return [OpenRouterAgent(n) for n in names]
    return [EchoAgent(n) for n in names]
