"""
Core tool pack — loaded in every mode.

Kept deliberately small: the tool deck is the model's attention budget, so
every definition here rides in every request. TOOL_DEFINITIONS is the
always-on set (file reading, manual, task checklist, paths, and the single
consolidated `layout` tool). GAME_TOOLS (roll_dice) only load for game modes
— see tools/__init__.py. The coding toolkit lives in tools/coding.py and
only loads for the DeetsCode mode.

Shared state (pending_writes, read_files) lives here even though most of its
consumers are in coding.py, because server.py imports it via `tools` and we
don't want the import path to depend on which mode happens to be loaded.
"""

import random
from pathlib import Path
from typing import Optional

# Shared mutable state used by coding.py (write_file/edit_file/list_context_files)
# and by server.py's auto-apply flow. Kept here to give it a stable import path
# regardless of which tool packs are active.
pending_writes: dict[str, str] = {}
read_files: list[str] = []


def clear_pending_writes():
    pending_writes.clear()


def clear_read_files():
    read_files.clear()


# Constants shared with coding.py — kept here so there's one source of truth.
MAX_READ_CHARS = 100_000


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file in the project. Returns line-numbered content. "
                "For large files, pass start_line/end_line to read just the "
                "range you need."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path":       {"type": "string", "description": "Path relative to project root"},
                    "start_line": {"type": "integer", "description": "First line to read (1-indexed, inclusive). Omit to read from the beginning."},
                    "end_line":   {"type": "integer", "description": "Last line to read (inclusive). Omit to read to the end of the file."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the contents of a directory in the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to project root. Use '.' for the root."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_manual",
            "description": "List the project's manual (project-scoped reference docs in `manual/`) and their section headings — architecture, conventions, server, styling, etc. Use this to discover what's documented, then call load_manual to pull just the section you need into context.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_manual",
            "description": "Read a manual doc, optionally just one section. Prefer loading a single section over the whole doc — manual files can be long and full loads eat context. Call list_manual first to see section names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name":    {"type": "string", "description": "Manual doc name (without .md), e.g. 'server' or 'styling'."},
                    "section": {"type": "string", "description": "Optional level-2 heading title to extract (e.g. 'Agent Loop'). Omit to load the whole doc."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_path",
            "description": (
                "Register (or update) a path constant in paths.py — the single source of "
                "truth for filesystem paths across the harness. Always use this instead "
                "of hardcoding `Path(__file__).parent / ...` in any other module. "
                "Kinds: 'dir' and 'file' resolve against HARNESS_ROOT and are exported "
                "as pathlib.Path; 'str' is a bare string constant (use for per-project "
                "filenames/subdirs that get resolved against a project root at runtime). "
                "If the constant already exists, its value is replaced in place."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string", "description": "SCREAMING_SNAKE constant name (e.g. REPORTS_DIR, CACHE_FILE)."},
                    "value": {"type": "string", "description": "For dir/file: path relative to HARNESS_ROOT (no leading slash). For str: the literal value."},
                    "kind":  {"type": "string", "enum": ["dir", "file", "str"], "description": "'dir' → Path, directory. 'file' → Path, file. 'str' → bare string (per-project relative path/name)."},
                    "description": {"type": "string", "description": "Optional one-line comment placed above the constant."},
                },
                "required": ["name", "value", "kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "layout",
            "description": (
                "Inspect and command the live workspace (bento) layout — one tool, "
                "selected by `action`:\n"
                "- 'get': read the live layout (grid, regions, every instance with "
                "its state, pin, floors). Call this first before changing anything.\n"
                "- 'panels': list installed panels (name, title, sizes, default state).\n"
                "- 'state': set a runtime state on an instance — 'dormant' (tray "
                "icon), 'idle' (small), 'active' (medium, passive interest), "
                "'focused' (hero, full attention). Transient; the UI rearranges "
                "live. Use to surface what you're discussing or tuck away what's done.\n"
                "- 'pin': pin an instance at col,row spanning cols×rows (persisted; "
                "1-indexed, (1,1) is top-left). Pins are floors — a promoted panel "
                "can grow past them. Validation errors come back verbatim; fix and retry.\n"
                "- 'unpin': return an instance to score-ordered auto-flow.\n"
                "- 'floor': set persistent floors (locked_size, locked_floor, "
                "never_dormant; clear=true drops all floors).\n"
                "- 'recompute': force a fresh flow pass (refreshes recency decay).\n"
                "- 'preset_save' / 'preset_apply': capture or apply a named layout sheet.\n"
                "Use instance ids from the layout (e.g. 'youtube_a', 'tool_log'), "
                "NOT panel names. Grid is 12 columns: spans across one row sum to "
                "12 (12, 6+6, 6+3+3, 3+3+3+3). Size classes: small=3 cols, "
                "medium=6, large=6×2 rows, hero=12×2."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action":        {"type": "string", "enum": ["get", "panels", "state", "pin", "unpin", "floor", "recompute", "preset_apply", "preset_save"], "description": "Which layout operation to perform."},
                    "instance":      {"type": "string", "description": "Instance id from the layout. Required for state/pin/unpin/floor."},
                    "state":         {"type": "string", "enum": ["dormant", "idle", "active", "focused"], "description": "Target state (action 'state')."},
                    "col":           {"type": "integer", "description": "Pin start column, 1-indexed (action 'pin')."},
                    "row":           {"type": "integer", "description": "Pin start row, 1-indexed (action 'pin')."},
                    "cols":          {"type": "integer", "description": "Pin column span, default 1. Spans across a row must fit 12 columns (action 'pin')."},
                    "rows":          {"type": "integer", "description": "Pin row span, default 1 (action 'pin')."},
                    "locked_size":   {"type": "string", "enum": ["icon", "small", "medium", "large", "hero"], "description": "Size class the instance never shrinks below (action 'floor')."},
                    "locked_floor":  {"type": "string", "enum": ["dormant", "idle", "active", "focused"], "description": "State the instance never drops below (action 'floor')."},
                    "never_dormant": {"type": "boolean", "description": "If true, dormant requests clamp to idle (action 'floor')."},
                    "clear":         {"type": "boolean", "description": "Drop all floors on the instance (action 'floor')."},
                    "name":          {"type": "string", "description": "Preset name, filename without .json (actions 'preset_save' / 'preset_apply')."},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": "Create, update, or clear the task checklist (task.md). Use markdown checkboxes: - [ ] todo, - [/] in-progress, - [x] done. To clear/delete the checklist (task finished or abandoned), pass clear: true (or an empty content string — both clear). Omit all arguments to read the current checklist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Full markdown content for task.md. Use - [ ] for todo, - [/] for in-progress, - [x] for done. An empty string clears the checklist."},
                    "clear": {"type": "boolean", "description": "Set true to delete task.md entirely (clears the task list). Takes precedence over content."},
                },
                "required": [],
            },
        },
    },
]


