# Repository Guidelines

## Project Structure & Module Organization
- Backend in `app/` (FastAPI): `routers/`, `services/`, `models/`, `deps/`.
- AI adapters in `agents/` (OpenAI, Anthropic, etc.), personas in `agents/personas/`.
- Speech in `speech/`: `asr/` (transcription) and `tts/` (text-to-voice).
- Frontend in `web/` (browser client, UI, WebSocket logic).
- Tests mirror sources in `tests/` (unit, integration, e2e).
- Config in `config/` and `.env*`; static assets in `assets/`.

Example layout:
```
/ app/       FastAPI app (app/main.py, routers/game.py)
/ agents/    provider adapters + common Agent interface
/ speech/    ASR (Whisper) and TTS engines
/ web/       frontend (e.g., Vite/React)
/ tests/     pytest suites (api, agents, speech)
```

## Build, Test, and Development Commands
- `make setup`: create venv and install deps (no Docker).
- `make dev-api`: run API with reload (`uvicorn app.main:app --reload`).
- `make dev-web`: run the web dev server in `web/`.
- `make lint` / `make fmt`: Ruff/Black; `make type`: mypy (optional but recommended).
- `make clean`: remove caches and temporary audio in `tmp/`.

No make? Use: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && uvicorn app.main:app --reload`.

## Coding Style & Naming Conventions
- Python 3.11+. Indent 4 spaces; Black (88 cols), Ruff, isort; type hints required.
- Files/dirs: `snake_case`; classes: `PascalCase`; functions/vars: `snake_case`; constants: `UPPER_SNAKE`.
- FastAPI endpoints are async; isolate provider logic behind `Agent` interface.

## Manual Testing
- Start API: `uvicorn app.main:app --reload`; verify `GET /health` returns 200.
- Web client: run `make dev-web` (or your web dev command) and connect to the API.
- WebSocket: connect to `/ws/game`; send a “start” event; confirm day/night cycle advances.
- Agents: run with real provider keys or local stubs; confirm seven agents receive prompts and produce replies without timeouts.
- Speech: `curl -F file=@sample.wav http://localhost:8000/asr/transcribe` to check ASR; `POST /tts/speak` with JSON `{ "text": "..." }` to get audio.
- Acceptance: no unhandled exceptions in logs; a full round completes end‑to‑end.

## Commit & Pull Request Guidelines
- Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`).
  - Example: `feat(agents): add Anthropic adapter with streaming`
- PRs: clear description, linked issue, manual test steps performed, and UI/WebSocket screenshots or logs.
- Require at least one review; manual validation over CI (no Docker, no automated tests).

## Security & Configuration Tips
- Never commit secrets. Use `.env`/`.env.local`; set `OPENROUTER_API_KEY` for agents.
- ASR/TTS providers (optional):
  - OpenAI: `ASR_PROVIDER=openai`, `TTS_PROVIDER=openai`, `OPENAI_API_KEY`, optional `TTS_VOICE`.
  - ElevenLabs (TTS): `TTS_PROVIDER=elevenlabs`, `ELEVENLABS_API_KEY`, `TTS_ELEVEN_VOICE_ID`.
- Validate uploads; rate-limit chat routes; enforce CORS; store audio in `tmp/` and clean up.

## Agent-Specific Instructions
- Agents use OpenRouter if `OPENROUTER_API_KEY` is set; otherwise fall back to local echo agents.
- Per-agent model overrides: set `AGENT_MODEL_MAP` (JSON) or `AGENT_MODEL_<NAME>` (e.g., `AGENT_MODEL_CLAUDE_4_5_OPUS=anthropic/claude-3.5-sonnet`).
- Implement/extend provider adapters under `agents/providers/` as needed.
- Keep per-agent conversation state isolated; log to `logs/agents/<name>.jsonl` if you add logging.
- Personas live in `agents/personas/*.yaml` (optional); load at startup and seed deterministically for fairness.
