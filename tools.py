import re
import shlex
import subprocess
from pathlib import Path

pending_writes: dict[str, str] = {}
read_files: list[str] = []

MAX_READ_CHARS = 100_000
MAX_SEARCH_MATCHES = 50
SEARCH_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"}

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


def clear_pending_writes():
    pending_writes.clear()


def clear_read_files():
    read_files.clear()


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file in the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to project root"},
                    "start_line": {"type": "integer", "description": "First line to read (1-indexed, inclusive). Omit to read from the beginning."},
                    "end_line": {"type": "integer", "description": "Last line to read (inclusive). Omit to read to the end of the file."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Queue a file write. Changes are held for user approval and not written to disk immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to project root"},
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
            "description": "Queue an edit to an existing file by replacing an exact string. old_string must match exactly once in the file. Prefer this over write_file for small changes. Changes are held for user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to project root"},
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
            "name": "search",
            "description": f"Search project files for a regex pattern. Returns up to {MAX_SEARCH_MATCHES} matches as 'path:line: snippet'. Skips binaries, node_modules, .git, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regex pattern"},
                    "path": {"type": "string", "description": "Optional subdirectory to limit search (relative to project root). Default: entire project."},
                    "glob": {"type": "string", "description": "Optional filename glob to filter (e.g. '*.py', '*.js'). Default: all files."},
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
            "name": "list_dir",
            "description": "List the contents of a directory in the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to project root. Use '.' for the root.",
                    }
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
                    "command": {"type": "string", "description": "Shell command to run"}
                },
                "required": ["command"],
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
                    "content": {
                        "type": "string",
                        "description": "Full markdown content for task.md. Use - [ ] for todo, - [/] for in-progress, - [x] for done. Leave empty to read the current checklist.",
                    }
                },
                "required": [],
            },
        },
    },
]


def execute_tool(name: str, args: dict, project_dir: Path) -> str:
    try:
        if name == "list_context_files":
            if not read_files:
                return "No files read yet this session."
            return "\n".join(read_files)

        if name == "read_file":
            if "path" not in args:
                return "Error: Missing required argument 'path'"
            path = (project_dir / args["path"]).resolve()
            if not path.is_relative_to(project_dir.resolve()):
                return "Error: path escapes project directory"
            if not path.exists():
                return f"Error: file not found: {args['path']}"
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            start = args.get("start_line")
            end = args.get("end_line")
            if start is not None or end is not None:
                s = max(0, (start or 1) - 1)
                e = end if end is not None else len(lines)
                lines = lines[s:e]
                content = "\n".join(lines)
                content = f"[lines {s+1}–{e} of {args['path']}]\n{content}"
            else:
                content = "\n".join(lines)
            if len(content) > MAX_READ_CHARS:
                content = content[:MAX_READ_CHARS] + f"\n\n[truncated: file exceeds {MAX_READ_CHARS} chars — re-read with start_line/end_line]"
            rel = args["path"]
            if rel not in read_files:
                read_files.append(rel)
            return content

        elif name == "write_file":
            if "path" not in args or "content" not in args:
                return "Error: Missing required arguments 'path' or 'content'"
            rel_path = args["path"]
            pending_writes[rel_path] = args["content"]
            return f"Queued write: {rel_path}"

        elif name == "edit_file":
            if "path" not in args or "old_string" not in args or "new_string" not in args:
                return "Error: Missing required arguments 'path', 'old_string', or 'new_string'"
            rel_path = args["path"]
            old = args["old_string"]
            new = args["new_string"]
            if old == new:
                return "Error: old_string and new_string are identical"
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

        elif name == "search":
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

        elif name == "list_symbols":
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

        elif name == "list_dir":
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
                if not e.name.startswith(".")
            )

        elif name == "run_command":
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
                    timeout=30,
                )
                output = (result.stdout + result.stderr).strip()
                if result.returncode != 0:
                    output = f"Command failed with exit code {result.returncode}\n" + output
                return output[:5000] if output else "(no output)"
            except Exception as e:
                return f"Error executing command: {e}"

        elif name == "update_task":
            task_path = project_dir / "task.md"
            content = args.get("content", "").strip()
            if content:
                task_path.write_text(content, encoding="utf-8")
                return f"task.md updated:\n{content}"
            else:
                if task_path.is_file():
                    return f"Current task.md:\n{task_path.read_text(encoding='utf-8', errors='replace')}"
                return "No task.md found. Call update_task with content to create one."

        return f"Unknown tool: {name}"

    except Exception as e:
        return f"Error: {e}"
