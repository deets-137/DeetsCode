"""
Core tool pack — loaded in every mode.

Kept intentionally tiny: 4 tools that any agent (coding, chess, dnd, mafia)
might legitimately want. The rest of the coding-agent toolkit lives in
tools/coding.py and only loads for the DeetsCode mode.

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
                "Read the contents of a file in the project. Available in every "
                "mode as an escape hatch — game modes use it rarely (e.g. inspect "
                "a saved game JSON); coding uses it constantly."
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
            "name": "recompute_layout",
            "description": (
                "Trigger a fresh tileflow pass on the bento — recomputes "
                "scores, re-ranks panels, applies new ordering. Use after a "
                "burst of `set_instance_state` calls if the arrangement "
                "looks stale, or to refresh recency decay (panels that were "
                "active a while ago will demote)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_instance_state",
            "description": (
                "Push a runtime state overlay onto a panel instance — the live "
                "bento rearranges in front of the user. Use sparingly to bubble "
                "something relevant ('I'm about to talk about your YouTube video, "
                "let's surface it') or to tuck something away ('done with the "
                "task list, demote it'). Transient: not persisted to the layout "
                "file. States: 'dormant' (demote to tray icon), 'idle' (small "
                "tile), 'active' (medium tile — passive interest), 'focused' "
                "(hero — full attention). The arrangement responds immediately "
                "via the FLIP runner; peers glide to accommodate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instance": {"type": "string", "description": "Instance id from layout/panel_layout.json (e.g. 'youtube_a', 'tool_log', 'settings'). NOT the panel name."},
                    "state":    {"type": "string", "enum": ["dormant", "idle", "active", "focused"], "description": "Target state."},
                },
                "required": ["instance", "state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_layout",
            "description": (
                "Read the live bento layout: grid dims, regions, and every panel "
                "instance with its current tileflow state, pin, and floors. Call "
                "this before proposing any layout change. Size classes: small=3 "
                "cols, medium=6 cols, large=6 cols x 2 rows, hero=12 cols x 2 rows. "
                "Pins are floors, not absolutes — a focused panel can still grow "
                "past its pinned span."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_panels",
            "description": (
                "List installed panels: name, title, tier, multi_instance, min/"
                "preferred pixel sizes, and default tileflow state. Answers 'is "
                "there a clock panel?' and 'how small can it go?'. Instance ids "
                "in the layout map to these panels via their `panel` field."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pin_instance",
            "description": (
                "Pin a panel instance to a bento grid rectangle (persisted in the "
                "layout file; the UI rearranges live). 1-indexed CSS Grid coords; "
                "(1,1) is top-left. The pin is a floor: state promotion can grow "
                "the panel past it, never shrink it. Validation errors (collision, "
                "out of bounds) come back verbatim — fix the coords and retry."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instance": {"type": "string", "description": "Instance id from the layout (NOT the panel name)."},
                    "col":  {"type": "integer", "description": "Start column, 1-indexed."},
                    "row":  {"type": "integer", "description": "Start row, 1-indexed."},
                    "cols": {"type": "integer", "description": "Column span. Default 1. Valid rows sum to 12: 12, 6+6, 6+3+3, 3+3+3+3."},
                    "rows": {"type": "integer", "description": "Row span. Default 1."},
                },
                "required": ["instance", "col", "row"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unpin_instance",
            "description": "Remove a panel instance's bento pin; it returns to score-ordered auto-flow.",
            "parameters": {
                "type": "object",
                "properties": {
                    "instance": {"type": "string", "description": "Instance id from the layout."},
                },
                "required": ["instance"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_instance_floor",
            "description": (
                "Set persistent floors on a panel instance (the 'settings stays "
                "medium even when dormant' knob). locked_size lower-bounds the "
                "size class and keeps the panel out of the tray; locked_floor "
                "lower-bounds the runtime state; never_dormant clamps dormant to "
                "idle. Omitted fields are left unchanged; pass clear=true to drop "
                "all floors."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instance":      {"type": "string", "description": "Instance id from the layout."},
                    "locked_size":   {"type": "string", "enum": ["icon", "small", "medium", "large", "hero"], "description": "Size class the instance never shrinks below."},
                    "locked_floor":  {"type": "string", "enum": ["dormant", "idle", "active", "focused"], "description": "State the instance never drops below."},
                    "never_dormant": {"type": "boolean", "description": "If true, dormant requests clamp to idle."},
                    "clear":         {"type": "boolean", "description": "Drop all floors on this instance."},
                },
                "required": ["instance"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_layout_preset",
            "description": (
                "Replace the whole layout sheet with a named preset from "
                "layout/presets/<name>.json. The preset passes the same pin "
                "validator as any other layout write. Use save_layout_preset "
                "first to capture the current arrangement under a name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Preset name (filename without .json)."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_layout_preset",
            "description": "Save the current layout sheet as a named preset under layout/presets/ for later apply_layout_preset calls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Preset name (filename without .json). Overwrites an existing preset of the same name."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": "Create or update the task checklist (task.md). Use markdown checkboxes: - [ ] todo, - [/] in-progress, - [x] done. If content is empty, returns the current checklist without modifying it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Full markdown content for task.md. Use - [ ] for todo, - [/] for in-progress, - [x] for done. Leave empty to read the current checklist."},
                },
                "required": [],
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


def _register_path(args: dict) -> str:
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


# ── Stage 3 layout tools ─────────────────────────────────────────────────────
# Thin wrappers over panels/loader.py. Mutations write the layout file
# directly; server.py's tool-dispatch site broadcasts `layout_updated` to
# connected clients after any of these returns "OK: ..." (same pattern as
# set_instance_state). Validator errors return verbatim so the model can
# self-correct on the next call.

_LAYOUT_TOOLS = {
    "get_layout", "get_panels", "pin_instance", "unpin_instance",
    "set_instance_floor", "apply_layout_preset", "save_layout_preset",
}


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
        if name == "read_file":
            if "path" not in args:
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
            if "path" not in args:
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
            pname = args.get("name", "").strip()
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
            return _register_path(args)

        if name in _LAYOUT_TOOLS:
            return _layout_tool(name, args)

        if name == "recompute_layout":
            # Server.py picks up the tool-name in the dispatch site and
            # broadcasts a synthetic frame the client reacts to. Returning
            # a confirmation keeps the tool sync.
            return "OK: layout recomputation broadcast queued."

        if name == "set_instance_state":
            inst = (args.get("instance") or "").strip()
            st = (args.get("state") or "").strip()
            valid = {"dormant", "idle", "active", "focused"}
            if not inst:
                return "Error: 'instance' is required"
            if st not in valid:
                return f"Error: 'state' must be one of {sorted(valid)}"
            # Side effect (WS broadcast) is handled in server.py at the tool
            # dispatch site so this function stays sync. Returning the
            # confirmation is enough for the model's tool-call loop.
            return f"OK: requested state '{st}' for instance '{inst}' (bento updating live)."

        if name == "update_task":
            from paths import TASK_FILENAME
            task_path = project_dir / TASK_FILENAME
            content = args.get("content", "").strip()
            if content:
                task_path.write_text(content, encoding="utf-8")
                return f"task.md updated:\n{content}"
            if task_path.is_file():
                return f"Current task.md:\n{task_path.read_text(encoding='utf-8', errors='replace')}"
            return "No task.md found. Call update_task with content to create one."

        return f"Unknown tool: {name}"

    except Exception as e:
        return f"Error: {e}"
