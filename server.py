import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path

THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
TOOL_CODE_RE = re.compile(r"<tool_code>.*?</tool_code>", re.DOTALL)


class ThinkStreamFilter:
    """Split a streaming text channel into (visible, thinking) segments.

    Models like Qwen3 emit reasoning as inline `<think>...</think>` blocks in
    regular content. Ollama's OpenAI-compat endpoint doesn't expose these via
    the `reasoning` field, so we parse them here. Contents of think blocks are
    returned via the second tuple element so the caller can forward them as
    `thinking` events instead of dropping them.

    Handles tags split across chunks. Safe to feed partial content; call
    flush() at the end to release any held suffix.
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self):
        self.in_think = False
        self.buf = ""

    def feed(self, chunk: str) -> tuple[str, str]:
        self.buf += chunk
        visible: list[str] = []
        thinking: list[str] = []
        while self.buf:
            if self.in_think:
                i = self.buf.find(self.CLOSE)
                if i < 0:
                    # Stream what's safe, hold a short suffix that might be
                    # the start of a straddling close tag.
                    keep = len(self.CLOSE) - 1
                    if len(self.buf) > keep:
                        thinking.append(self.buf[:-keep])
                        self.buf = self.buf[-keep:]
                    return "".join(visible), "".join(thinking)
                thinking.append(self.buf[:i])
                self.buf = self.buf[i + len(self.CLOSE):]
                self.in_think = False
            else:
                i = self.buf.find(self.OPEN)
                if i < 0:
                    hold = 0
                    for n in range(min(len(self.buf), len(self.OPEN) - 1), 0, -1):
                        if self.OPEN.startswith(self.buf[-n:]):
                            hold = n
                            break
                    if hold:
                        visible.append(self.buf[:-hold])
                        self.buf = self.buf[-hold:]
                    else:
                        visible.append(self.buf)
                        self.buf = ""
                    return "".join(visible), "".join(thinking)
                visible.append(self.buf[:i])
                self.buf = self.buf[i + len(self.OPEN):]
                self.in_think = True
        return "".join(visible), "".join(thinking)

    def flush(self) -> tuple[str, str]:
        # Unterminated think block — release whatever's buffered as thinking.
        if self.in_think:
            out, self.buf = self.buf, ""
            return "", out
        out, self.buf = self.buf, ""
        return out, ""

DEFAULT_PROMPT = """You are working on the project at: {project_dir}

File tree:
{file_tree}

