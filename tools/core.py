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
            "name": "list_packs",
            "description": "List available reference doc packs and their section headings. Packs are domain knowledge (server architecture, UI styling, conventions, etc). Use this to discover what's available, then call load_pack to pull just the section you need into context.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_pack",
            "description": "Read a reference pack, optionally just one section. Prefer loading a single section over the whole pack — packs can be long and full loads eat context. Call list_packs first to see section names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name":    {"type": "string", "description": "Pack name (without .md), e.g. 'server' or 'styling'."},
                    "section": {"type": "string", "description": "Optional level-2 heading title to extract (e.g. 'Agent Loop'). Omit to load the whole pack."},
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

PACKS_GLOBAL_DIR = Path(__file__).parent.parent / "packs"


def _pack_lookup_dirs(project_dir: Path) -> list[tuple[str, Path]]:
    return [("project", project_dir / "manual"), ("global", PACKS_GLOBAL_DIR)]


def _find_pack(name: str, project_dir: Path) -> Optional[Path]:
    safe = Path(name).name
    for _scope, src in _pack_lookup_dirs(project_dir):
        path = src / f"{safe}.md"
        if path.is_file():
            return path
    return None


def _pack_sections(text: str) -> list[tuple[str, str]]:
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


def execute_tool(
    name: str,
    args: dict,
    session_id: str,
    project_dir: Path,
    user_id: Optional[str] = None,
) -> str:
    """Unified signature matching game packs. Core tools ignore session_id/user_id."""
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

        if name == "list_packs":
            lines = []
            seen = set()
            for scope, src in _pack_lookup_dirs(project_dir):
                if not src.is_dir():
                    continue
                for entry in sorted(src.iterdir(), key=lambda p: p.name.lower()):
                    if entry.suffix.lower() != ".md" or entry.name.lower() == "readme.md":
                        continue
                    if entry.stem in seen:
                        continue
                    seen.add(entry.stem)
                    try:
                        text = entry.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    sections = [t for t, _ in _pack_sections(text) if t != "_preamble"]
                    hint = f" — sections: {', '.join(sections)}" if sections else ""
                    lines.append(f"- {entry.stem} ({scope}, {entry.stat().st_size} chars){hint}")
            return "Available packs:\n" + "\n".join(lines) if lines else "No packs available."

        if name == "load_pack":
            pname = args.get("name", "").strip()
            if not pname:
                return "Error: 'name' is required"
            path = _find_pack(pname, project_dir)
            if path is None:
                return f"Error: pack '{pname}' not found"
            text = path.read_text(encoding="utf-8", errors="replace")
            section = (args.get("section") or "").strip()
            if not section:
                return f"[pack: {pname}]\n{text}"
            for title, body in _pack_sections(text):
                if title.lower() == section.lower():
                    return f"[pack: {pname} § {title}]\n{body}"
            avail = ", ".join(t for t, _ in _pack_sections(text) if t != "_preamble") or "(none)"
            return f"Error: section '{section}' not found in '{pname}'. Available: {avail}"

        if name == "update_task":
            task_path = project_dir / "task.md"
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