# Game-mode-only core tools (dnd, chess, mafia…). Defined here because the
# handlers share this module's execute_tool, but kept out of TOOL_DEFINITIONS
# so coding/blog decks don't carry dice they'll never roll — see
# tools/__init__.py's _GAME_MODES gate.
GAME_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": "Roll dice. Use this for any probabilistic outcome — it's instant and cannot be hallucinated. Returns individual rolls, the total, and the expression for narration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sides":     {"type": "integer", "description": "Number of sides on each die (e.g. 20 for d20, 6 for d6). Must be >= 2."},
                    "count":     {"type": "integer", "description": "How many dice to roll. Default 1."},
                    "modifier":  {"type": "integer", "description": "Flat modifier to add to the total (e.g. +3 for a Strength bonus). Default 0."},
                    "advantage": {"type": "string", "enum": ["none", "advantage", "disadvantage"], "description": "For a single die: roll twice and keep higher (advantage) or lower (disadvantage). Ignored when count > 1."},
                    "label":     {"type": "string", "description": "Optional short label for narration (e.g. 'attack', 'persuasion', 'damage')."},
                },
                "required": ["sides"],
            },
        },
    },
]


SKIP_DIRS_HIDDEN = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"}

from paths import PROJECT_MANUAL_SUBDIR


