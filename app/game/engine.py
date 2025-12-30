from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple
import re

from .state import Player, assign_roles


@dataclass
class GameEngine:
    agents: List[str]
    roles: Dict[str, Player] = field(init=False)
    phase: str = field(default="night")  # night or day
    round: int = field(default=1)
    day_index: int = field(default=0)
    night_index: int = field(default=0)
    public_log: List[str] = field(default_factory=list)
    mafia_log: List[str] = field(default_factory=list)
    detective_log: List[str] = field(default_factory=list)
    doctor_log: List[str] = field(default_factory=list)
    events: List[Dict[str, str]] = field(default_factory=list)
    mafia_votes: Dict[str, str] = field(default_factory=dict)
    mafia_kills: Dict[str, str] = field(default_factory=dict)
    day_votes: Dict[str, str] = field(default_factory=dict)
    detective_target: str | None = None
    doctor_target: str | None = None
    detective_results: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.roles = assign_roles(self.agents)

    # --- Queries
    def alive_players(self) -> List[str]:
        return [n for n, p in self.roles.items() if p.alive]

    def mafia_names(self) -> List[str]:
        return [n for n, p in self.roles.items() if p.role == "mafia"]

    def alive_mafia_names(self) -> List[str]:
        return [n for n, p in self.roles.items() if p.alive and p.role == "mafia"]

    # --- Logs
    def log_public(self, text: str) -> None:
        self.public_log.append(text)

    def log_mafia(self, text: str) -> None:
        self.mafia_log.append(text)

    def add_event(self, kind: str, **data: str) -> None:
        e = {"type": kind}
        e.update({k: str(v) for k, v in data.items()})
        self.events.append(e)

    def summary(self, limit: int = 6) -> str:
        # A compact, safe summary used in prompts; last N notable events only
        parts: List[str] = []
        if self.events:
            for e in self.events[-limit:]:
                if e["type"] == "night_kill":
                    parts.append(f"Night{e.get('night','#')}: {e.get('victim')} died")
                elif e["type"] == "day_lynch":
                    parts.append(f"Day{e.get('day','#')}: {e.get('victim')} was eliminated")
                elif e["type"] == "night_saved":
                    parts.append(f"Night{e.get('night','#')}: save on {e.get('target')}")
        return "; ".join(parts)

    def recap(self, limit: int = 6) -> str:
        """Human-friendly recap: recent events, alive and dead lists.
        Example: Recap: Night1: Alice died; Day1: Bob eliminated; Alive: A,B,C; Dead: X,Y
        """
        ev = []
        if self.events:
            for e in self.events[-limit:]:
                t = e.get("type")
                if t == "night_kill":
                    ev.append(f"Night{e.get('night','#')}: {e.get('victim') } died")
                elif t == "day_lynch":
                    ev.append(f"Day{e.get('day','#')}: {e.get('victim')} eliminated")
                elif t == "night_saved":
                    tgt = e.get('target') or 'someone'
                    ev.append(f"Night{e.get('night','#')}: save on {tgt}")
        alive = ", ".join(self.alive_players()) or "none"
        dead = ", ".join(sorted([n for n,p in self.roles.items() if not p.alive])) or "none"
        head = f"Recap: {'; '.join(ev)}" if ev else "Recap: (no prior events)"
        return f"{head}; Alive: {alive}; Dead: {dead}"

    # --- Phase management
    def start_night(self) -> None:
        self.phase = "night"
        self.night_index += 1
        # Reset mafia chat for this night window so context reflects current night only
        self.mafia_log = []
        self.detective_log = []
        self.doctor_log = []
        self.mafia_votes = {}
        self.mafia_kills = {}
        self.detective_target = None
        self.doctor_target = None
        # keep detective_results across nights so detective accumulates knowledge

    def end_night(self, victim: str | None) -> None:
        if victim:
            self.roles[victim].alive = False
            self.add_event("night_kill", victim=victim, night=str(self.night_index))

    def start_day(self) -> None:
        self.phase = "day"
        self.day_index += 1
        self.day_votes = {}

    def end_day(self) -> None:
        self.round += 1

    # --- Decisions
    def choose_night_victim(self) -> str | None:
        # Prefer a non-mafia alive target
        for n, p in self.roles.items():
            if p.alive and p.role != "mafia":
                return n
        return None

    def parse_target_from_mafia_log(self) -> str | None:
        # naive heuristic: find a mentioned alive player name in recent mafia messages
        alive = set(self.alive_players())
        for line in reversed(self.mafia_log[-12:]):  # scan last 12 lines
            for name in alive:
                if name in line:
                    return name
        return None

    def parse_target_from_text(self, text: str) -> str | None:
        alive = set(self.alive_players())
        # Prefer explicit VOTE: <name>
        lower = text.lower()
        if "vote:" in lower or "kill" in lower:
            for name in alive:
                if name.lower() in lower:
                    return name
        # fallback: first alive name mentioned
        for name in alive:
            if name in text:
                return name
        return None

    def parse_kill_from_text(self, text: str) -> str | None:
        """Parse an explicit mafia KILL target.
        Accepts forms like 'KILL: alice' or 'kill alice', even with Markdown (e.g., **KILL: alice**).
        """
        import re
        alive_list = self.alive_players()
        # Normalize markdown and punctuation noise
        lower = text.lower()
        lower = lower.replace("**", " ").replace("*", " ").replace("`", " ").replace("_", " ")
        m = re.search(r"kill\s*:?\s*([^\n\r,.;!]+)", lower)
        if m:
            seg = m.group(1).strip().strip("'\" ")
            # remove stray non-alnum except hyphen/underscore/space
            seg = re.sub(r"[^a-z0-9\-_ ]", "", seg)
            for name in alive_list:
                nl = name.lower()
                if (seg == nl or seg in nl or nl in seg) and self.roles.get(name).role != "mafia":
                    return name
        return None

    def record_mafia_vote(self, voter: str, text: str) -> Tuple[Dict[str, str], str | None]:
        target = self.parse_target_from_text(text) or ""
        if target:
            self.mafia_votes[voter] = target
        # Check unanimity among alive mafia
        alive_mafia = self.alive_mafia_names()
        if not alive_mafia:
            return self.mafia_votes, None
        if all(v in self.mafia_votes for v in alive_mafia):
            vals = {self.mafia_votes[v] for v in alive_mafia}
            if len(vals) == 1:
                agreed = next(iter(vals))
                # Ensure target is alive and not mafia
                if agreed in self.roles and self.roles[agreed].alive and self.roles[agreed].role != "mafia":
                    return self.mafia_votes, agreed
        return self.mafia_votes, None

    # --- Prompt state
    def build_agent_state(self, agent_name: str, channel: str) -> Dict[str, object]:
        role = self.roles.get(agent_name).role if agent_name in self.roles else "villager"
        st: Dict[str, object] = {
            "phase": self.phase,
            "round": self.round,
            "day_index": self.day_index,
            "night_index": self.night_index,
            "role": role,
            "channel": channel,
            "alive_players": self.alive_players(),
            "summary": self.summary(),
            "recap": self.recap(),
        }
        if role == "mafia" and channel == "mafia":
            st["mafia_teammates"] = [n for n in self.mafia_names() if n != agent_name]
            # Provide recent private chat lines for coordination
            recent = self.mafia_log[-8:]
            st["recent_mafia"] = recent
            # Surface current votes snapshot
            if self.mafia_votes:
                st["votes"] = dict(self.mafia_votes)
            if self.mafia_kills:
                st["kills"] = dict(self.mafia_kills)
        if channel == "public":
            st["recent_public"] = self.public_log[-8:]
            if self.day_votes:
                st["votes"] = dict(self.day_votes)
        if role == "detective" and channel == "detective":
            st["recent_detective"] = self.detective_log[-6:]
            if self.detective_results:
                st["known_alignments"] = dict(self.detective_results)
        if role == "doctor" and channel == "doctor":
            st["recent_doctor"] = self.doctor_log[-6:]
        return st

    def record_mafia_kill(self, voter: str, text: str) -> Tuple[Dict[str, str], str | None]:
        target = self.parse_kill_from_text(text) or ""
        if target:
            self.mafia_kills[voter] = target
        alive_mafia = self.alive_mafia_names()
        if not alive_mafia:
            return self.mafia_kills, None
        if all(v in self.mafia_kills for v in alive_mafia):
            vals = {self.mafia_kills[v] for v in alive_mafia}
            if len(vals) == 1:
                agreed = next(iter(vals))
                if agreed in self.roles and self.roles[agreed].alive and self.roles[agreed].role != "mafia":
                    return self.mafia_kills, agreed
        return self.mafia_kills, None

    # --- Day voting
    def parse_day_vote_from_text(self, text: str) -> str | None:
        """Parse an explicit public day vote.
        Only counts if the message contains an explicit 'VOTE <name>' or 'VOTE: <name>' or 'lynch <name>'.
        Mere mentions of names do NOT count as votes.
        """
        alive_list = self.alive_players()
        lower = text.lower()
        # Allow optional colon after vote, and simple 'lynch <name>' form
        patterns = [r"\bvote\s*:?\s*([^\n\r,.;!]+)", r"\blynch\s+([^\n\r,.;!]+)"]
        for pat in patterns:
            m = re.search(pat, lower)
            if not m:
                continue
            seg = m.group(1).strip().strip("'\" ")
            # Normalize target token
            seg = re.sub(r"[^a-z0-9\- _]", "", seg)
            if len(seg) < 2:
                continue
            # Prefer exact or prefix match; avoid loose substring matches that cause false positives
            # Build candidate list with a simple score (exact=3, prefix=2, contains=1)
            best = None
            best_score = 0
            for name in alive_list:
                nl = name.lower()
                score = 0
                if seg == nl:
                    score = 3
                elif nl.startswith(seg) or seg.startswith(nl):
                    score = 2
                elif seg in nl and len(seg) >= 3:
                    score = 1
                if score > best_score:
                    best = name
                    best_score = score
            if best_score > 0:
                return best
        # No explicit vote form found
        return None

    def record_day_vote(self, voter: str, text: str) -> Dict[str, str]:
        target = self.parse_day_vote_from_text(text)
        if target:
            # Only alive voters count
            if voter in self.roles and self.roles[voter].alive:
                self.day_votes[voter] = target
        return self.day_votes

    def compute_day_lynch(self) -> str | None:
        # Tally votes from alive voters only; plurality wins; ties -> no lynch
        tally: Dict[str, int] = {}
        for voter, target in self.day_votes.items():
            if voter in self.roles and self.roles[voter].alive and target in self.roles and self.roles[target].alive:
                tally[target] = tally.get(target, 0) + 1
        if not tally:
            return None
        # Find max
        max_votes = max(tally.values())
        top = [name for name, count in tally.items() if count == max_votes]
        if len(top) == 1:
            return top[0]
        return None

    def tally_day_votes(self) -> Dict[str, int]:
        tally: Dict[str, int] = {}
        for voter, target in self.day_votes.items():
            if voter in self.roles and self.roles[voter].alive and target in self.roles and self.roles[target].alive:
                tally[target] = tally.get(target, 0) + 1
        return tally

    def majority_threshold(self) -> int:
        alive = len(self.alive_players())
        return alive // 2 + 1

    # --- Detective/Doctor actions
    def parse_detective_from_text(self, text: str) -> str | None:
        alive = set(self.alive_players())
        lower = text.lower()
        if any(k in lower for k in ("inspect", "investigate", "check")):
            for name in alive:
                if name.lower() in lower:
                    return name
        return None

    def parse_doctor_from_text(self, text: str) -> str | None:
        alive = set(self.alive_players())
        lower = text.lower()
        if any(k in lower for k in ("protect", "save", "guard", "heal")):
            for name in alive:
                if name.lower() in lower:
                    return name
        return None

    def record_detective(self, voter: str, text: str) -> str | None:
        target = self.parse_detective_from_text(text)
        if target:
            self.detective_target = target
        return self.detective_target

    def record_doctor(self, voter: str, text: str) -> str | None:
        target = self.parse_doctor_from_text(text)
        if target:
            self.doctor_target = target
        return self.doctor_target
