"""
DnD game-master tool pack — loaded for the `dnd` mode.

Philosophy: the model narrates, the tools are the ledger. Campaign state
lives in one JSON file at <project_dir>/dnd/campaign_state.json (paths:
DND_SUBDIR / CAMPAIGN_STATE_FILENAME) so a campaign survives restarts and
the model never has to hold HP totals in its head. Dice come from the core
`roll_dice` tool — this pack deliberately has no randomness of its own.
"""

import json
import time
from pathlib import Path
from typing import Optional

from paths import CAMPAIGN_STATE_FILENAME, DND_SUBDIR

QUEST_LOG_CAP = 200

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "dnd_new_campaign",
            "description": (
                "Start a new campaign: creates dnd/campaign_state.json in the "
                "project dir. Refuses if a campaign already exists unless "
                "overwrite=true. Party members can start minimal (just names) "
                "and be fleshed out later with dnd_update_character."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign": {"type": "string", "description": "Campaign title."},
                    "setting":  {"type": "string", "description": "One-paragraph setting/premise."},
                    "party": {
                        "type": "array",
                        "description": "Player characters. Each: {name, cls, level, max_hp, stats, inventory}. Only name is required.",
                        "items": {"type": "object"},
                    },
                    "overwrite": {"type": "boolean", "description": "Replace an existing campaign. Default false."},
                },
                "required": ["campaign"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dnd_get_state",
            "description": "Read the full campaign state (party, scene, quest log tail, combat). Call at session start and whenever unsure of a number — never recall HP/inventory from memory.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dnd_update_character",
            "description": (
                "Create or update a party member (or notable NPC follower). "
                "`patch` is shallow-merged into the character: pass only the "
                "fields that changed (e.g. {\"hp\": 17} after damage, "
                "{\"inventory\": [...]} after looting — send the full new list). "
                "Pass {\"remove\": true} to drop the character."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string", "description": "Character name (party key)."},
                    "patch": {"type": "object", "description": "Fields to merge, e.g. {\"hp\": 12, \"conditions\": [\"poisoned\"]}."},
                },
                "required": ["name", "patch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dnd_set_scene",
            "description": "Set the current scene (location, situation, present NPCs). Also appends a scene-change line to the quest log. Call on every meaningful location/situation change.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location":    {"type": "string", "description": "Where the party is."},
                    "description": {"type": "string", "description": "Current situation in 1-3 sentences."},
                    "npcs": {
                        "type": "array",
                        "description": "NPCs present, e.g. [{\"name\": \"Bram\", \"role\": \"innkeeper\", \"disposition\": \"friendly\"}].",
                        "items": {"type": "object"},
                    },
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dnd_log_event",
            "description": "Append one line to the quest log — decisions, discoveries, promises, kills, loot. The log is the campaign's memory across sessions; log anything you'd hate to forget.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "One-line event description."},
                    "kind": {"type": "string", "enum": ["event", "quest", "loot", "combat", "npc", "death"], "description": "Category. Default 'event'."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dnd_combat",
            "description": (
                "Combat ledger. Actions: 'start' (pass enemies + initiative "
                "order), 'damage' / 'heal' (target + amount — works on party "
                "members and enemies; party HP persists to their sheet), "
                "'next_round', 'end' (appends a combat summary to the log). "
                "Roll attacks/damage with roll_dice first, then record here."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["start", "damage", "heal", "next_round", "end"]},
                    "enemies": {
                        "type": "array",
                        "description": "start only: [{\"name\": \"Goblin A\", \"hp\": 7, \"ac\": 13}].",
                        "items": {"type": "object"},
                    },
                    "initiative": {
                        "type": "array",
                        "description": "start only: turn order, e.g. [\"Thorin\", \"Goblin A\", \"Mira\"].",
                        "items": {"type": "string"},
                    },
                    "target": {"type": "string", "description": "damage/heal: character or enemy name."},
                    "amount": {"type": "integer", "description": "damage/heal: hit points."},
                    "summary": {"type": "string", "description": "end: one-line outcome for the quest log."},
                },
                "required": ["action"],
            },
        },
    },
]


# ── State I/O ────────────────────────────────────────────────────────────────

def _state_path(project_dir: Path) -> Path:
    return project_dir / DND_SUBDIR / CAMPAIGN_STATE_FILENAME


def _load(project_dir: Path) -> Optional[dict]:
    path = _state_path(project_dir)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save(project_dir: Path, state: dict) -> None:
    path = _state_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _log(state: dict, kind: str, text: str) -> None:
    state.setdefault("quest_log", []).append(
        {"ts": int(time.time()), "kind": kind, "text": text}
    )
    if len(state["quest_log"]) > QUEST_LOG_CAP:
        state["quest_log"] = state["quest_log"][-QUEST_LOG_CAP:]


def _brief(state: dict) -> str:
    """Compact human-readable snapshot for tool returns."""
    party = ", ".join(
        f"{n} ({c.get('hp', '?')}/{c.get('max_hp', '?')} hp)"
        for n, c in state.get("party", {}).items()
    ) or "(no party)"
    scene = state.get("scene") or {}
    combat = state.get("combat")
    lines = [
        f"campaign: {state.get('campaign', '?')}",
        f"party: {party}",
        f"scene: {scene.get('location', '(none)')}",
    ]
    if combat:
        enemies = ", ".join(f"{n} ({e.get('hp', '?')} hp)" for n, e in combat.get("enemies", {}).items())
        lines.append(f"combat: round {combat.get('round', 1)} — {enemies or 'no enemies'}")
    return "\n".join(lines)


# ── Dispatch ─────────────────────────────────────────────────────────────────