def _manual_dir(project_dir: Path) -> Path:
    """Where the manual lives for the current project. Single source — the
    legacy global `packs/` fallback was retired when usage settled on
    project-scoped manuals only."""
    return project_dir / PROJECT_MANUAL_SUBDIR


def _find_manual(name: str, project_dir: Path) -> Optional[Path]:
    safe = Path(name).name
    path = _manual_dir(project_dir) / f"{safe}.md"
    return path if path.is_file() else None


def _manual_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown by `## ` (level-2) headings. Preamble above the first
    heading is returned under the synthetic section name `_preamble`."""
    sections: list[tuple[str, list[str]]] = [("_preamble", [])]
    for line in text.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            title = line[3:].strip()
            sections.append((title, []))
        else:
            sections[-1][1].append(line)
    return [(t, "\n".join(ls).strip()) for t, ls in sections if "\n".join(ls).strip()]


import re as _re
import keyword as _keyword

_PATHS_FILE = Path(__file__).parent.parent / "paths.py"
_CONST_LINE_RE = _re.compile(r"^([A-Z][A-Z0-9_]*)\s*[:=]")

# Names register_path must never replace — everything else in paths.py hangs
# off HARNESS_ROOT, and clobbering it would make the module unimportable.
_RESERVED_PATH_NAMES = {"HARNESS_ROOT"}


def _write_paths_file(new_text: str) -> Optional[str]:
    """Compile-check the candidate paths.py before writing. Every module in
    the harness imports paths at boot, so a syntax error here bricks the
    server. Returns an error string, or None on successful write."""
    try:
        compile(new_text, str(_PATHS_FILE), "exec")
    except SyntaxError as e:
        return f"Error: change would make paths.py unparseable (line {e.lineno}: {e.msg}) — not written"
    _PATHS_FILE.write_text(new_text, encoding="utf-8")
    return None


def _register_path(args: dict, project_dir: Path) -> str:
    # This tool edits the HARNESS's paths.py — it only makes sense when the
    # active project IS the harness. Guard against silently mutating the
    # harness registry while working in some other repo.
    harness_root = _PATHS_FILE.parent.resolve()
    if project_dir.resolve() != harness_root:
        return (
            f"Error: register_path edits the harness's own paths.py, but the "
            f"active project is {project_dir}. It is only available when the "
            f"project is the harness root ({harness_root})."
        )

    name = (args.get("name") or "").strip()
    value = (args.get("value") or "").strip()
    kind = (args.get("kind") or "").strip()
    desc = (args.get("description") or "").strip()

    if not name or not name.isidentifier() or not name.isupper():
        return "Error: 'name' must be a SCREAMING_SNAKE identifier"
    if _keyword.iskeyword(name) or _keyword.issoftkeyword(name):
        return f"Error: '{name}' is a Python keyword and can't be a constant name"
    if name in _RESERVED_PATH_NAMES:
        return f"Error: '{name}' is a reserved name in paths.py — pick a different constant"
    if not value:
        return "Error: 'value' is required"
    if kind not in ("dir", "file", "str"):
        return "Error: 'kind' must be one of 'dir', 'file', 'str'"
    if not _PATHS_FILE.is_file():
        return f"Error: paths.py not found at {_PATHS_FILE}"

    # Escape quotes in value for safe embedding in a double-quoted string literal.
    safe_value = value.replace("\\", "\\\\").replace('"', '\\"')
    if kind == "str":
        new_line = f'{name} = "{safe_value}"'
    else:
        new_line = f'{name}: Path = HARNESS_ROOT / "{safe_value}"'

    block = (f"# {desc}\n{new_line}" if desc else new_line)

    original = _PATHS_FILE.read_text(encoding="utf-8")
    lines = original.splitlines()

    # Replace an existing constant line (plus its immediately-preceding comment, if any).
    replaced = False
    for i, line in enumerate(lines):
        m = _CONST_LINE_RE.match(line)
        if m and m.group(1) == name:
            start = i
            # Absorb one preceding comment line if it belongs to this constant
            # (adjacent, starts with '#', and is not blank).
            if start > 0 and lines[start - 1].startswith("#"):
                start -= 1
            new_lines = lines[:start] + block.split("\n") + lines[i + 1:]
            err = _write_paths_file("\n".join(new_lines) + ("\n" if original.endswith("\n") else ""))
            if err:
                return err
            return f"Updated `{name}` in paths.py → {new_line}"

    # Append to end.
    suffix = "" if original.endswith("\n") else "\n"
    new_text = original + suffix + block + "\n"
    err = _write_paths_file(new_text)
    if err:
        return err
    return f"Added `{name}` to paths.py → {new_line}"


# ── Layout tools ─────────────────────────────────────────────────────────────
# Thin wrappers over panels/loader.py. Mutations write the layout file
# directly; server.py's tool-dispatch site broadcasts `layout_updated` to
# connected clients after any of these returns "OK: ..." (same pattern as
# the state overlay). Validator errors return verbatim so the model can
# self-correct on the next call.
#
# The model-facing surface is the single `layout` tool (action enum) — the
# per-name entry points below survive as compat aliases so old sessions and
# scripts keep working.

_LAYOUT_TOOLS = {
    "get_layout", "get_panels", "pin_instance", "unpin_instance",
    "set_instance_floor", "apply_layout_preset", "save_layout_preset",
}

# `layout` tool action → legacy handler name.
_LAYOUT_ACTIONS = {
    "get":          "get_layout",
    "panels":       "get_panels",
    "pin":          "pin_instance",
    "unpin":        "unpin_instance",
    "floor":        "set_instance_floor",
    "preset_apply": "apply_layout_preset",
    "preset_save":  "save_layout_preset",
}


def _state_overlay(args: dict) -> str:
    """Validate a runtime state push. The WS broadcast happens in server.py's
    dispatch site so this stays sync; the confirmation string is enough for
    the model's tool loop."""
    inst = (args.get("instance") or "").strip()
    st = (args.get("state") or "").strip()
    valid = {"dormant", "idle", "active", "focused"}
    if not inst:
        return "Error: 'instance' is required"
    if st not in valid:
        return f"Error: 'state' must be one of {sorted(valid)}"
    return f"OK: requested state '{st}' for instance '{inst}' (bento updating live)."


