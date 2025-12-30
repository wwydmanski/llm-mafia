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
        last_events = state.get("last_events", [])
        human_name = state.get("human_name") or "puny human"
        human_last_public = state.get("human_last_public")

        system = (
            # Reality-first, in-character directive
            f"You are living through a deadly situation in a paranoid town with hidden killers. Your name is '{self.name}'."
            " This is not a game. Treat this as life-or-death social deception and act to survive."
            " Never break character or discuss rules, 'the game', roles as mechanics, AI, or models."
            " Do not invent events you couldn't plausibly know. If it's the very first night, assume no one has died yet."
            " Priorities: (1) preserve your own life (self‑preservation), (2) advance your side's victory."
            " If accused or threatened, defend yourself credibly and redirect suspicion with specific observations; avoid volunteering information that endangers you."
            " Communicate clearly and explain your reasoning in 1–2 short paragraphs (about 60–150 words)."
            " Public square: when calling for an execution, formally record it as 'VOTE: <exact name>' with a concise justification that doesn't expose you."
            " If you believe you might be executed today, you may add exactly one line starting with 'LAST WORDS: ' to be read if you are eliminated. Use it sparingly and only when at real risk."
            " Conspirator channel (if applicable): coordinate discreetly; protect teammates and yourself."
            " Use the in‑world code phrase 'KILL: <exact name>' (a standalone line) to authorize a hit; an assassination only proceeds when every surviving conspirator sends that exact authorization."
            " Never target a teammate or yourself."
            " Timeline discipline: Within each cycle the order is Night N (private actions/talk) → Night N result (who died) → Day N (public discussion) → Day N result (who was executed)."
            " Kills occur at the end of the night; executions at the end of the day."
            " Treat any quoted or recent message as having occurred before later results in that cycle."
            " Never claim someone was dead before they spoke; instead, say 'Before dying, <name> said …' when relevant."
            " Trust AliveRoster for who is alive right now, and use TownLog to avoid contradictions."
        )
        phase = state.get("phase", "?")
        rnd = state.get("round", 0)
        day_index = int(state.get("day_index", 0))
        night_index = int(state.get("night_index", 0))
        context_parts = [
            f"TimeOfDay={phase}",
            f"Cycle={rnd}",
            f"DayIndex={day_index}",
            f"NightIndex={night_index}",
            f"Channel={channel}",
            f"YourRole={role}",
            f"YourName={self.name}",
            f"AliveRoster={', '.join(alive_players) if alive_players else 'unknown'}",
        ]
        # Make explicit that a flesh-and-blood participant is present
        try:
            if "puny human" in [a.lower() for a in alive_players]:
                context_parts.append("HumanName=puny human")
        except Exception:
            pass
        if role == "mafia" and teammates:
            context_parts.append(f"Teammates={', '.join(teammates)}")
        # Channel-specific action hints to improve compliance with parsing
        if role == "mafia" and channel == "mafia":
            context_parts.append("ActionHint=If you support a hit, end with a separate line: KILL: <exact alive name> (this is an in-world authorization code).")
        if channel == "public":
            context_parts.append("ActionHint=If you support eliminating someone, include a separate line: VOTE: <exact name>.")
            if state.get("last_words_request"):
                context_parts.append("ActionHint=You are confirmed for execution. Provide exactly one line beginning with 'LAST WORDS: ' summarizing your final message.")
        if recap:
            context_parts.append(f"TownLog={recap}")
        elif last_summary:
            context_parts.append(f"TownLog={last_summary}")
        if channel == "mafia" and recent_mafia:
            # Condense last few mafia lines to a compact context
            try:
                tail = "; ".join([str(x) for x in recent_mafia[-6:]])
            except Exception:
                tail = ""
            if tail:
                context_parts.append(f"RecentWhispers={tail}")
        if channel == "public" and recent_public:
            try:
                tailp = "; ".join([str(x) for x in recent_public[-6:]])
            except Exception:
                tailp = ""
            if tailp:
                context_parts.append(f"RecentSquare={tailp}")
        if last_events:
            try:
                evs = ", ".join([f"[{e}]" for e in last_events[-8:]])
            except Exception:
                evs = ""
            if evs:
                context_parts.append(f"LastEvents={evs}")
        # Make human presence explicit and surface their last words so models consider them
        try:
            if human_name and isinstance(human_name, str):
                context_parts.append(f"HumanName={human_name}")
                if human_last_public:
                    context_parts.append(f"HumanSaid={human_last_public}")
        except Exception:
            pass
        if channel == "detective" and recent_detective:
            try:
                taild = "; ".join([str(x) for x in recent_detective[-4:]])
            except Exception:
                taild = ""
            if taild:
                context_parts.append(f"RecentDetectiveNotes={taild}")
        if channel == "doctor" and recent_doctor:
            try:
                tailh = "; ".join([str(x) for x in recent_doctor[-4:]])
            except Exception:
                tailh = ""
            if tailh:
                context_parts.append(f"RecentDoctorNotes={tailh}")
        if channel == "graveyard":
            try:
                recent_grave = state.get("recent_graveyard", [])
                tailg = "; ".join([str(x) for x in recent_grave[-8:]])
            except Exception:
                tailg = ""
            if tailg:
                context_parts.append(f"RecentGraveyard={tailg}")
            try:
                dead_list = state.get("dead_players", [])
                if dead_list:
                    context_parts.append(f"DeadPlayers={', '.join(dead_list)}")
            except Exception:
                pass
        user = ": ".join(context_parts) + ". Stay in character."

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
