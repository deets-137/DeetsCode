import shlex
import subprocess
from pathlib import Path

from config import ALLOWED_COMMANDS

pending_writes: dict[str, str] = {}
read_files: list[str] = []

MAX_READ_CHARS = 100_000


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
            "name": "run_bash",
            "description": f"Run a shell command in the project directory. Allowed commands: {', '.join(ALLOWED_COMMANDS)}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"}
                },
                "required": ["command"],
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
            rel_path = args["path"]
            pending_writes[rel_path] = args["content"]
            return f"Queued write: {rel_path}"

        elif name == "list_dir":
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

        elif name == "run_bash":
            command = args["command"]
            if any(ch in command for ch in ";&|`$><\n"):
                return "Error: shell metacharacters (; & | ` $ > < newline) are not allowed"
            try:
                parts = shlex.split(command)
            except ValueError as e:
                return f"Error parsing command: {e}"
            if not parts or parts[0] not in ALLOWED_COMMANDS:
                blocked = parts[0] if parts else "(empty)"
                return f"Error: '{blocked}' is not in the allowed command list: {ALLOWED_COMMANDS}"
            result = subprocess.run(
                parts,
                shell=False,
                capture_output=True,
                text=True,
                cwd=str(project_dir),
                timeout=30,
            )
            output = (result.stdout + result.stderr).strip()
            return output[:5000] if output else "(no output)"

        return f"Unknown tool: {name}"

    except Exception as e:
        return f"Error: {e}"