def _layout_tool(name: str, args: dict) -> str:
    import json
    from panels import loader
    import paths as _paths

    if not loader.registry():
        loader.discover()

    if name == "get_layout":
        return json.dumps(loader.condensed_layout(), indent=1)

    if name == "get_panels":
        return json.dumps(loader.condensed_panels(), indent=1)

    inst_id = (args.get("instance") or "").strip() if "instance" in args else ""

    if name == "pin_instance":
        if not inst_id:
            return "Error: 'instance' is required"
        try:
            pin = loader.InstancePin(
                col=int(args["col"]), row=int(args["row"]),
                cols=int(args.get("cols") or 1), rows=int(args.get("rows") or 1),
            )
        except (KeyError, TypeError, ValueError) as e:
            return f"Error: bad pin coordinates: {e}"
        layout = loader.load_layout()
        try:
            loader.validate_pin_for_instance(layout, inst_id, pin)
        except loader.PinValidationError as e:
            return f"Pin rejected: {e}"
        target = next(i for i in layout.instances if i.instance == inst_id)
        target.pin = pin
        loader.save_layout(layout)
        return f"OK: pinned '{inst_id}' at col {pin.col}, row {pin.row}, span {pin.cols}x{pin.rows}."

    if name == "unpin_instance":
        if not inst_id:
            return "Error: 'instance' is required"
        layout = loader.load_layout()
        target = next((i for i in layout.instances if i.instance == inst_id), None)
        if target is None:
            return f"Error: unknown instance: {inst_id}"
        if target.pin is None:
            return f"OK: '{inst_id}' was not pinned (no change)."
        target.pin = None
        loader.save_layout(layout)
        return f"OK: unpinned '{inst_id}'."

    if name == "set_instance_floor":
        if not inst_id:
            return "Error: 'instance' is required"
        layout = loader.load_layout()
        target = next((i for i in layout.instances if i.instance == inst_id), None)
        if target is None:
            return f"Error: unknown instance: {inst_id}"
        if args.get("clear"):
            target.tileflow = None
            loader.save_layout(layout)
            return f"OK: cleared all floors on '{inst_id}'."
        tf = target.tileflow or loader.InstanceTileflow()
        if "locked_size" in args and args["locked_size"]:
            if args["locked_size"] not in ("icon", "small", "medium", "large", "hero"):
                return "Error: locked_size must be one of icon/small/medium/large/hero"
            tf.locked_size = args["locked_size"]
        if "locked_floor" in args and args["locked_floor"]:
            if args["locked_floor"] not in ("dormant", "idle", "active", "focused"):
                return "Error: locked_floor must be one of dormant/idle/active/focused"
            tf.locked_floor = args["locked_floor"]
        if "never_dormant" in args:
            tf.never_dormant = bool(args["never_dormant"])
        target.tileflow = tf
        loader.save_layout(layout)
        floors = tf.model_dump(exclude_none=True, exclude_defaults=True)
        return f"OK: floors on '{inst_id}' now {json.dumps(floors) if floors else '(none)'}."

    if name == "apply_layout_preset":
        pname = Path((args.get("name") or "").strip()).name
        if not pname:
            return "Error: 'name' is required"
        preset_path = _paths.LAYOUT_PRESETS_DIR / f"{pname}.json"
        if not preset_path.is_file():
            avail = sorted(p.stem for p in _paths.LAYOUT_PRESETS_DIR.glob("*.json")) if _paths.LAYOUT_PRESETS_DIR.is_dir() else []
            return f"Error: preset '{pname}' not found. Available: {', '.join(avail) or '(none)'}"
        try:
            candidate = loader.PanelLayout.model_validate(json.loads(preset_path.read_text(encoding="utf-8")))
        except Exception as e:
            return f"Error: preset '{pname}' is not a valid layout sheet: {e}"
        errors = loader.validate_layout_pins(candidate)
        if errors:
            return "Preset rejected by pin validator:\n" + "\n".join(f"- {e}" for e in errors)
        loader.save_layout(candidate)
        return f"OK: applied preset '{pname}' ({len(candidate.instances)} instances)."

    if name == "save_layout_preset":
        pname = Path((args.get("name") or "").strip()).name
        if not pname:
            return "Error: 'name' is required"
        layout = loader.load_layout()
        _paths.LAYOUT_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        preset_path = _paths.LAYOUT_PRESETS_DIR / f"{pname}.json"
        preset_path.write_text(
            json.dumps(layout.model_dump(by_alias=True, exclude_none=True), indent=2),
            encoding="utf-8",
        )
        return f"OK: saved current layout as preset '{pname}'."

    return f"Unknown layout tool: {name}"


