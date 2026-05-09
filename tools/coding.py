"""
Coding tool pack — loaded for the `DeetsCode` mode (and its legacy `default`
alias). These are the tools a coding agent needs: search, write/edit with
approval queueing, symbol listing, context tracking, and shell.

Game modes (chess, dnd, ...) deliberately do NOT load this pack. Chess doesn't
need shell or `write_file`; adding them would bloat the schema and invite bugs.
`read_file` lives in tools/core.py so game modes have a narrow escape hatch.
"""

import re
import subprocess
from pathlib import Path
from typing import Optional

from .core import pending_writes, read_files

MAX_SEARCH_MATCHES = 50
SEARCH_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"}

# Telltale sequences of UTF-8 bytes re-interpreted as cp1252 and re-encoded
# as UTF-8 — the classic double-encoding footprint. None of these appear in
# normal source, so a hit means the model almost certainly regurgitated a
# corrupted copy of the file.
_MOJIBAKE_MARKERS = (
    "â€”", "â€“", "â€œ", "â€\x9d", "â€˜", "â€™", "â€¦",
    "âœ“", "âœ•", "âœŽ", "â†º", "â†’", "â–¸", "â–¾", "â—‰", "â—‹",
    "Ã©", "Ã¨", "Ã¢", "Ã´", "Ã§", "Ã±",
)


def _looks_mojibaked(text: str) -> str | None:
    for m in _MOJIBAKE_MARKERS:
        if m in text:
            return m
    return None


def _strip_line_numbers(s: str) -> str:
    """If every non-empty line is prefixed with `N<TAB>` (read_file's format),
    strip it. No-op otherwise so we don't corrupt content that legitimately
    starts with digits + tab."""
    lines = s.split("\n")
    nonblank = [ln for ln in lines if ln.strip()]
    if nonblank and all(re.match(r"^\s*\d+\t", ln) for ln in nonblank):
        return "\n".join(re.sub(r"^\s*\d+\t", "", ln) for ln in lines)
    return s

_SYMBOL_PATTERNS = {
    "python": [
        (re.compile(r"^\s*async\s+def\s+(\w+)"), "async def"),
        (re.compile(r"^\s*def\s+(\w+)"), "def"),
        (re.compile(r"^\s*class\s+(\w+)"), "class"),
    ],
    "js": [
        (re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)"), "function"),
        (re.compile(r"^\s*(?:export\s+)?class\s+(\w+)"), "class"),
        (re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?(?:function|\()"), "const fn"),
    ],
    "css": [
        (re.compile(r"^(\.[\w-]+|#[\w-]+|@[\w-]+|[\w-]+)\s*\{"), "rule"),
    ],
    "markdown": [
        (re.compile(r"^(#+\s+.+)$"), "heading"),
    ],
}

_EXT_LANG = {
    ".py": "python",
    ".js": "js", ".ts": "js", ".jsx": "js", ".tsx": "js", ".mjs": "js",
    ".css": "css",
    ".md": "markdown",
}


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Queue a file write. Changes are held for user approval and not written to disk immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Path relative to project root"},
                    "content": {"type": "string", "description": "Full file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Queue an edit to an existing file by replacing an exact string. old_string must match exactly once in the file. Prefer this over write_file for small changes. Changes are held for user approval. IMPORTANT: when copying text from read_file output, strip the `N<TAB>` line-number prefix from each line — it is metadata, not part of the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":       {"type": "string", "description": "Path relative to project root"},
                    "old_string": {"type": "string", "description": "Exact text to find. Must match exactly once — include surrounding context to disambiguate if needed."},
                    "new_string": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "insert_to_file",
            "description": "Queue an insertion into an existing file WITHOUT replacing anything. Use this when adding a new section, function, theme block, etc. Cleaner than edit_file for pure additions — you don't have to fake 'replace X with X plus more'. Position 'end' (most common) appends; 'start' prepends; 'after'/'before' insert relative to an exact-match anchor string. Changes are held for user approval. IMPORTANT: when copying anchor text from read_file output, strip the `N<TAB>` line-number prefix.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":     {"type": "string", "description": "Path relative to project root"},
                    "content":  {"type": "string", "description": "Text to insert. Include any leading/trailing newlines you want when using 'after'/'before'; 'end'/'start' auto-handle the boundary newline."},
                    "position": {"type": "string", "enum": ["end", "start", "after", "before"], "description": "Where to insert. 'end' = append to end of file; 'start' = prepend; 'after'/'before' = insert relative to anchor."},
                    "anchor":   {"type": "string", "description": "Required for 'after'/'before'. Exact text to anchor against; must match exactly once. Ignored for 'end'/'start'."},
                },
                "required": ["path", "content", "position"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "discard_pending_write",
            "description": "Drop a previously queued write or edit for the given path without touching disk. Use this if you realize a queued change is wrong and you can't repair it with edit_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to project root"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": f"Search project files for a regex pattern. Returns up to {MAX_SEARCH_MATCHES} matches as 'path:line: snippet'. Skips binaries, node_modules, .git, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regex pattern"},
                    "path":    {"type": "string", "description": "Optional subdirectory to limit search (relative to project root). Default: entire project."},
                    "glob":    {"type": "string", "description": "Optional filename glob to filter (e.g. '*.py', '*.js'). Default: all files."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_symbols",
            "description": "List function/class definitions and their line numbers in a source file. Much cheaper than reading the whole file — use to skim a large file before reading the relevant section with read_file(start_line, end_line).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to project root"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_context_files",
            "description": "List all files you have already read in this session. Check this before reading a file to avoid re-reading something already in context.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command in the project directory. Use this to run tests, execute scripts, and verify your own work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                },
                "required": ["command"],
            },
        },
    },
]