def execute_tool(
    name: str,
    args: dict,
    session_id: str,
    project_dir: Path,
    user_name: Optional[str] = None,
) -> str:
    try:
        if name == "dnd_new_campaign":
            existing = _load(project_dir)
            if existing is not None and not args.get("overwrite"):
                return (
                    f"Error: campaign '{existing.get('campaign', '?')}' already exists. "
                    "Pass overwrite=true to replace it, or continue with dnd_get_state."
                )
            party = {}
            for member in args.get("party") or []:
                if not isinstance(member, dict) or not member.get("name"):
                    continue
                m = dict(member)
                pname = m.pop("name")
                m.setdefault("level", 1)
                if "max_hp" in m:
                    m.setdefault("hp", m["max_hp"])
                party[pname] = m
            state = {
                "v": 1,
                "campaign": args["campaign"],
                "setting": args.get("setting", ""),
                "created_at": int(time.time()),
                "party": party,
                "scene": None,
                "quest_log": [],
                "combat": None,
            }
            _log(state, "event", f"Campaign '{args['campaign']}' begins.")
            _save(project_dir, state)
            return f"Campaign created.\n{_brief(state)}"

        # Everything below needs an existing campaign.
        state = _load(project_dir)
        if state is None:
            return "Error: no campaign. Call dnd_new_campaign first."

        if name == "dnd_get_state":
            view = dict(state)
            view["quest_log"] = state.get("quest_log", [])[-20:]  # tail only
            return json.dumps(view, indent=1, ensure_ascii=False)

        if name == "dnd_update_character":
            cname = (args.get("name") or "").strip()
            patch = args.get("patch")
            if not cname or not isinstance(patch, dict):
                return "Error: 'name' and object 'patch' are required"
            party = state.setdefault("party", {})
            if patch.get("remove"):
                if party.pop(cname, None) is None:
                    return f"Error: no character named '{cname}'"
                _log(state, "event", f"{cname} leaves the party.")
                _save(project_dir, state)
                return f"{cname} removed.\n{_brief(state)}"
            char = party.setdefault(cname, {})
            char.update({k: v for k, v in patch.items() if k != "remove"})
            _save(project_dir, state)
            return f"{cname} updated: {json.dumps(char, ensure_ascii=False)}"

        if name == "dnd_set_scene":
            loc = (args.get("location") or "").strip()
            if not loc:
                return "Error: 'location' is required"
            state["scene"] = {
                "location": loc,
                "description": args.get("description", ""),
                "npcs": args.get("npcs") or [],
            }
            _log(state, "event", f"Scene: {loc}")
            _save(project_dir, state)
            return f"Scene set: {loc}"

        if name == "dnd_log_event":
            text = (args.get("text") or "").strip()
            if not text:
                return "Error: 'text' is required"
            _log(state, args.get("kind") or "event", text)
            _save(project_dir, state)
            return f"Logged: {text}"

        if name == "dnd_combat":
            action = (args.get("action") or "").strip()
            combat = state.get("combat")

            if action == "start":
                enemies = {}
                for e in args.get("enemies") or []:
                    if isinstance(e, dict) and e.get("name"):
                        d = dict(e)
                        ename = d.pop("name")
                        enemies[ename] = d
                state["combat"] = {
                    "round": 1,
                    "initiative": args.get("initiative") or [],
                    "enemies": enemies,
                }
                _log(state, "combat", f"Combat begins: {', '.join(enemies) or 'unknown foes'}")
                _save(project_dir, state)
                return f"Combat started (round 1).\n{_brief(state)}"

            if combat is None:
                return "Error: no combat in progress. Use action='start' first."

            if action in ("damage", "heal"):
                target = (args.get("target") or "").strip()
                try:
                    amount = int(args.get("amount", 0))
                except (TypeError, ValueError):
                    return "Error: 'amount' must be an integer"
                if not target or amount < 0:
                    return "Error: 'target' and non-negative 'amount' are required"
                delta = -amount if action == "damage" else amount
                if target in combat.get("enemies", {}):
                    e = combat["enemies"][target]
                    e["hp"] = max(0, int(e.get("hp", 0)) + delta)
                    note = f"{target} is DOWN." if e["hp"] == 0 else f"{target}: {e['hp']} hp."
                    if e["hp"] == 0:
                        _log(state, "combat", f"{target} defeated.")
                elif target in state.get("party", {}):
                    c = state["party"][target]
                    cap = c.get("max_hp")
                    hp = int(c.get("hp", cap or 0)) + delta
                    if cap is not None:
                        hp = min(int(cap), hp)
                    c["hp"] = max(0, hp)
                    note = (
                        f"{target} is DYING (0 hp)!" if c["hp"] == 0
                        else f"{target}: {c['hp']}/{cap if cap is not None else '?'} hp."
                    )
                    if c["hp"] == 0:
                        _log(state, "death", f"{target} drops to 0 hp.")
                else:
                    return f"Error: '{target}' is neither an enemy nor a party member"
                _save(project_dir, state)
                return f"{action} {amount} → {note}"

            if action == "next_round":
                combat["round"] = int(combat.get("round", 1)) + 1
                _save(project_dir, state)
                return f"Round {combat['round']}. Initiative: {', '.join(combat.get('initiative', [])) or '(unset)'}"

            if action == "end":
                summary = (args.get("summary") or "Combat ends.").strip()
                _log(state, "combat", summary)
                state["combat"] = None
                _save(project_dir, state)
                return f"Combat ended. {summary}\n{_brief(state)}"

            return "Error: action must be one of start/damage/heal/next_round/end"

        return f"Unknown dnd tool: {name}"

    except Exception as e:
        return f"Error: {e}"
