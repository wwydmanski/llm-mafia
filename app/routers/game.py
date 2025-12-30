import asyncio
import contextlib
import os
import time
import json
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agents.base import get_agents
from app.game.engine import GameEngine
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
        phases += ["night", "day"]

    agents = get_agents()  # dynamic list (now 9 agents)
    engine = GameEngine([a.name for a in agents])
    agent_timeout = get_agent_timeout()

    for i, phase in enumerate(phases, start=1):
        # Announce phase with current alive roster
        await websocket.send_json({
            "type": "phase",
            "name": phase,
            "index": i,
            "ts": datetime.utcnow().isoformat(),
            "agents": engine.alive_players(),
        })

        if phase == "day":
            engine.start_day()
            # Full 3 minutes of town discussion. Agents may speak when they have something new; they can SKIP.
            start_time = time.monotonic()
            end_time = start_time + 180.0
            per_turn_timeout = min(
                agent_timeout,
                float(os.getenv("DAY_TURN_TIMEOUT_S", "30"))
            )
            cooldown = float(os.getenv("DAY_COOLDOWN_S", "3"))
            max_msgs = int(os.getenv("DAY_MAX_MSGS", "2"))
            last_spoke: dict[str, float] = {}
            spoke_count: dict[str, int] = {}
            # initial status
            await websocket.send_json({
                "type": "day_status",
                "remaining_s": max(0.0, end_time - time.monotonic()),
                "votes": {},
                "tally": {},
                "threshold": engine.majority_threshold(),
            })
            while time.monotonic() < end_time:
                alive_speakers = [a for a in agents if (engine.roles.get(a.name) and engine.roles[a.name].alive)]
                if not alive_speakers:
                    break
                for agent in alive_speakers:
                    now = time.monotonic()
                    if now >= end_time:
                        break
                    # Respect cooldown and per-day message limit
                    if now - last_spoke.get(agent.name, 0.0) < cooldown:
                        continue
                    if spoke_count.get(agent.name, 0) >= max_msgs:
                        continue
                    st = engine.build_agent_state(agent.name, channel="public")
                    # Encourage skipping if nothing new
                    st["may_skip"] = True
                    msg = await _call_agent(agent, st, per_turn_timeout)
                    text = (msg or "").strip()
                    # Interpret SKIP/pass as silence
                    low = text.lower()
                    if not text or low in {"skip", "pass", "no comment"} or low.startswith("skip"):
                        last_spoke[agent.name] = now
                        # update status occasionally
                        if int(now) % 1 == 0:
                            await websocket.send_json({
                                "type": "day_status",
                                "remaining_s": max(0.0, end_time - time.monotonic()),
                                "votes": engine.day_votes,
                                "tally": engine.tally_day_votes(),
                                "threshold": engine.majority_threshold(),
                            })
                        await asyncio.sleep(0.02)
                        continue
                    await websocket.send_json({"type": "agent_message", "agent": agent.name, "text": text})
                    engine.log_public(f"{agent.name}: {text}")
                    engine.record_day_vote(agent.name, text)
                    spoke_count[agent.name] = spoke_count.get(agent.name, 0) + 1
                    last_spoke[agent.name] = now
                    # Check for majority to end day early
                    tally = engine.tally_day_votes()
                    threshold = engine.majority_threshold()
                    await websocket.send_json({
                        "type": "day_status",
                        "remaining_s": max(0.0, end_time - time.monotonic()),
                        "votes": engine.day_votes,
                        "tally": tally,
                        "threshold": threshold,
                    })
                    for target, count in tally.items():
                        if count >= threshold:
                            # Early majority reached
                            end_time = 0.0
                            break
                    await asyncio.sleep(0.05)
            engine.end_day()
            # Resolve day lynch (plurality; ties/no votes => no lynch)
            # Prefer majority winner if any, else plurality
            current_tally = engine.tally_day_votes()
            threshold = engine.majority_threshold()
            victim = None
            for target, count in current_tally.items():
                if count >= threshold:
                    victim = target
                    break
            if victim is None:
                victim = engine.compute_day_lynch()
            if victim:
                engine.roles[victim].alive = False
                engine.add_event("day_lynch", victim=victim, day=str(engine.day_index))
            await websocket.send_json({
                "type": "event",
                "name": "day_result",
                "victim": victim,
                "votes": engine.day_votes,
                "tally": current_tally,
                "threshold": threshold,
            })
            # Check win conditions after day resolution
            alive = engine.alive_players()
            mafia_alive = engine.alive_mafia_names()
            town_alive_count = len(alive) - len(mafia_alive)
            winner = None
            reason = None
            if len(mafia_alive) == 0:
                winner, reason = "town", "all_mafia_eliminated"
            elif town_alive_count <= len(mafia_alive):
                winner, reason = "mafia", "mafia_reached_parity"
            if winner:
                await websocket.send_json({
                    "type": "game_over",
                    "winner": winner,
                    "reason": reason,
                    "mafia_alive": mafia_alive,
                    "alive": alive,
                })
                await websocket.send_json({"type": "done"})
                return
        else:
            # Night: mafia private chat window (30s), then a kill
            engine.start_night()
            mafia_agents = [
                a for a in agents
                if (engine.roles.get(a.name) and engine.roles[a.name].alive and engine.roles[a.name].role == "mafia")
            ]
            mafia_names = [a.name for a in mafia_agents]
            # Detective and Doctor (single each)
            det_name = next((n for n,p in engine.roles.items() if p.role=="detective" and p.alive), None)
            doc_name = next((n for n,p in engine.roles.items() if p.role=="doctor" and p.alive), None)
            det_agent = next((a for a in agents if a.name==det_name), None)
            doc_agent = next((a for a in agents if a.name==doc_name), None)
            start_time = time.monotonic()
            end_time = start_time + 30.0
            per_turn_timeout = min(
                agent_timeout,
                float(os.getenv("NIGHT_TURN_TIMEOUT_S", "30"))
            )
            early_victim: str | None = None
            # Send initial mafia status with countdown
            await websocket.send_json({
                "type": "mafia_status",
                "mafia": mafia_names,
                "alive_mafia": engine.alive_mafia_names(),
                "votes": engine.mafia_votes,
                "kills": engine.mafia_kills,
                "remaining_s": max(0.0, end_time - time.monotonic()),
            })
            # Detective and Doctor take one action prompt at night start
            if det_agent is not None:
                st = engine.build_agent_state(det_agent.name, channel="detective")
                msg = await _call_agent(det_agent, st, per_turn_timeout)
                engine.detective_log.append(f"{det_agent.name}: {msg}")
                engine.record_detective(det_agent.name, msg)
                await websocket.send_json({"type": "private_message", "channel": "detective", "agent": det_agent.name, "text": msg})
            if doc_agent is not None:
                st = engine.build_agent_state(doc_agent.name, channel="doctor")
                msg = await _call_agent(doc_agent, st, per_turn_timeout)
                engine.doctor_log.append(f"{doc_agent.name}: {msg}")
                engine.record_doctor(doc_agent.name, msg)
                await websocket.send_json({"type": "private_message", "channel": "doctor", "agent": doc_agent.name, "text": msg})
            while time.monotonic() < end_time:
                for agent in mafia_agents:
                    if time.monotonic() >= end_time:
                        break
                    st = engine.build_agent_state(agent.name, channel="mafia")
                    msg = await _call_agent(agent, st, per_turn_timeout)
                    engine.log_mafia(f"{agent.name}: {msg}")
                    await websocket.send_json({"type": "private_message", "channel": "mafia", "agent": agent.name, "text": msg})
                    # Track votes (discussion) and explicit KILL commands
                    _, agreed = engine.record_mafia_vote(agent.name, msg)
                    _, kill_agreed = engine.record_mafia_kill(agent.name, msg)
                    # End night early only on unanimous KILL
                    if kill_agreed:
                        early_victim = kill_agreed
                        end_time = 0.0
                        break
                    # Send live status update
                    await websocket.send_json({
                        "type": "mafia_status",
                        "mafia": mafia_names,
                        "alive_mafia": engine.alive_mafia_names(),
                        "votes": engine.mafia_votes,
                        "kills": engine.mafia_kills,
                        "remaining_s": max(0.0, end_time - time.monotonic()),
                    })
                    await asyncio.sleep(0.1)
            # Victim must be explicitly KILLed; if no unanimous KILL, no one dies
            victim = early_victim
            if not victim:
                # If all alive mafia issued KILL for same name by end of timer, accept it
                alive_mafia = engine.alive_mafia_names()
                if alive_mafia and all(v in engine.mafia_kills for v in alive_mafia):
                    vals = {engine.mafia_kills[v] for v in alive_mafia}
                    if len(vals) == 1:
                        candidate = next(iter(vals))
                        if engine.roles.get(candidate) and engine.roles[candidate].alive and engine.roles[candidate].role != "mafia":
                            victim = candidate
            # Apply doctor protection if any
            saved = False
            if victim and engine.doctor_target and victim == engine.doctor_target:
                victim = None
                saved = True
            engine.end_night(victim)
            # Detective private result
            if det_agent is not None and engine.detective_target:
                target = engine.detective_target
                alignment = "Mafia" if (engine.roles.get(target) and engine.roles[target].role == "mafia") else "Town"
                engine.detective_results[target] = alignment
                result_msg = f"RESULT: {target} is {alignment}"
                engine.detective_log.append(f"{det_agent.name}: {result_msg}")
                await websocket.send_json({"type": "private_message", "channel": "detective", "agent": det_agent.name, "text": result_msg})
            await websocket.send_json({"type": "event", "name": "night_result", "victim": victim})
            if saved:
                await websocket.send_json({"type": "event", "name": "night_saved", "target": engine.doctor_target})
            # Check win conditions after night resolution
            alive = engine.alive_players()
            mafia_alive = engine.alive_mafia_names()
            town_alive_count = len(alive) - len(mafia_alive)
            winner = None
            reason = None
            if len(mafia_alive) == 0:
                winner, reason = "town", "all_mafia_eliminated"
            elif town_alive_count <= len(mafia_alive):
                winner, reason = "mafia", "mafia_reached_parity"
            if winner:
                await websocket.send_json({
                    "type": "game_over",
                    "winner": winner,
                    "reason": reason,
                    "mafia_alive": mafia_alive,
                    "alive": alive,
                })
                await websocket.send_json({"type": "done"})
                return
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
                    rounds = int(payload.get("rounds", 10))
                    bg_task = asyncio.create_task(send_cycle(websocket, rounds=rounds))
                    await websocket.send_json({"type": "started"})
            else:
                await websocket.send_json({"type": "error", "message": "unknown command"})
    except WebSocketDisconnect:
        if bg_task and not bg_task.done():
            bg_task.cancel()
            with contextlib.suppress(Exception):
                await bg_task