def execute_tool(
    name: str,
    args: dict,
    session_id: str,
    project_dir: Path,
    user_name: Optional[str] = None,
) -> str:
    """Coding-agent tools. Ignores session_id/user_name (no per-channel state)."""
    try:
        if name == "write_file":
            if "path" not in args or "content" not in args:
                return "Error: Missing required arguments 'path' or 'content'"
            rel_path = args["path"]
            marker = _looks_mojibaked(args["content"])
            if marker is not None:
                return (
                    f"Error: content looks double-encoded (found {marker!r}). "
                    "Your copy of the file is corrupted. Do NOT re-submit the whole "
                    "file — call read_file again and use edit_file on the specific region."
                )
            pending_writes[rel_path] = args["content"]
            return f"Queued write: {rel_path}"

        if name == "discard_pending_write":
            if "path" not in args:
                return "Error: Missing required argument 'path'"
            rel_path = args["path"]
            if rel_path not in pending_writes:
                return f"Error: no pending write for {rel_path}"
            pending_writes.pop(rel_path, None)
            return f"Discarded pending write: {rel_path}"

        if name == "edit_file":
            if "path" not in args or "old_string" not in args or "new_string" not in args:
                return "Error: Missing required arguments 'path', 'old_string', or 'new_string'"
            rel_path = args["path"]
            old = args["old_string"]
            new = args["new_string"]
            old = _strip_line_numbers(old)
            new = _strip_line_numbers(new)
            if old == new:
                return "Error: old_string and new_string are identical"
            marker = _looks_mojibaked(new)
            if marker is not None:
                return (
                    f"Error: new_string looks double-encoded (found {marker!r}). "
                    "Your copy of the source text is corrupted. Re-read the file and try again."
                )
            if rel_path in pending_writes:
                content = pending_writes[rel_path]
                source = "pending write"
            else:
                path = (project_dir / rel_path).resolve()
                if not path.is_relative_to(project_dir.resolve()):
                    return "Error: path escapes project directory"
                if not path.exists():
                    return f"Error: file not found: {rel_path}"
                content = path.read_text(encoding="utf-8", errors="replace")
                source = "disk"
            count = content.count(old)
            if count == 0:
                return f"Error: old_string not found in {rel_path} (searched {source})"
            if count > 1:
                return f"Error: old_string matches {count} times in {rel_path} — include more surrounding context to make it unique"
            pending_writes[rel_path] = content.replace(old, new, 1)
            return f"Queued edit: {rel_path}"

        if name == "insert_to_file":
            if "path" not in args or "content" not in args or "position" not in args:
                return "Error: Missing required arguments 'path', 'content', or 'position'"
            rel_path = args["path"]
            content_in = _strip_line_numbers(args["content"])
            position = args["position"]
            if position not in ("end", "start", "after", "before"):
                return "Error: position must be one of 'end', 'start', 'after', 'before'"
            if position in ("after", "before") and not args.get("anchor"):
                return f"Error: anchor is required when position is '{position}'"
            marker = _looks_mojibaked(content_in)
            if marker is not None:
                return (
                    f"Error: content looks double-encoded (found {marker!r}). "
                    "Re-read the source and try again."
                )
            if rel_path in pending_writes:
                existing = pending_writes[rel_path]
                source = "pending write"
            else:
                path = (project_dir / rel_path).resolve()
                if not path.is_relative_to(project_dir.resolve()):
                    return "Error: path escapes project directory"
                if not path.exists():
                    return f"Error: file not found: {rel_path}"
                existing = path.read_text(encoding="utf-8", errors="replace")
                source = "disk"
            if position == "end":
                sep = "" if (not existing or existing.endswith("\n")) else "\n"
                new_content = existing + sep + content_in
                if not new_content.endswith("\n"):
                    new_content += "\n"
            elif position == "start":
                sep = "" if content_in.endswith("\n") else "\n"
                new_content = content_in + sep + existing
            else:
                anchor = _strip_line_numbers(args["anchor"])
                count = existing.count(anchor)
                if count == 0:
                    return f"Error: anchor not found in {rel_path} (searched {source})"
                if count > 1:
                    return f"Error: anchor matches {count} times in {rel_path} — include more surrounding context to make it unique"
                if position == "after":
                    new_content = existing.replace(anchor, anchor + content_in, 1)
                else:
                    new_content = existing.replace(anchor, content_in + anchor, 1)
            pending_writes[rel_path] = new_content
            return f"Queued insert ({position}): {rel_path}"

        if name == "search":
            if "pattern" not in args:
                return "Error: Missing required argument 'pattern'"
            pattern = args["pattern"]
            try:
                regex = re.compile(pattern)
            except re.error as e:
                return f"Error: invalid regex: {e}"
            sub = args.get("path", ".") or "."
            search_root = (project_dir / sub).resolve()
            if not search_root.is_relative_to(project_dir.resolve()):
                return "Error: path escapes project directory"
            if not search_root.exists():
                return f"Error: path not found: {sub}"
            glob_pat = args.get("glob") or "*"
            matches = []
            iterator = search_root.rglob(glob_pat) if search_root.is_dir() else [search_root]
            for entry in iterator:
                if not entry.is_file():
                    continue
                rel_parts = entry.relative_to(project_dir).parts
                if any(p in SEARCH_SKIP_DIRS or p.startswith(".") for p in rel_parts):
                    continue
                try:
                    text = entry.read_text(encoding="utf-8", errors="strict")
                except (UnicodeDecodeError, OSError):
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        rel = entry.relative_to(project_dir).as_posix()
                        matches.append(f"{rel}:{i}: {line.strip()[:200]}")
                        if len(matches) >= MAX_SEARCH_MATCHES:
                            break
                if len(matches) >= MAX_SEARCH_MATCHES:
                    break
            if not matches:
                return f"No matches for /{pattern}/"
            header = f"{len(matches)} match{'es' if len(matches) != 1 else ''}"
            if len(matches) >= MAX_SEARCH_MATCHES:
                header += f" (truncated at {MAX_SEARCH_MATCHES})"
            return header + "\n" + "\n".join(matches)

        if name == "list_symbols":
            if "path" not in args:
                return "Error: Missing required argument 'path'"
            rel = args["path"]
            path = (project_dir / rel).resolve()
            if not path.is_relative_to(project_dir.resolve()):
                return "Error: path escapes project directory"
            if not path.is_file():
                return f"Error: file not found: {rel}"
            lang = _EXT_LANG.get(path.suffix.lower(), "markdown")
            patterns = _SYMBOL_PATTERNS.get(lang, _SYMBOL_PATTERNS["markdown"])
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return f"Error reading {rel}: {e}"
            symbols = []
            for i, line in enumerate(text.splitlines(), 1):
                for regex, kind in patterns:
                    m = regex.match(line)
                    if m:
                        symbols.append(f"{i}: {kind} {m.group(1)}")
                        break
            if not symbols:
                return f"No symbols found in {rel} (detected lang: {lang})"
            header = f"{len(symbols)} symbols in {rel} ({lang}):"
            return header + "\n" + "\n".join(symbols)

        if name == "list_context_files":
            if not read_files:
                return "No files read yet this session."
            return "\n".join(read_files)

        if name == "run_command":
            if "command" not in args:
                return "Error: Missing required argument 'command'"
            command = args["command"]
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=str(project_dir),
                    timeout=120,
                )
                output = (result.stdout + result.stderr).strip()
                if result.returncode != 0:
                    output = f"Command failed with exit code {result.returncode}\n" + output
                return output[:5000] if output else "(no output)"
            except Exception as e:
                return f"Error executing command: {e}"

        return f"Unknown tool: {name}"

    except Exception as e:
        return f"Error: {e}"
