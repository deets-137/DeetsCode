import shlex
import subprocess
from pathlib import Path

from config import ALLOWED_COMMANDS

pending_writes: dict[str, str] = {}


def clear_pending_writes():
    pending_writes.clear()


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file in the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to project root"}
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
        if name == "read_file":
            path = (project_dir / args["path"]).resolve()
            if not path.is_relative_to(project_dir.resolve()):
                return "Error: path escapes project directory"
            if not path.exists():
                return f"Error: file not found: {args['path']}"
            return path.read_text(encoding="utf-8", errors="replace")

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
            try:
                parts = shlex.split(command)
            except ValueError as e:
                return f"Error parsing command: {e}"
            if not parts or parts[0] not in ALLOWED_COMMANDS:
                blocked = parts[0] if parts else "(empty)"
                return f"Error: '{blocked}' is not in the allowed command list: {ALLOWED_COMMANDS}"
            result = subprocess.run(
                command,
                shell=True,
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
