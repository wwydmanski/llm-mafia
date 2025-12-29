import asyncio
import contextlib
import os
import json
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agents.base import get_agents
from app.routers.settings import get_agent_timeout

router = APIRouter(prefix="/ws", tags=["game"])


async def _call_agent(agent, state, timeout: float) -> str:
    try:
        # Run sync .generate in a thread; enforce timeout
        return await asyncio.wait_for(asyncio.to_thread(agent.generate, state), timeout=timeout)
    except asyncio.TimeoutError:
        return f"{agent.name} timed out"
    except Exception as e:  # noqa: BLE001
        return f"{agent.name} error: {type(e).__name__}"


async def send_cycle(websocket: WebSocket, rounds: int = 3) -> None:
    # Build phases: day/night repeated for the requested number of rounds
    phases: list[str] = []
    for _ in range(max(1, rounds)):
        phases += ["day", "night"]

    agents = get_agents()  # dynamic list (now 9 agents)
    agent_timeout = get_agent_timeout()

    for i, phase in enumerate(phases, start=1):
        await websocket.send_json({
            "type": "phase",
            "name": phase,
            "index": i,
            "ts": datetime.utcnow().isoformat(),
            "agents": [a.name for a in agents],
        })

        # Sequentially collect agent messages for this phase
        for agent in agents:
            msg = await _call_agent(agent, {"phase": phase, "round": i}, agent_timeout)
            await websocket.send_json({"type": "agent_message", "agent": agent.name, "text": msg})
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.15)
    await websocket.send_json({"type": "done"})


@router.websocket("/game")
async def game_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    bg_task: asyncio.Task | None = None
    try:
        await websocket.send_json({"type": "hello", "message": "connected"})
        while True:
            data = await websocket.receive_text()
            try:
                payload: Dict[str, Any] = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "invalid json"})
                continue

            kind = payload.get("type")
            if kind == "ping":
                await websocket.send_json({"type": "pong"})
            elif kind == "start":
                if bg_task and not bg_task.done():
                    await websocket.send_json({"type": "info", "message": "already running"})
                else:
                    rounds = int(payload.get("rounds", 3))
                    bg_task = asyncio.create_task(send_cycle(websocket, rounds=rounds))
                    await websocket.send_json({"type": "started"})
            else:
                await websocket.send_json({"type": "error", "message": "unknown command"})
    except WebSocketDisconnect:
        if bg_task and not bg_task.done():
            bg_task.cancel()
            with contextlib.suppress(Exception):
                await bg_task