Do exactly what the user asks. Use the provided tools. Keep responses short."""


def strip_think(text: str) -> str:
    text = THINK_BLOCK_RE.sub("", text)
    text = TOOL_CODE_RE.sub("", text)
    return text.strip()


def load_prompt_template(name: str = "DeetsCode") -> str:
    # Backcompat: sessions saved before the coding-mode rename have
    # prompt="default". Transparently redirect to DeetsCode.
    if name == "default":
        name = "DeetsCode"
    prompts_dir = Path(__file__).parent / "prompts"
    candidate = prompts_dir / f"{Path(name).name}.md"
    if candidate.is_file():
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            pass
    # fallback: legacy prompt.md in cwd
    try:
        return Path("prompt.md").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return DEFAULT_PROMPT

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI

from config import HOST, MODEL, OLLAMA_BASE_URL, PORT, TEMPERATURE
from tools import clear_pending_writes, clear_read_files, load_tools, pending_writes
import storage

# Spectators by session_id. Each spectator is a WebSocket that receives a
# read-only copy of every frame emitted for that session. Populated by the
# spectate handshake; drained on disconnect.
_spectators: dict[str, set[WebSocket]] = {}

# Types of frames we do NOT persist to the event log. Mostly ephemeral UI
# bookkeeping that would bloat the table without debug value.
_EVENT_SKIP_TYPES = {"ctx_length", "hello_ack"}

app = FastAPI()
client = AsyncOpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")

project_dir: Path = Path(".").resolve()
packs_dir: Path = Path(__file__).parent / "packs"
auto_apply_enabled: bool = False
current_model: str = MODEL
current_temperature: float = TEMPERATURE
current_context_length: int = 32768
DEFAULT_NUM_CTX = 32768
DEFAULT_NUM_PREDICT = 8192

SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"}

SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSION_SCHEMA = 1


def _session_path(session_id: str) -> Path | None:
    # Only allow simple slugs — no path separators, dots, or shell chars.
    if not session_id or not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", session_id):
        return None
    return SESSIONS_DIR / f"{session_id}.json"


def load_session(session_id: str) -> dict | None:
    path = _session_path(session_id)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != SESSION_SCHEMA:
            return None  # incompatible; ignore rather than crash
        return data
    except (OSError, json.JSONDecodeError):
        return None


def save_session(session_id: str, messages: list, packs: list[str], prompt: str, temperature: float):
    path = _session_path(session_id)
    if path is None:
        return
    SESSIONS_DIR.mkdir(exist_ok=True)
    payload = {
        "schema": SESSION_SCHEMA,
        "messages": messages,
        "packs": packs,
        "prompt": prompt,
        "temperature": temperature,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def delete_session(session_id: str):
    path = _session_path(session_id)
    if path is not None and path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


async def fetch_context_length(model: str) -> int:
    import httpx
    show_url = OLLAMA_BASE_URL.replace("/v1", "") + "/api/show"
    try:
        async with httpx.AsyncClient(timeout=5) as hc:
            r = await hc.post(show_url, json={"name": model})
            info = r.json().get("model_info", {})
            for key in info:
                if "context_length" in key:
                    return int(info[key])
    except Exception:
        pass
    return 131072


def _pack_sources() -> list[tuple[str, Path]]:
    """Return (scope, dir) in lookup order: project manual wins on name collision."""
    return [
        ("project", project_dir / "manual"),
        ("global", packs_dir),
    ]


def list_packs() -> list[dict]:
    out = []
    seen = set()
    for scope, src in _pack_sources():
        if not src.is_dir():
            continue
        for entry in sorted(src.iterdir(), key=lambda p: p.name.lower()):
            if entry.suffix.lower() != ".md" or entry.name.lower() == "readme.md":
                continue
            if entry.stem in seen:
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            out.append({"name": entry.stem, "chars": size, "scope": scope})
            seen.add(entry.stem)
    return out


def load_packs(names: list[str]) -> str:
    """Emit a manifest of selected packs (names + section headings only), not
    their full bodies. The model pulls actual content via the load_pack tool
    when it needs it — keeps packs out of the standing context cost."""
    if not names:
        return ""
    lines = []
    for name in names:
        safe = Path(name).name
        for _scope, src in _pack_sources():
            path = src / f"{safe}.md"
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                break
            sections = [
                ln[3:].strip() for ln in text.splitlines()
                if ln.startswith("## ") and not ln.startswith("### ")
            ]
            hint = f" — sections: {', '.join(sections)}" if sections else ""
            lines.append(f"- {safe}{hint}")
            break
    if not lines:
        return ""
    return (
        "## Reference Documentation (on-demand)\n\n"
        "The following packs are available for this session. They are NOT loaded "
        "in full — call `load_pack(name, section=...)` to pull a specific section "
        "into context when you need it. Prefer a single section over the whole pack.\n\n"
        + "\n".join(lines)
    )


_STEP_RE = re.compile(r"\s*[-*]\s+\[([ /xX])\]\s*(.*)")


def _parse_task_steps(root: Path) -> list[tuple[str, str]]:
    task_path = root / "task.md"
    if not task_path.is_file():
        return []
    try:
        text = task_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    steps = []
    for line in text.splitlines():
        m = _STEP_RE.match(line)
        if m:
            steps.append((m.group(1), m.group(2).strip()))
    return steps


def build_focus_block(root: Path) -> str:
    steps = _parse_task_steps(root)
    if not steps:
        return ""
    total = len(steps)
    done = sum(1 for s, _ in steps if s.lower() == "x")
    in_progress_idx = next((i for i, (s, _) in enumerate(steps) if s == "/"), None)

    if in_progress_idx is not None:
        state = "STEP_IN_PROGRESS"
        step_label = f"{in_progress_idx + 1}/{total}"
        step_text = steps[in_progress_idx][1]
        next_text = steps[in_progress_idx + 1][1] if in_progress_idx + 1 < total else "(none — final step)"
        action = "IF step finished → call update_task (real tool call, not prose) marking this [x] and next [/] | ELSE → continue step work. Do NOT write the plan as visible text."
    else:
        todo_idx = next((i for i, (s, _) in enumerate(steps) if s == " "), None)
        if todo_idx is None:
            return "<system>\nSTATE: ALL_STEPS_COMPLETE\nACTION: emit final reply to user and stop. do not call tools.\n</system>"
        state = "STEP_PENDING_START"
        step_label = f"{todo_idx + 1}/{total}"
        step_text = steps[todo_idx][1]
        next_text = steps[todo_idx + 1][1] if todo_idx + 1 < total else "(none — final step)"
        action = "emit a real update_task tool call marking this step [/] before doing any work. Do NOT narrate the plan in text."

    return (
        "<focus>\n"
        f"STATE: {state}\n"
        f"STEP: {step_label} — {step_text}\n"
        f"NEXT: {next_text}\n"
        f"PROGRESS: {done}/{total} done\n"
        f"ACTION: {action}\n"
        "</focus>"
    )


def build_file_tree(root: Path, indent: int = 0, max_depth: int = 4) -> str:
    if indent >= max_depth:
        return ""
    lines = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
        for entry in entries:
            if entry.name.startswith(".") or entry.name in SKIP_DIRS:
                continue
            prefix = "  " * indent
            if entry.is_dir():
                lines.append(f"{prefix}{entry.name}/")
                subtree = build_file_tree(entry, indent + 1, max_depth)
                if subtree:
                    lines.append(subtree)
            else:
                lines.append(f"{prefix}{entry.name}")
    except PermissionError:
        pass
    return "\n".join(lines)


def build_tree_json(root: Path, depth: int = 0, max_depth: int = 4) -> list:
    if depth >= max_depth:
        return []
    nodes = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
        for entry in entries:
            if entry.name.startswith(".") or entry.name in SKIP_DIRS:
                continue
            if entry.is_dir():
                nodes.append({
                    "name": entry.name,
                    "type": "dir",
                    "children": build_tree_json(entry, depth + 1, max_depth),
                })
            else:
                nodes.append({"name": entry.name, "type": "file"})
    except PermissionError:
        pass
    return nodes


@app.get("/tree")
def get_tree():
    return JSONResponse({"tree": build_tree_json(project_dir), "root": str(project_dir)})


@app.get("/packs")
def get_packs():
    return JSONResponse({"packs": list_packs()})


@app.get("/api/prompts")
def get_prompts():
    prompts_dir = Path(__file__).parent / "prompts"
    names = []
    if prompts_dir.is_dir():
        names = sorted(p.stem for p in prompts_dir.iterdir() if p.suffix == ".md")
    return JSONResponse({"prompts": names})


@app.get("/pending")
def get_pending():
    return JSONResponse({"writes": dict(pending_writes)})


@app.delete("/pending")
def flush_pending():
    count = len(pending_writes)
    clear_pending_writes()
    return JSONResponse({"flushed": count})


async def _agent_loop_impl(ws: WebSocket, user_content: str, messages: list, state: dict, selected_packs: list[str], selected_prompt: str = "DeetsCode", session_id: str | None = None, user_id: str | None = None):
    # Cache the file tree on `state`. Recompute only if invalidated (e.g. after
    # write_file applies). Static over a session — was being re-rendered each
    # turn for no reason.
    tree_text = state.get("file_tree")
    if not tree_text:
        tree_text = build_file_tree(project_dir)
        state["file_tree"] = tree_text
    prompt_template = load_prompt_template(selected_prompt)
    system_prompt = prompt_template.replace("{project_dir}", str(project_dir)).replace("{file_tree}", tree_text)
    pack_block = load_packs(selected_packs)
    if pack_block:
        system_prompt = f"{system_prompt}\n\n{pack_block}"
    loop_messages = [{"role": "system", "content": system_prompt}] + messages
    # Mode-gated tool pack. Each turn reloads because the mode can switch
    # between turns via /mode — cheap enough, keeps the schema in sync.
    tool_defs, execute_tool = load_tools(selected_prompt)
    MAX_ITERATIONS = 25
    iteration = 0

    def _trim_stale_tool_results(msgs: list, keep_recent: int = 3, stub_over: int = 400):
        """Replace `role: tool` message bodies older than the last `keep_recent`
        tool results with a short stub. Models rarely re-use old tool output
        verbatim; keeping it costs tokens on every turn. Small results
        (<= stub_over chars) are left alone — they're cheap and sometimes
        referenced later."""
        tool_idxs = [i for i, m in enumerate(msgs) if m.get("role") == "tool"]
        if len(tool_idxs) <= keep_recent:
            return
        for i in tool_idxs[:-keep_recent]:
            body = msgs[i].get("content") or ""
            if isinstance(body, str) and len(body) > stub_over and not body.startswith("[elided:"):
                msgs[i]["content"] = f"[elided: {len(body)} chars of prior tool output — call the tool again if still relevant]"

    while True:
        iteration += 1
        _trim_stale_tool_results(loop_messages)
        if iteration > MAX_ITERATIONS:
            await ws.send_json({"type": "error", "content": f"Stopped: exceeded {MAX_ITERATIONS} tool-call iterations (likely stuck in a loop)."})
            messages.append({"role": "assistant", "content": "[stopped: iteration cap reached]"})
            break
        # Force a tool call on turn 1 when there's no live plan yet — stops the
        # model from narrating its plan as prose instead of calling update_task.
        # Backs off to "auto" after so the model can pick tools or reply freely.
        # Bypass: short/conversational messages (greetings, trivial questions)
        # would be warped into a forced plan. Heuristic — under 80 chars AND
        # no action verb — skips the force and lets the model just reply.
        has_in_progress = any(s == "/" for s, _ in _parse_task_steps(project_dir))
        _action_verbs = re.compile(r"\b(add|fix|edit|create|build|write|refactor|update|change|make|implement|remove|delete|rename|move|run|test|debug|check|review|install|setup|migrate|deploy|read|find|search|list|show me|investigate|diagnose)\b", re.I)
        _looks_conversational = len(user_content) < 80 and not _action_verbs.search(user_content)
        force_tool = iteration == 1 and not has_in_progress and not _looks_conversational
        stream = await client.chat.completions.create(
            model=current_model,
            messages=loop_messages,
            tools=tool_defs,
            tool_choice="required" if force_tool else "auto",
            stream=True,
            temperature=current_temperature,
            stream_options={"include_usage": True},
            extra_body={
                "options": {
                    "num_ctx": DEFAULT_NUM_CTX,
                    "num_predict": DEFAULT_NUM_PREDICT,
                    "num_gpu": 99,
                    "presence_penalty": 0,
                }
            }
        )
        state["stream"] = stream

        reasoning_buf = ""
        content_buf = ""
        tool_calls_buf: dict[int, dict] = {}
        think_filter = ThinkStreamFilter()

        async for chunk in stream:
            if chunk.usage:
                state["usage_tokens"] = chunk.usage.total_tokens
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            raw_extra = getattr(delta, "model_extra", None) or {}
            reasoning_chunk = raw_extra.get("reasoning") or ""
            if reasoning_chunk:
                reasoning_buf += reasoning_chunk
                await ws.send_json({"type": "thinking", "content": reasoning_chunk})

            if delta.content:
                content_buf += delta.content
                visible, thinking = think_filter.feed(delta.content)
                if thinking:
                    reasoning_buf += thinking
                    await ws.send_json({"type": "thinking", "content": thinking})
                if visible:
                    await ws.send_json({"type": "text", "content": visible})

            if delta.tool_calls:
                for tc_chunk in delta.tool_calls:
                    i = tc_chunk.index
                    if i not in tool_calls_buf:
                        tool_calls_buf[i] = {"id": "", "name": "", "arguments": ""}
                    if tc_chunk.id:
                        tool_calls_buf[i]["id"] += tc_chunk.id
                    if tc_chunk.function.name:
                        tool_calls_buf[i]["name"] += tc_chunk.function.name
                    if tc_chunk.function.arguments:
                        tool_calls_buf[i]["arguments"] += tc_chunk.function.arguments
        tail_visible, tail_thinking = think_filter.flush()
        if tail_thinking:
            reasoning_buf += tail_thinking
            await ws.send_json({"type": "thinking", "content": tail_thinking})
        if tail_visible:
            await ws.send_json({"type": "text", "content": tail_visible})
        if not tool_calls_buf:
            stripped = strip_think(content_buf)
            if stripped:
                messages.append({"role": "assistant", "content": stripped})
            break

        # Ensure all tool calls have an ID (some models/Ollama streams omit them)
        for tc in tool_calls_buf.values():
            if not tc["id"]:
                tc["id"] = "call_" + uuid.uuid4().hex[:8]

        assembled_tool_calls = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            }
            for tc in tool_calls_buf.values()
        ]
        assistant_msg = {
            "role": "assistant",
            "content": content_buf or None,
            "tool_calls": assembled_tool_calls,
        }
        loop_messages.append(assistant_msg)
        messages.append(assistant_msg)

        for tc in tool_calls_buf.values():
            name = tc["name"]
            try:
                args = json.loads(tc["arguments"])
            except json.JSONDecodeError:
                args = {}

            await ws.send_json({"type": "tool_call", "name": name, "args": args})
            result = execute_tool(name, args, session_id or "unknown", project_dir, user_id=user_id)
            await ws.send_json({"type": "tool_result", "name": name, "content": result})

            # Auto-refresh the task panel in the UI when update_task writes
            if name == "update_task" and args.get("content", "").strip():
                await ws.send_json({"type": "task_updated"})

            focus = build_focus_block(project_dir)
            directive = focus if focus else "<system>\nACTION: continue the user's original task. if complete, emit final reply and stop.\n</system>"
            tool_msg_history = {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": f"<tool_result>\n{result}\n</tool_result>",
            }
            loop_messages.append({**tool_msg_history, "content": f"<tool_result>\n{result}\n</tool_result>\n\n{directive}"})
            messages.append(tool_msg_history)

        if pending_writes and auto_apply_enabled:
            applied, rejected = [], []
            root = project_dir.resolve()
            for rel_path, content in pending_writes.items():
                full_path = (project_dir / rel_path).resolve()
                if not full_path.is_relative_to(root):
                    rejected.append(rel_path)
                    continue
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content, encoding="utf-8")
                applied.append(rel_path)
            clear_pending_writes()
            if rejected:
                await ws.send_json({"type": "error", "content": f"Blocked writes escaping project dir: {', '.join(rejected)}"})
            await ws.send_json({"type": "writes_applied", "files": applied})
            loop_messages.append({
                "role": "system",
                "content": f"<system>\nEVENT: queued files written to disk: {', '.join(applied)}\nACTION: proceed with the next step.\n</system>"
            })
            continue

    if pending_writes:
        await ws.send_json({"type": "pending_writes", "writes": dict(pending_writes)})


async def agent_loop(ws: WebSocket, user_content: str, messages: list, selected_packs: list[str], selected_prompt: str = "DeetsCode", session_id: str | None = None, temperature: float | None = None, user_id: str | None = None):
    state: dict = {"usage_tokens": None, "stream": None}
    try:
        await _agent_loop_impl(ws, user_content, messages, state, selected_packs, selected_prompt, session_id, user_id)
    except asyncio.CancelledError:
        # asyncio.cancel() alone doesn't reliably kill the underlying HTTP stream
        # to Ollama — httpx cancellation can be delayed on Windows. Force-close
        # so the Ollama POST actually terminates instead of running to completion.
        s = state.get("stream")
        if s is not None:
            try:
                await s.close()
            except Exception:
                pass
        try:
            await ws.send_json({"type": "info", "content": "Cancelled."})
        except Exception:
            pass
        raise
    finally:
        s = state.get("stream")
        if s is not None:
            try:
                await s.close()
            except Exception:
                pass
        if session_id:
            try:
                save_session(session_id, messages, selected_packs, selected_prompt, temperature if temperature is not None else current_temperature)
            except Exception:
                pass
        try:
            if state["usage_tokens"]:
                await ws.send_json({"type": "usage", "total": state["usage_tokens"], "max": current_context_length})
            await ws.send_json({"type": "done", "model": current_model})
        except Exception:
            pass


@app.get("/models")
async def get_models():
    import urllib.request
    try:
        url = OLLAMA_BASE_URL.replace("/v1", "/api/tags")
        req = urllib.request.urlopen(url, timeout=5)
        data = json.loads(req.read())
        models = [m["name"] for m in data.get("models", [])]
        return JSONResponse({"models": models, "current": current_model})
    except Exception as e:
        return JSONResponse({"models": [], "current": current_model, "error": str(e)})


@app.get("/api/task")
async def get_task():
    task_path = project_dir / "task.md"
    try:
        if task_path.is_file():
            content = task_path.read_text(encoding="utf-8", errors="replace")
            return JSONResponse({"content": content})
        return JSONResponse({"content": ""})
    except Exception as e:
        return JSONResponse({"content": "", "error": str(e)})


@app.get("/api/themes")
async def get_themes():
    """Parse theme.css and return all discovered themes with their swatch colors."""
    import re as _re
    theme_css = Path(__file__).parent / "static" / "theme.css"
    themes = []
    try:
        if not theme_css.is_file():
            return JSONResponse({"themes": themes})
        text = theme_css.read_text(encoding="utf-8", errors="replace")
        # Find each [data-theme="X"] block
        blocks = _re.findall(r'\[data-theme=["\'](\d+)["\']\]\s*\{([^}]+)\}', text)
        for theme_id, body in blocks:
            colors = {}
            for line in body.splitlines():
                m = _re.match(r'\s*--([\w-]+)\s*:\s*([^;]+);', line)
                if m:
                    colors[m.group(1)] = m.group(2).strip()
            swatches = [
                colors.get("canvas", "#888"),
                colors.get("canvas-blob-1", "#888"),
                colors.get("canvas-blob-2", "#888"),
                colors.get("response-text", "#888"),
            ]
            # Derive a name from the dominant color feel
            themes.append({"id": theme_id, "swatches": swatches})
        return JSONResponse({"themes": themes})
    except Exception as e:
        return JSONResponse({"themes": [], "error": str(e)})


@app.get("/api/events/sessions")
async def list_event_sessions_endpoint(limit: int = 50):
    return JSONResponse({"sessions": storage.list_event_sessions(limit=limit)})


@app.get("/api/events")
async def query_events_endpoint(
    session_id: str | None = None,
    since_id: int = 0,
    type: str | None = None,
    limit: int = 500,
):
    types = [t for t in type.split(",")] if type else None
    rows = storage.query_events(session_id=session_id, since_id=since_id, types=types, limit=limit)
    return JSONResponse({"events": rows})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global current_model, auto_apply_enabled, current_temperature, current_context_length
    await ws.accept()
    messages: list[dict] = []
    current_task: asyncio.Task | None = None
    selected_packs: list[str] = []
    selected_prompt: str = "DeetsCode"
    session_id: str | None = None
    spectating: str | None = None  # session_id this ws is subscribed to (read-only mode)

    # Wrap ws.send_json so every outbound frame is recorded to the event log
    # and fanned out to any spectators attached to this session. One hook
    # covers all ~30 emit sites.
    _original_send_json = ws.send_json
    async def _traced_send_json(payload: dict):
        t = payload.get("type") if isinstance(payload, dict) else None
        if session_id and t and t not in _EVENT_SKIP_TYPES:
            try:
                storage.record_event(session_id, t, payload)
            except Exception:
                pass
            for spec in list(_spectators.get(session_id, ())):
                try:
                    await spec.send_json({"type": "event", "event": {"type": t, "payload": payload}})
                except Exception:
                    _spectators.get(session_id, set()).discard(spec)
        await _original_send_json(payload)
    ws.send_json = _traced_send_json

    # Emit ctx length on connect so the bar isn't stuck on the HTML fallback.
    try:
        current_context_length = await fetch_context_length(current_model)
        await ws.send_json({"type": "ctx_length", "max": current_context_length})
    except Exception:
        pass

    try:
        while True:
            try:
                data = await ws.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception as e:
                await ws.send_json({"type": "error", "content": f"Malformed message: {e}"})
                continue

            if data["type"] == "hello":
                sid = data.get("session_id")
                if not isinstance(sid, str) or _session_path(sid) is None:
                    await ws.send_json({"type": "error", "content": f"Invalid session_id: {sid!r}"})
                    continue
                session_id = sid
                loaded = load_session(session_id)
                if loaded:
                    messages = list(loaded.get("messages") or [])
                    selected_packs = list(loaded.get("packs") or [])
                    selected_prompt = loaded.get("prompt") or "DeetsCode"
                    # Session-file backcompat: old sessions saved mode="default".
                    if selected_prompt == "default":
                        selected_prompt = "DeetsCode"
                    try:
                        current_temperature = float(loaded.get("temperature", current_temperature))
                    except (TypeError, ValueError):
                        pass
                    await ws.send_json({"type": "hello_ack", "restored": True, "messages": len(messages), "prompt": selected_prompt})
                else:
                    await ws.send_json({"type": "hello_ack", "restored": False, "messages": 0, "prompt": selected_prompt})
                continue

            if data["type"] == "spectate":
                # Read-only attach to another session's frames. Replays event
                # history, then subscribes for live emissions via _spectators.
                target = data.get("session_id")
                if not isinstance(target, str) or not target:
                    await _original_send_json({"type": "error", "content": "spectate: session_id required"})
                    continue
                since = int(data.get("since_id") or 0)
                # Detach from a previous target if re-subscribing.
                if spectating and spectating in _spectators:
                    _spectators[spectating].discard(ws)
                spectating = target
                _spectators.setdefault(spectating, set()).add(ws)
                # Replay history in batches so huge sessions don't block the loop.
                last_id = since
                while True:
                    batch = storage.query_events(session_id=target, since_id=last_id, limit=500)
                    if not batch:
                        break
                    for ev in batch:
                        await _original_send_json({"type": "event", "event": {
                            "id": ev["id"], "ts": ev["ts"], "type": ev["type"], "payload": ev["payload"],
                        }})
                        last_id = ev["id"]
                    if len(batch) < 500:
                        break
                await _original_send_json({"type": "spectate_ack", "session_id": target, "last_id": last_id})
                continue

            if data["type"] == "unspectate":
                if spectating and spectating in _spectators:
                    _spectators[spectating].discard(ws)
                spectating = None
                await _original_send_json({"type": "spectate_ack", "session_id": None, "last_id": 0})
                continue

            if data["type"] == "set_dir":
                global project_dir
                new_dir = Path(data["path"]).expanduser().resolve()
                if not new_dir.is_dir():
                    await ws.send_json({"type": "error", "content": f"Directory not found: {new_dir}"})
                    continue
                project_dir = new_dir
                messages.clear()
                clear_pending_writes()
                clear_read_files()
                if session_id:
                    delete_session(session_id)
                await ws.send_json({"type": "info", "content": f"Project set to: {project_dir}", "project": str(project_dir)})
                continue

            if data["type"] == "cancel":
                if current_task and not current_task.done():
                    current_task.cancel()
                continue

            if data["type"] == "slash":
                tool = data.get("tool")
                args = data.get("args") or {}
                SLASH_TOOLS = {"read_file", "search", "list_dir", "list_symbols"}
                if tool not in SLASH_TOOLS:
                    await ws.send_json({"type": "error", "content": f"Slash tool not allowed: {tool}"})
                    continue
                await ws.send_json({"type": "tool_call", "name": tool, "args": args})
                # Slash tools are whitelisted to core read-only ops; load core-only
                # regardless of the session's game mode so we don't surprise-invoke
                # a chess tool from the web UI slash menu.
                _, _slash_execute = load_tools("DeetsCode")
                result = _slash_execute(tool, args, session_id or "unknown", project_dir, user_id=None)
                await ws.send_json({"type": "tool_result", "name": tool, "content": result})
                continue

            if data["type"] == "compact":
                if not messages:
                    await ws.send_json({"type": "info", "content": "Nothing to compact."})
                    continue
                if current_task and not current_task.done():
                    current_task.cancel()
                    try:
                        await current_task
                    except (asyncio.CancelledError, Exception):
                        pass
                await ws.send_json({"type": "info", "content": "Compacting conversation..."})
                try:
                    resp = await client.chat.completions.create(
                        model=current_model,
                        messages=messages + [{
                            "role": "user",
                            "content": "Summarize our conversation above in 5-10 concise bullets. Focus on: user intent, decisions made, files changed or in progress, and any open questions. Output only the bullets, no preamble.",
                        }],
                        temperature=0.1,
                        stream=False,
                    )
                    summary = resp.choices[0].message.content or "(empty summary)"
                    summary = strip_think(summary)
                    prior = len(messages)
                    messages.clear()
                    messages.append({"role": "user", "content": "Summary of prior conversation:"})
                    messages.append({"role": "assistant", "content": summary})
                    await ws.send_json({"type": "compacted", "prior": prior, "summary": summary})
                except Exception as e:
                    await ws.send_json({"type": "error", "content": f"Compact failed: {e}"})
                continue

            if data["type"] == "set_packs":
                incoming = data.get("names", [])
                if isinstance(incoming, list):
                    selected_packs = [str(n) for n in incoming if isinstance(n, str)]
                continue

            if data["type"] == "set_prompt":
                new_prompt = data.get("prompt", "DeetsCode")
                if new_prompt == "default":
                    new_prompt = "DeetsCode"
                # On mode switch, scrub tool artifacts from history — the new
                # mode's tool schema won't match the old mode's recorded calls,
                # and stale tool output (chess boards, read_file dumps) just
                # confuses the model. User/assistant text turns are preserved.
                if new_prompt != selected_prompt and messages:
                    scrubbed = []
                    for m in messages:
                        role = m.get("role")
                        if role == "tool":
                            continue
                        if role == "assistant" and m.get("tool_calls"):
                            m = {k: v for k, v in m.items() if k != "tool_calls"}
                            if not m.get("content"):
                                continue
                        scrubbed.append(m)
                    messages[:] = scrubbed
                selected_prompt = new_prompt
                continue

            if data["type"] == "reset":
                if current_task and not current_task.done():
                    current_task.cancel()
                    try:
                        await asyncio.wait_for(current_task, timeout=5.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                        pass
                messages.clear()
                clear_pending_writes()
                clear_read_files()
                if session_id:
                    delete_session(session_id)
                await ws.send_json({"type": "reset_complete"})
                continue

            if data["type"] == "apply_writes":
                applied, rejected = [], []
                root = project_dir.resolve()
                for rel_path, content in pending_writes.items():
                    full_path = (project_dir / rel_path).resolve()
                    if not full_path.is_relative_to(root):
                        rejected.append(rel_path)
                        continue
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(content, encoding="utf-8")
                    applied.append(rel_path)
                clear_pending_writes()
                await ws.send_json({"type": "writes_applied", "files": applied})
                if rejected:
                    await ws.send_json({"type": "error", "content": f"Blocked writes escaping project dir: {', '.join(rejected)}"})
                continue

            if data["type"] == "reject_writes":
                clear_pending_writes()
                await ws.send_json({"type": "writes_rejected"})
                continue

            if data["type"] == "set_auto_apply":
                auto_apply_enabled = bool(data.get("enabled", False))
                continue

            if data["type"] == "set_model":
                new_model = data.get("model")
                if new_model:
                    current_model = new_model
                    current_context_length = await fetch_context_length(current_model)
                    await ws.send_json({"type": "info", "content": f"Switched model to {current_model} (ctx: {current_context_length:,})"})
                    await ws.send_json({"type": "ctx_length", "max": current_context_length})
                continue

            if data["type"] == "set_temperature":
                try:
                    t = float(data.get("temperature"))
                except (TypeError, ValueError):
                    continue
                current_temperature = max(0.0, min(2.0, t))
                continue

            if data["type"] == "shutdown":
                # Hard exit — bypasses uvicorn reloader and finally blocks.
                # That's the point: this fires when the server is wedged.
                try:
                    await ws.send_json({"type": "info", "content": "Harness shutting down."})
                    await ws.close()
                except Exception:
                    pass
                os._exit(0)

            if data["type"] == "message":
                if current_task and not current_task.done():
                    current_task.cancel()

                user_id: str | None = None
                try:
                    user_payload = json.loads(data["content"])
                    user_name = user_payload["name"]
                    user_text = user_payload["text"]
                    # Discord IDs are ints; normalize to string for tool-side compare.
                    raw_id = user_payload.get("id")
                    user_id = str(raw_id) if raw_id is not None else None
                except Exception:
                    user_name = "User"
                    user_text = data["content"]

                if selected_prompt == "dnd":
                    dnd_dir = project_dir / "dnd"
                    world_state_path = dnd_dir / "campaign_state.json"
                    # Back-compat: migrate an old root-level campaign_state.json.
                    legacy = project_dir / "campaign_state.json"
                    if not world_state_path.exists() and legacy.exists():
                        dnd_dir.mkdir(exist_ok=True)
                        world_state_path.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
                    world_state = "{}"
                    if world_state_path.exists():
                        world_state = world_state_path.read_text(encoding="utf-8")

                    for m in messages:
                        if m.get("role") == "user" and isinstance(m.get("content"), str) and "<current_action>" in m["content"]:
                            m["content"] = m["content"].replace("<current_action>", "<prior_action>").replace("</current_action>", "</prior_action>")

                    user_content = (
                        f"<world_state>\n{world_state}\n</world_state>\n\n"
                        f"<current_action>\nPlayer: {user_name}\nAction: {user_text}\n</current_action>\n\n"
                        f"<dm_instructions>\n"
                        f"- You are the Dungeon Master.\n"
                        f"- Use the current_action to progress the story.\n"
                        f"- If stats change, use write_file to update campaign_state.json.\n"
                        f"- Maintain the 'vibe' of the current world_state.\n"
                        f"</dm_instructions>"
                    )
                else:
                    user_content = user_text

                messages.append({"role": "user", "content": user_content})
                current_task = asyncio.create_task(agent_loop(ws, user_text, messages, list(selected_packs), selected_prompt, session_id, current_temperature, user_id))

    except WebSocketDisconnect:
        if current_task and not current_task.done():
            current_task.cancel()
        clear_pending_writes()
        clear_read_files()
    except Exception as e:
        import traceback
        traceback.print_exc()
        if current_task and not current_task.done():
            current_task.cancel()
        try:
            await ws.send_json({"type": "error", "content": f"Server error: {e}"})
            await ws.send_json({"type": "done", "model": current_model})
        except Exception:
            pass
        clear_pending_writes()
        clear_read_files()
    finally:
        if spectating and spectating in _spectators:
            _spectators[spectating].discard(ws)


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        project_dir = Path(sys.argv[1]).expanduser().resolve()
        print(f"Project dir: {project_dir}")
    uvicorn.run(app, host=HOST, port=PORT, reload=False)