def execute_tool(
    name: str,
    args: dict,
    session_id: str,
    project_dir: Path,
    user_name: Optional[str] = None,
) -> str:
    """Unified signature matching game packs. Core tools ignore session_id/user_name."""
    try:
        # Models sometimes send explicit nulls ("path": null) — coerce string
        # args up front so handlers see "" instead of crashing on None.strip().
        args = {k: ("" if v is None else v) for k, v in (args or {}).items()}

        if name == "read_file":
            if not args.get("path"):
                return "Error: Missing required argument 'path'"
            path = (project_dir / args["path"]).resolve()
            if not path.is_relative_to(project_dir.resolve()):
                return "Error: path escapes project directory"
            if not path.exists():
                return f"Error: file not found: {args['path']}"
            all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            total = len(all_lines)
            start = args.get("start_line")
            end = args.get("end_line")
            if start is not None or end is not None:
                s = max(0, (start or 1) - 1)
                e = min(end if end is not None else total, total)
                sliced = all_lines[s:e]
                header = f"[lines {s+1}–{e} of {total} in {args['path']}]"
            else:
                s = 0
                sliced = all_lines
                header = f"[{total} lines in {args['path']}]"
            # `N\t<content>` — tab-separated so the prefix is unambiguously
            # metadata. edit_file strips this defensively if the model copies
            # it into old_string.
            width = len(str(s + len(sliced))) if sliced else 1
            numbered = "\n".join(f"{str(s + i + 1).rjust(width)}\t{ln}" for i, ln in enumerate(sliced))
            content = (
                f"{header}\n"
                f"# Line numbers are prefixed as `N<TAB>`. They are NOT part of the file — "
                f"do NOT include them in edit_file's old_string.\n"
                f"{numbered}"
            )
            if len(content) > MAX_READ_CHARS:
                content = content[:MAX_READ_CHARS] + f"\n\n[truncated: output exceeds {MAX_READ_CHARS} chars — re-read with start_line/end_line]"
            rel = args["path"]
            if rel in read_files:
                content = f"<system>\nWARNING: re-read of '{rel}'. if file unchanged, use prior context.\n</system>\n\n{content}"
            else:
                read_files.append(rel)
            return content

        if name == "list_dir":
            if not args.get("path"):
                return "Error: Missing required argument 'path'"
            path = (project_dir / args["path"]).resolve()
            if not path.is_relative_to(project_dir.resolve()):
                return "Error: path escapes project directory"
            if not path.is_dir():
                return f"Error: not a directory: {args['path']}"
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
            return "\n".join(
                f"{'[dir] ' if e.is_dir() else '[file]'} {e.name}"
                for e in entries
                if not e.name.startswith(".") and e.name not in SKIP_DIRS_HIDDEN
            )

        if name == "roll_dice":
            try:
                sides = int(args.get("sides", 0))
            except (TypeError, ValueError):
                return "Error: 'sides' must be an integer"
            if sides < 2:
                return "Error: 'sides' must be >= 2"
            try:
                count = int(args.get("count", 1) or 1)
            except (TypeError, ValueError):
                return "Error: 'count' must be an integer"
            if count < 1 or count > 100:
                return "Error: 'count' must be between 1 and 100"
            try:
                modifier = int(args.get("modifier", 0) or 0)
            except (TypeError, ValueError):
                return "Error: 'modifier' must be an integer"
            adv = (args.get("advantage") or "none").lower()
            label = (args.get("label") or "").strip()

            if count == 1 and adv in ("advantage", "disadvantage"):
                a, b = random.randint(1, sides), random.randint(1, sides)
                kept = max(a, b) if adv == "advantage" else min(a, b)
                total = kept + modifier
                rolls_str = f"[{a}, {b}] → kept {kept} ({adv})"
                expr = f"1d{sides}{'+' if modifier >= 0 else ''}{modifier or ''} with {adv}"
            else:
                rolls = [random.randint(1, sides) for _ in range(count)]
                total = sum(rolls) + modifier
                rolls_str = "[" + ", ".join(str(r) for r in rolls) + "]"
                mod_str = f"{'+' if modifier >= 0 else ''}{modifier}" if modifier else ""
                expr = f"{count}d{sides}{mod_str}"

            head = f"{label}: " if label else ""
            return f"{head}{expr} = {rolls_str}" + (f" + {modifier}" if modifier and count > 1 else "") + f" → **{total}**"

        if name == "list_manual":
            lines = []
            src = _manual_dir(project_dir)
            if src.is_dir():
                for entry in sorted(src.iterdir(), key=lambda p: p.name.lower()):
                    if entry.suffix.lower() != ".md" or entry.name.lower() == "readme.md":
                        continue
                    try:
                        text = entry.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    sections = [t for t, _ in _manual_sections(text) if t != "_preamble"]
                    hint = f" — sections: {', '.join(sections)}" if sections else ""
                    lines.append(f"- {entry.stem} ({entry.stat().st_size} chars){hint}")
            return "Available manual docs:\n" + "\n".join(lines) if lines else "No manual docs available."

        if name == "load_manual":
            pname = (args.get("name") or "").strip()
            if not pname:
                return "Error: 'name' is required"
            path = _find_manual(pname, project_dir)
            if path is None:
                return f"Error: manual doc '{pname}' not found"
            text = path.read_text(encoding="utf-8", errors="replace")
            section = (args.get("section") or "").strip()
            if not section:
                return f"[manual: {pname}]\n{text}"
            for title, body in _manual_sections(text):
                if title.lower() == section.lower():
                    return f"[manual: {pname} § {title}]\n{body}"
            avail = ", ".join(t for t, _ in _manual_sections(text) if t != "_preamble") or "(none)"
            return f"Error: section '{section}' not found in '{pname}'. Available: {avail}"

        if name == "register_path":
            return _register_path(args, project_dir)

        if name == "layout":
            action = (args.get("action") or "").strip()
            if action in _LAYOUT_ACTIONS:
                return _layout_tool(_LAYOUT_ACTIONS[action], args)
            if action == "state":
                return _state_overlay(args)
            if action == "recompute":
                # server.py's dispatch site sees action=recompute and
                # broadcasts the tileflow_recompute frame.
                return "OK: layout recomputation broadcast queued."
            return (
                f"Error: unknown layout action '{action}'. Valid actions: "
                f"get, panels, state, pin, unpin, floor, recompute, "
                f"preset_apply, preset_save"
            )

        # Compat aliases: pre-consolidation tool names still dispatch (old
        # sessions, curl scripts). The model-facing deck only ships `layout`.
        if name in _LAYOUT_TOOLS:
            return _layout_tool(name, args)
        if name == "recompute_layout":
            return "OK: layout recomputation broadcast queued."
        if name == "set_instance_state":
            return _state_overlay(args)

        if name == "update_task":
            from paths import TASK_FILENAME
            task_path = project_dir / TASK_FILENAME
            content = (args.get("content") or "").strip()
            # Clear paths: explicit `clear: true`, OR content passed but
            # empty. Small local models reach for `content: ""` when asked
            # to clear — honor the intent instead of bouncing them to the
            # read branch. Read = calling with no content key at all.
            if args.get("clear") or ("content" in args and not content):
                task_path.unlink(missing_ok=True)
                return "task.md cleared."
            if content:
                # Small-model JSON double-escaping guard: content arriving
                # with literal \n sequences and no real newlines is an
                # escaping artifact, not intent — normalize it.
                if "\\n" in content and "\n" not in content:
                    content = content.replace("\\n", "\n")
                task_path.write_text(content, encoding="utf-8")
                return f"task.md updated:\n{content}"
            if task_path.is_file():
                return f"Current task.md:\n{task_path.read_text(encoding='utf-8', errors='replace')}"
            return "No task.md found. Call update_task with content to create one."

        return f"Unknown tool: {name}"

    except Exception as e:
        return f"Error: {e}"
