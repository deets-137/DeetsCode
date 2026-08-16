"""
Core tool pack — loaded in every mode.

Kept deliberately small: the tool deck is the model's attention budget, so
every definition here rides in every request. TOOL_DEFINITIONS is the
always-on set: file reading and the task checklist. The coding toolkit lives
in tools/coding.py and only loads for the DeetsCode mode.

Shared state (pending_writes, read_files) lives here even though most of its
consumers are in coding.py, because server.py imports it via `tools` and we
don't want the import path to depend on which mode happens to be loaded.
"""

from pathlib import Path
from typing import Optional

# Shared mutable state used by coding.py (write_file/edit_file)
# and by server.py's auto-apply flow. Kept here to give it a stable import path
# regardless of which tool packs are active.
pending_writes: dict[str, str] = {}
read_files: list[str] = []

# Images produced by view_photo during the current tool call, as OpenAI
# content parts ({"type": "image_url", ...}). server.py drains this right
# after execute_tool() returns and attaches the parts to the tool-result
# message, because a tool can only hand back a str. Gemma 4's chat template
# accepts image parts inside a tool result (verified 2026-08-16 — the model
# described a photo delivered this way), which is what makes the round trip
# work without touching the chat message pipeline.
pending_images: list[dict] = []


def clear_pending_writes():
    pending_writes.clear()


def clear_read_files():
    read_files.clear()


def clear_pending_images():
    pending_images.clear()


# Constants shared with coding.py — kept here so there's one source of truth.
MAX_READ_CHARS = 100_000

# view_photo downscales before encoding. 896 px on the long edge is what the
# vision tower wants (224 px tiles) and keeps a 33 MP camera JPEG from
# becoming a 20 MB base64 blob. Raising this does not buy detail — the image
# token budget, not the pixel count, is what limits what the model sees.
PHOTO_MAX_EDGE = 896
PHOTO_JPEG_QUALITY = 88
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


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
    {
        "type": "function",
        "function": {
            "name": "view_photo",
            "description": (
                "Look at an image file. Use this whenever the user refers to a "
                "photo, picture, screenshot, or image and you need to see it to "
                "answer. The image is returned to you directly — describe what "
                "you actually see, never guess from the filename. Accepts a "
                "path relative to the project or an absolute path anywhere on "
                "disk (photo libraries usually live outside the project)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the image file. Relative to the project, or absolute."},
                },
                "required": ["path"],
            },
        },
    },
]


SKIP_DIRS_HIDDEN = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"}

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

        if name == "view_photo":
            if not args.get("path"):
                return "Error: Missing required argument 'path'"
            raw = str(args["path"]).strip().strip('"').strip("'")
            candidate = Path(raw).expanduser()
            # Absolute paths are allowed on purpose: photo libraries live
            # outside the project (this one is on the Desktop), so the
            # project sandbox that read_file enforces would make the tool
            # useless. This stays defensible because view_photo is read-only
            # and refuses anything that isn't a decodable image.
            path = candidate if candidate.is_absolute() else (project_dir / raw)
            try:
                path = path.resolve()
            except OSError as e:
                return f"Error: bad path: {e}"
            if not path.exists() or not path.is_file():
                return f"Error: file not found: {raw}"
            if path.suffix.lower() not in PHOTO_SUFFIXES:
                return (
                    f"Error: '{path.suffix}' is not a viewable image. "
                    f"Supported: {', '.join(sorted(PHOTO_SUFFIXES))}. "
                    f"Camera raw (.arw/.cr2/.nef) is not supported yet — "
                    f"look for a matching JPG beside it."
                )
            try:
                from PIL import Image, ImageOps
            except ImportError:
                return "Error: Pillow is not installed — run: pip install Pillow"
            import base64
            import io
            try:
                with Image.open(path) as im:
                    # exif_transpose before anything else: a portrait frame
                    # off this camera carries orientation 8, and an
                    # un-rotated image makes the model describe a sideways
                    # scene with total confidence.
                    im = ImageOps.exif_transpose(im)
                    original = im.size
                    im.thumbnail((PHOTO_MAX_EDGE, PHOTO_MAX_EDGE))
                    buf = io.BytesIO()
                    im.convert("RGB").save(buf, "JPEG", quality=PHOTO_JPEG_QUALITY)
            except Exception as e:
                return f"Error: could not read image '{path.name}': {e}"
            data = buf.getvalue()
            pending_images.append({
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(data).decode()},
            })
            return (
                f"[image attached: {path.name} — {original[0]}x{original[1]} "
                f"original, sent at {im.size[0]}x{im.size[1]}]\n"
                f"Full path: {path}\n"
                f"The image follows this text. Describe what you see in it."
            )

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
