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


class GameControl:
    def __init__(self) -> None:
        self._paused = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        # Queue for messages from the human player (admin acting as player)
        # Each item: {"text": str, "channel": "auto"|"public"|"mafia"|"detective"|"doctor"}
        self.player_messages: asyncio.Queue[dict] = asyncio.Queue()

    def pause(self) -> None:
        self._paused = True
        self._pause_event.clear()

    def resume(self) -> None:
        self._paused = False
        self._pause_event.set()

    async def wait_if_paused(self) -> None:
        while not self._pause_event.is_set():
            await asyncio.sleep(0.1)


async def _call_agent(agent, state, timeout: float) -> str:
    try:
        # Run sync .generate in a thread; enforce timeout
        return await asyncio.wait_for(asyncio.to_thread(agent.generate, state), timeout=timeout)
    except asyncio.TimeoutError:
        return f"{agent.name} timed out"
    except Exception as e:  # noqa: BLE001
        return f"{agent.name} error: {type(e).__name__}"


async def send_cycle(websocket: WebSocket, rounds: int = 3, ctrl: GameControl | None = None, human_enabled: bool = True) -> None:
    # Build phases: day/night repeated for the requested number of rounds
    phases: list[str] = []
    for _ in range(max(1, rounds)):
        phases += ["night", "day"]

    agents = get_agents()  # dynamic list (now 9 agents)
    engine = GameEngine([a.name for a in agents], human_name=("puny human" if human_enabled else None))
    agent_timeout = get_agent_timeout()

    # Dispatch human player messages immediately in a background task to avoid delays
    pm_task: asyncio.Task | None = None
    grave_task: asyncio.Task | None = None
    if ctrl is not None:
        async def _player_dispatcher() -> None:
            try:
                while True:
                    payload = await ctrl.player_messages.get()
                    text = (payload.get("text") or "").strip()
                    channel = (payload.get("channel") or "auto").lower()
                    # Determine destination
                    human = engine.human_name or None
                    # Resolve channel
                    dest = "public"
                    if channel == "auto":
                        if engine.phase == "day":
                            dest = "public"
                        else:
                            role = engine.roles.get(human).role if human in engine.roles else "villager"
                            if role in ("mafia", "detective", "doctor"):
                                dest = role
                            else:
                                dest = "public"
                    elif channel in ("public", "mafia", "detective", "doctor", "graveyard"):
                        dest = channel
                    # Broadcast + record
                    if dest == "public":
                        # If human is dead, reroute to graveyard
                        if not human:
                            # No human in the game; ignore public/admin-as-player messages
                            continue
                        if human in engine.roles and not engine.roles[human].alive:
                            dest = "graveyard"
                        else:
                            await websocket.send_json({"type": "agent_message", "agent": human, "text": text})
                            engine.log_public(f"{human}: {text}")
                            # Track explicit LAST WORDS declarations during the day
                            engine.record_last_words(human, text)
                            # Day vote parsing
                            if engine.phase == "day":
                                engine.record_day_vote(human, text)
                                tally = engine.tally_day_votes()
                                await websocket.send_json({
                                    "type": "day_status",
                                    "remaining_s": 0,
                                    "votes": engine.day_votes,
                                    "tally": tally,
                                    "threshold": engine.majority_threshold(),
                                })
                    if dest == "graveyard":
                        speaker = human if human else "ADMIN"
                        engine.graveyard_log.append(f"{speaker}: {text}")
                        await websocket.send_json({"type": "private_message", "channel": "graveyard", "agent": speaker, "text": text})
                    elif dest == "mafia":
                        if not human:
                            continue
                        engine.log_mafia(f"{human}: {text}")
                        await websocket.send_json({"type": "private_message", "channel": "mafia", "agent": human, "text": text})
                        engine.record_mafia_vote(human, text)
                        engine.record_mafia_kill(human, text)
                        await websocket.send_json({
                            "type": "mafia_status",
                            "mafia": [n for n in engine.mafia_names()],
                            "alive_mafia": engine.alive_mafia_names(),
                            "votes": engine.mafia_votes,
                            "kills": engine.mafia_kills,
                            "remaining_s": 0,
                        })
                    elif dest == "detective":
                        engine.detective_log.append(f"{human}: {text}")
                        engine.record_detective(human, text)
                        await websocket.send_json({"type": "private_message", "channel": "detective", "agent": human, "text": text})
                    elif dest == "doctor":
                        engine.doctor_log.append(f"{human}: {text}")
                        engine.record_doctor(human, text)
                        await websocket.send_json({"type": "private_message", "channel": "doctor", "agent": human, "text": text})
            except asyncio.CancelledError:
                return

        pm_task = asyncio.create_task(_player_dispatcher())

    # Background: dead agents chat in graveyard at a gentle cadence
    async def _graveyard_daemon() -> None:
        last_spoke: dict[str, float] = {}
        cooldown = float(os.getenv("GRAVEYARD_COOLDOWN_S", "8"))
        try:
            while True:
                await asyncio.sleep(1.0)
                # Gather dead agent objects
                try:
                    dead_agents = [a for a in agents if (engine.roles.get(a.name) and not engine.roles[a.name].alive)]
                except Exception:
                    dead_agents = []
                for agent in dead_agents:
                    now = time.monotonic()
                    if now - last_spoke.get(agent.name, 0.0) < cooldown:
                        continue
                    st = engine.build_agent_state(agent.name, channel="graveyard")
                    await websocket.send_json({"type": "agent_generating", "agent": agent.name, "channel": "graveyard"})
                    msg = await _call_agent(agent, st, get_agent_timeout())
                    text = (msg or "").strip()
                    engine.graveyard_log.append(f"{agent.name}: {text}")
                    await websocket.send_json({"type": "private_message", "channel": "graveyard", "agent": agent.name, "text": text})
                    await websocket.send_json({"type": "agent_done", "agent": agent.name, "channel": "graveyard"})
                    last_spoke[agent.name] = now
        except asyncio.CancelledError:
            return

    grave_task = asyncio.create_task(_graveyard_daemon())

    for i, phase in enumerate(phases, start=1):
        # Announce phase with current alive roster
        human = engine.human_name
        phase_payload = {
            "type": "phase",
            "name": phase,
            "index": i,
            "ts": datetime.utcnow().isoformat(),
            "agents": engine.alive_players(),
        }
        if human:
            phase_payload["human"] = human
            phase_payload["human_role"] = engine.roles.get(human).role if human in engine.roles else None
        await websocket.send_json(phase_payload)

        if phase == "day":
            if ctrl:
                await ctrl.wait_if_paused()
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
                if ctrl:
                    await ctrl.wait_if_paused()
                alive_speakers = [a for a in agents if (engine.roles.get(a.name) and engine.roles[a.name].alive)]
                if not alive_speakers:
                    break
                for agent in alive_speakers:
                    if ctrl:
                        await ctrl.wait_if_paused()
                    now = time.monotonic()
                    if now >= end_time:
                        break
                    # Respect cooldown and per-day message limit
                    if now - last_spoke.get(agent.name, 0.0) < cooldown:
                        continue
                    if spoke_count.get(agent.name, 0) >= max_msgs:
                        continue
                    st = engine.build_agent_state(agent.name, channel="public")
                    # Indicate which agent is generating now (UI can show a spinner)
                    await websocket.send_json({"type": "agent_generating", "agent": agent.name, "channel": "public"})
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
                    await websocket.send_json({"type": "agent_done", "agent": agent.name, "channel": "public"})
                    engine.log_public(f"{agent.name}: {text}")
                    # Capture explicit LAST WORDS declarations during the day
                    engine.record_last_words(agent.name, text)
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
            # If a victim exists, open a last-words window, prompt the victim, then finalize
            if victim:
                # Open last words window
                engine.open_last_words(victim)
                await websocket.send_json({
                    "type": "event",
                    "name": "last_words_window",
                    "victim": victim,
                    "duration_s": 10,
                })
                # If the victim is an AI agent, prompt once for last words
                ai_victim = next((a for a in agents if a.name == victim), None)
                if ai_victim is not None:
                    st = engine.build_agent_state(victim, channel="public")
                    st["last_words_request"] = True
                    await websocket.send_json({"type": "agent_generating", "agent": victim, "channel": "public"})
                    msg = await _call_agent(ai_victim, st, per_turn_timeout)
                    text = (msg or "").strip()
                    await websocket.send_json({"type": "agent_message", "agent": victim, "text": text})
                    await websocket.send_json({"type": "agent_done", "agent": victim, "channel": "public"})
                    engine.log_public(f"{victim}: {text}")
                    engine.record_last_words(victim, text)
                else:
                    # Human victim: allow up to duration for manual input
                    await asyncio.sleep(10)
                # Close window
                engine.close_last_words()
                # Finalize lynch
                engine.roles[victim].alive = False
                engine.add_event("day_lynch", victim=victim, day=str(engine.day_index))
            await websocket.send_json({
                "type": "event",
                "name": "day_result",
                "victim": victim,
                "victim_role": (engine.roles.get(victim).role if victim and victim in engine.roles else None),
                "last_words": (engine.get_last_words(victim) if victim else None),
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
            if ctrl:
                await ctrl.wait_if_paused()
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
                await websocket.send_json({"type": "agent_generating", "agent": det_agent.name, "channel": "detective"})
                msg = await _call_agent(det_agent, st, per_turn_timeout)
                engine.detective_log.append(f"{det_agent.name}: {msg}")
                engine.record_detective(det_agent.name, msg)
                await websocket.send_json({"type": "private_message", "channel": "detective", "agent": det_agent.name, "text": msg})
                await websocket.send_json({"type": "agent_done", "agent": det_agent.name, "channel": "detective"})
            if doc_agent is not None:
                st = engine.build_agent_state(doc_agent.name, channel="doctor")
                await websocket.send_json({"type": "agent_generating", "agent": doc_agent.name, "channel": "doctor"})
                msg = await _call_agent(doc_agent, st, per_turn_timeout)
                engine.doctor_log.append(f"{doc_agent.name}: {msg}")
                engine.record_doctor(doc_agent.name, msg)
                await websocket.send_json({"type": "private_message", "channel": "doctor", "agent": doc_agent.name, "text": msg})
                await websocket.send_json({"type": "agent_done", "agent": doc_agent.name, "channel": "doctor"})
            while time.monotonic() < end_time:
                if ctrl:
                    await ctrl.wait_if_paused()
                for agent in mafia_agents:
                    if ctrl:
                        await ctrl.wait_if_paused()
                    if time.monotonic() >= end_time:
                        break
                    st = engine.build_agent_state(agent.name, channel="mafia")
                    await websocket.send_json({"type": "agent_generating", "agent": agent.name, "channel": "mafia"})
                    msg = await _call_agent(agent, st, per_turn_timeout)
                    engine.log_mafia(f"{agent.name}: {msg}")
                    await websocket.send_json({"type": "private_message", "channel": "mafia", "agent": agent.name, "text": msg})
                    await websocket.send_json({"type": "agent_done", "agent": agent.name, "channel": "mafia"})
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
            await websocket.send_json({
                "type": "event",
                "name": "night_result",
                "victim": victim,
                "victim_role": (engine.roles.get(victim).role if victim and victim in engine.roles else None),
            })
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
    # Cleanup background dispatcher
    if pm_task:
        pm_task.cancel()
        with contextlib.suppress(Exception):
            await pm_task
    if grave_task:
        grave_task.cancel()
        with contextlib.suppress(Exception):
            await grave_task
    await websocket.send_json({"type": "done"})


@router.websocket("/game")
async def game_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    bg_task: asyncio.Task | None = None
    ctrl: GameControl | None = None
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
                    ctrl = GameControl()
                    human_enabled = bool(payload.get("human", True))
                    bg_task = asyncio.create_task(send_cycle(websocket, rounds=rounds, ctrl=ctrl, human_enabled=human_enabled))
                    await websocket.send_json({"type": "started"})
            elif kind == "pause":
                if ctrl:
                    ctrl.pause()
                    await websocket.send_json({"type": "paused"})
                else:
                    await websocket.send_json({"type": "error", "message": "no game in progress"})
            elif kind == "resume":
                if ctrl:
                    ctrl.resume()
                    await websocket.send_json({"type": "resumed"})
                else:
                    await websocket.send_json({"type": "error", "message": "no game in progress"})
            elif kind == "say":
                txt = (payload.get("text") or "").strip()
                channel = (payload.get("channel") or "auto").lower()
                if not txt:
                    await websocket.send_json({"type": "error", "message": "empty message"})
                elif ctrl:
                    await ctrl.player_messages.put({"text": txt, "channel": channel})
                    await websocket.send_json({"type": "info", "message": "message queued"})
                else:
                    await websocket.send_json({"type": "error", "message": "no game in progress"})
            else:
                await websocket.send_json({"type": "error", "message": "unknown command"})
    except WebSocketDisconnect:
        if bg_task and not bg_task.done():
            bg_task.cancel()
            with contextlib.suppress(Exception):
                await bg_task
