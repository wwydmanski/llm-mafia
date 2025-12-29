from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


DEFAULT_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


def _headers() -> Dict[str, str]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # Optional routing headers (documented by OpenRouter)
    site_url = os.getenv("OPENROUTER_SITE_URL")
    app_name = os.getenv("OPENROUTER_APP_NAME")
    if site_url:
        headers["HTTP-Referer"] = site_url
    if app_name:
        headers["X-Title"] = app_name
    return headers


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()


def _env_model_map() -> Dict[str, str]:
    raw = os.getenv("AGENT_MODEL_MAP", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _model_for(name: str) -> str:
    # Priority: AGENT_MODEL_MAP JSON > AGENT_MODEL_<NAME> > defaults
    override_map = _env_model_map()
    if name in override_map:
        return override_map[name]

    env_key = f"AGENT_MODEL_{_sanitize(name)}"
    if os.getenv(env_key):
        return os.getenv(env_key) or "openrouter/auto"

    mapping = {
        # Map our seven agent labels to OpenRouter model slugs
        "gpt-5.2": "openrouter/auto",
        "claude-4.5-opus": "anthropic/claude-3.5-sonnet",
        "sonnet-4.5": "anthropic/claude-3.5-sonnet",
        "llama-405b": "meta-llama/llama-3.1-405b-instruct",
        "mixtral-8x22b": "mistralai/mixtral-8x22b-instruct",
        "gemini-2.0": "google/gemini-1.5-pro",
        "deepseek-r1": "deepseek/deepseek-chat",
        "deepseek-v3.2": "deepseek/deepseek-v3.2",
        "glm-4.7": "z-ai/glm-4.7",
    }
    return mapping.get(name, "openrouter/auto")


@dataclass
class OpenRouterAgent:
    name: str
    base_url: str = DEFAULT_BASE_URL
    temperature: float = 0.7
    max_tokens: Optional[int] = 256
    timeout_s: float = 20.0

    def generate(self, state: Dict[str, Any]) -> str:
        model = _model_for(self.name)
        system = (
            "You are a player in a Mafia game. Be concise, persuasive, "
            "and vary your style lightly each turn. Avoid revealing hidden roles."
        )
        phase = state.get("phase", "?")
        rnd = state.get("round", 0)
        user = f"Phase: {phase}, Round: {rnd}. Provide one short sentence."

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens

        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout_s, headers=_headers()) as client:
                resp = client.post("/chat/completions", json=body)
            resp.raise_for_status()
            data = resp.json()
            # OpenRouter follows OpenAI-like shape
            choices: List[Dict[str, Any]] = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        except Exception as e:
            return f"{self.name} (error): {type(e).__name__}"

        return f"{self.name}: ..."
