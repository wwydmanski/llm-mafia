## Mafia AI – FastAPI Game
Inspired by https://www.youtube.com/watch?v=JhBtg-lyKdo
A web-based Mafia/Werewolf game played by one human against multiple AI models via OpenRouter. Starts at night with mafia coordination, then cycles through day discussion and voting. No Docker; fast local dev, manual testing.

### Quick Start

- Requirements: Python 3.11+, Node not required (static HTML served by Python).
- Install deps and run API:
  - `make setup`
  - `make dev-api` (serves FastAPI on http://localhost:8000)
- Run the web UI:
  - `make dev-web` (serves static UI on http://localhost:5173)
- Open the UI and click Start (top-right, choose rounds).

Without Make:
- `python -m venv .venv && source .venv/bin/activate`
- `pip install -r requirements.txt`
- `uvicorn app.main:app --reload`
- `cd web && python3 -m http.server 5173`

### Configuration

Copy `.env.example` to `.env` and set as needed:
- `OPENROUTER_API_KEY` — enable real AI agents (OpenRouter).
- Optional speech: `OPENAI_API_KEY`, `ASR_PROVIDER=openai`, `TTS_PROVIDER=openai` (falls back to stubs if unset).
- Optional tuning: `AGENT_TIMEOUT_S`, `OPENROUTER_TIMEOUT_S`, `DAY_TURN_TIMEOUT_S`, `NIGHT_TURN_TIMEOUT_S`.

### Gameplay

- Night (first):
  - Mafia chat for 30s, interleaved into the main timeline; unanimous `KILL <name>` ends night early.
  - Doctor chooses `PROTECT <name>`; Detective `INSPECT <name>` (private); saves cancel kills.
- Day (180s):
  - Free-form, models speak when they have something new; explicit votes only:
    - `VOTE <name>` or `lynch <name>` (mentions don’t count).
  - Majority ends the day early; otherwise plurality lynch (ties = no lynch).
- Dead don’t talk. Legend and Graveyard appear above the timeline.

### Model Behavior

- Agents are told their codename/model and receive a recap each turn (recent events, alive/dead) to reduce confusion.
- Prompts encourage clear, fully articulated reasoning (1–2 short paragraphs), explicit VOTE/KILL with concise rationale, and self‑preservation.
- Reasoning-friendly settings for GPT‑5 and DeepSeek R1 (extra tokens, reasoning hints).

### Notes

- Manual testing only; no Docker required.
- If agents time out, raise `AGENT_TIMEOUT_S` (UI header shows status) or `OPENROUTER_TIMEOUT_S`.
- Private detective/doctor results are not leaked publicly.
