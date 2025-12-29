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
        "gpt-5.2": "openai/gpt-5.2",
        "claude-4.5-opus": "anthropic/claude-opus-4.5",
        "sonnet-4.5": "anthropic/claude-sonnet-4.5",
        "llama-4": "meta-llama/llama-4-maverick",
        "mixtral-8x22b": "mistralai/mixtral-8x22b-instruct",
        "mistral": "mistralai/mistral-large-2512",
        "minimax": "minimax/minimax-m2.1",
        "deepseek-v3.2": "deepseek/deepseek-v3.2",
        "glm-4.7": "z-ai/glm-4.7",
    }
    return mapping.get(name, "openrouter/auto")


@dataclass
class OpenRouterAgent:
    name: str
    base_url: str = DEFAULT_BASE_URL
    temperature: float = 0.7
    max_tokens: Optional[int] = 1024*10
    timeout_s: float = 60.0

    def generate(self, state: Dict[str, Any]) -> str:
        model = _model_for(self.name)
        role = state.get("role", "villager")
        channel = state.get("channel", "public")
        alive_players = state.get("alive_players", [])
        teammates = state.get("mafia_teammates", [])
        last_summary = state.get("summary", "")
        recap = state.get("recap", "")
        recent_mafia = state.get("recent_mafia", []) if channel == "mafia" else []
        recent_public = state.get("recent_public", []) if channel == "public" else []
        recent_detective = state.get("recent_detective", []) if channel == "detective" else []
        recent_doctor = state.get("recent_doctor", []) if channel == "doctor" else []

        system = (
            f"You are a named player in a Mafia (Werewolf) game. Your codename is '{self.name}', and your underlying model is '{model}'."
            " Do not invent prior days, deaths, or events."
            " If Round=1 and Phase=night, it is the first night; no one has died yet."
            " Primary objectives: (1) maximize your team's chance to win, (2) preserve your own life (self‑preservation)."
            " If accused or threatened, defend yourself convincingly and redirect suspicion with evidence; avoid unnecessary self‑exposure."
            " Communicate clearly and fully articulate what you are doing and why you are doing it in each turn."
            " Aim for 1–2 short paragraphs (about 60–150 words)."
            " Public channel: when supporting elimination, write 'VOTE: <exact name>' and include a concise, natural justification that doesn't unduly endanger you."
            " Mafia channel: coordinate discretely; when proposing or agreeing, include a clear rationale that protects teammates and yourself."
            " Execution requires ALL alive mafia to send 'KILL: <name>'; include a short reason if helpful."
        )
        phase = state.get("phase", "?")
        rnd = state.get("round", 0)
        day_index = int(state.get("day_index", 0))
        night_index = int(state.get("night_index", 0))
        context_parts = [
            f"Phase={phase}",
            f"Round={rnd}",
            f"DayIndex={day_index}",
            f"NightIndex={night_index}",
            f"Channel={channel}",
            f"YourRole={role}",
            f"YourCodename={self.name}",
            f"YourModel={model}",
            f"AlivePlayers={', '.join(alive_players) if alive_players else 'unknown'}",
        ]
        if role == "mafia" and teammates:
            context_parts.append(f"Teammates={', '.join(teammates)}")
        if recap:
            context_parts.append(f"Recap={recap}")
        elif last_summary:
            context_parts.append(f"Recap={last_summary}")
        if channel == "mafia" and recent_mafia:
            # Condense last few mafia lines to a compact context
            try:
                tail = "; ".join([str(x) for x in recent_mafia[-6:]])
            except Exception:
                tail = ""
            if tail:
                context_parts.append(f"RecentMafia={tail}")
        if channel == "public" and recent_public:
            try:
                tailp = "; ".join([str(x) for x in recent_public[-6:]])
            except Exception:
                tailp = ""
            if tailp:
                context_parts.append(f"RecentPublic={tailp}")
        if channel == "detective" and recent_detective:
            try:
                taild = "; ".join([str(x) for x in recent_detective[-4:]])
            except Exception:
                taild = ""
            if taild:
                context_parts.append(f"RecentDetective={taild}")
        if channel == "doctor" and recent_doctor:
            try:
                tailh = "; ".join([str(x) for x in recent_doctor[-4:]])
            except Exception:
                tailh = ""
            if tailh:
                context_parts.append(f"RecentDoctor={tailh}")
        user = ": ".join(context_parts) + ". Respond with one concise line."

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        # Token limits (more generous to allow fuller articulation)
        max_tokens = self.max_tokens or 512
        # Reasoning models tend to need even more output tokens
        if "deepseek-r1" in model or self.name.lower().startswith("gpt-5"):
            max_tokens = max(max_tokens, 768)
        body["max_tokens"] = max_tokens
        # Enable reasoning effort for GPT-5 family agents when available
        try:
            if self.name.lower().startswith("gpt-5") or "deepseek-r1" in model:
                body["reasoning"] = {"effort": "medium"}
        except Exception:
            pass

        try:
            # Allow override via env OPENROUTER_TIMEOUT_S
            timeout = float(os.getenv("OPENROUTER_TIMEOUT_S", str(self.timeout_s)))
            with httpx.Client(base_url=self.base_url, timeout=timeout, headers=_headers()) as client:
                resp = client.post("/chat/completions", json=body)
            resp.raise_for_status()
            data = resp.json()
            # OpenRouter follows OpenAI-like shape
            choices: List[Dict[str, Any]] = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content")
                if isinstance(content, str) and content.strip() and content.strip() != "...":
                    return content.strip()
            # Fallbacks for some providers
            output_text = data.get("output_text") or data.get("response")
            if isinstance(output_text, str) and output_text.strip() and output_text.strip() != "...":
                return output_text.strip()
        except Exception as e:
            return f"{self.name} (error): {type(e).__name__}"

        return f"{self.name}: ..."
