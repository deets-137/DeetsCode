import asyncio
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Optional

THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
TOOL_CODE_RE = re.compile(r"<tool_code>.*?</tool_code>", re.DOTALL)
SYSTEM_BLOCK_RE = re.compile(r"<system>.*?</system>", re.DOTALL)


class ThinkStreamFilter:
    """Split a streaming text channel into (visible, thinking) segments.

    Models like Qwen3 emit reasoning as inline `<think>...</think>` blocks in
    regular content. Ollama's OpenAI-compat endpoint doesn't expose these via
    the `reasoning` field, so we parse them here. Contents of hidden blocks are
    returned via the second tuple element so the caller can forward them as
    `thinking` events instead of dropping them.

    `<system>...</system>` blocks are hidden the same way: the agent loop
    injects system directives after tool results, and models sometimes echo
    them back verbatim — those echoes must never reach the visible channel
    (see friction.md).

    Handles tags split across chunks. Safe to feed partial content; call
    flush() at the end to release any held suffix.
    """

    HIDDEN_TAGS = [("<think>", "</think>"), ("<system>", "</system>")]

    def __init__(self):
        self.close = None  # closing tag of the hidden block we're inside, or None
        self.buf = ""

    def feed(self, chunk: str) -> tuple[str, str]:
        self.buf += chunk
        visible: list[str] = []
        thinking: list[str] = []
        while self.buf:
            if self.close:
                i = self.buf.find(self.close)
                if i < 0:
                    # Stream what's safe, hold a short suffix that might be
                    # the start of a straddling close tag.
                    keep = len(self.close) - 1
                    if len(self.buf) > keep:
                        thinking.append(self.buf[:-keep])
                        self.buf = self.buf[-keep:]
                    return "".join(visible), "".join(thinking)
                thinking.append(self.buf[:i])
                self.buf = self.buf[i + len(self.close):]
                self.close = None
            else:
                # Earliest open tag of any hidden pair wins.
                first_i = -1
                first_open, first_close = "", ""
                for open_tag, close_tag in self.HIDDEN_TAGS:
                    i = self.buf.find(open_tag)
                    if i >= 0 and (first_i < 0 or i < first_i):
                        first_i, first_open, first_close = i, open_tag, close_tag
                if first_i < 0:
                    # Hold the longest suffix that could be the start of a
                    # straddling open tag.
                    hold = 0
                    for open_tag, _ in self.HIDDEN_TAGS:
                        for n in range(min(len(self.buf), len(open_tag) - 1), 0, -1):
                            if open_tag.startswith(self.buf[-n:]):
                                hold = max(hold, n)
                                break
                    if hold:
                        visible.append(self.buf[:-hold])
                        self.buf = self.buf[-hold:]
                    else:
                        visible.append(self.buf)
                        self.buf = ""
                    return "".join(visible), "".join(thinking)
                visible.append(self.buf[:first_i])
                self.buf = self.buf[first_i + len(first_open):]
                self.close = first_close
        return "".join(visible), "".join(thinking)

    def flush(self) -> tuple[str, str]:
        # Unterminated hidden block — release whatever's buffered as thinking.
        if self.close:
            out, self.buf = self.buf, ""
            self.close = None
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
    text = SYSTEM_BLOCK_RE.sub("", text)
    return text.strip()


def load_prompt_template(name: str = "DeetsCode") -> str:
    # Backcompat: sessions saved before the coding-mode rename have
    # prompt="default". Transparently redirect to DeetsCode.
    if name == "default":
        name = "DeetsCode"
    candidate = paths.PROMPTS_DIR / f"{Path(name).name}.md"
    if candidate.is_file():
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            pass
    return DEFAULT_PROMPT

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI

def _list_ollama_models(base_url: str) -> list[str] | None:
    """Query Ollama for installed model names. Returns None if Ollama is
    unreachable (so callers can distinguish 'not running' from 'running but
    empty'). Uses stdlib urllib to avoid pulling in httpx this early."""
    import json as _json
    import urllib.request
    tags_url = base_url.replace("/v1", "") + "/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=2) as r:
            data = _json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return None


_config_path = Path(__file__).parent / "config.py"
if not _config_path.exists():
    _example = _config_path.with_name("config.example.py")
    # Read example to learn the default OLLAMA_BASE_URL, then try to pick a
    # real installed model so a fresh device works without manual edits.
    example_src = _example.read_text(encoding="utf-8")
    example_url = "http://localhost:11434/v1"
    for line in example_src.splitlines():
        if line.startswith("OLLAMA_BASE_URL"):
            example_url = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
    installed = _list_ollama_models(example_url)
    if installed:
        picked = installed[0]
        new_src = re.sub(r'MODEL\s*=\s*"[^"]*"', f'MODEL = "{picked}"', example_src, count=1)
        _config_path.write_text(new_src, encoding="utf-8")
        print(f"[setup] created config.py with MODEL=\"{picked}\" (auto-picked from Ollama)")
    else:
        shutil.copyfile(_example, _config_path)
        print(f"[setup] created config.py from {_example.name} — Ollama not reachable, edit MODEL by hand")

from config import HOST, MODEL, OLLAMA_BASE_URL, PORT, TEMPERATURE

# Preflight: warn loudly if the configured model isn't installed. Don't crash —
# the UI's model picker can override via ACTIVE_MODEL_FILE.
_installed = _list_ollama_models(OLLAMA_BASE_URL)
if _installed is None:
    print(f"[preflight] WARNING: Ollama not reachable at {OLLAMA_BASE_URL} — start it with `ollama serve`")
elif MODEL not in _installed:
    print(f"[preflight] WARNING: MODEL=\"{MODEL}\" not installed. Available: {_installed or '[]'}")
    print(f"[preflight] fix: edit config.py, or run `ollama pull {MODEL}`")
from tools import clear_pending_writes, clear_read_files, load_tools, pending_writes
import storage
import paths

# Spectators by session_id. Each spectator is a WebSocket that receives a
# read-only copy of every frame emitted for that session. Populated by the
# spectate handshake; drained on disconnect.
_spectators: dict[str, set[WebSocket]] = {}

# Every connected WS (one per browser tab). Tileflow uses this to push live
# panel-state overlay frames to all clients regardless of session. This is a
# dev-toy convenience — multi-user installs would scope by session/user.
_panel_ws: set[WebSocket] = set()

# Runtime overlay: last server-pushed tileflow state per instance. Cleared on
# process restart by design — the persisted `panel_layout.json` is the floor;
# overlay is transient "right now" intent (model just bubbled this, video
# just started playing, etc.). Sent to newly connected clients on hello so a
# tab refresh doesn't drop the current arrangement. The dict itself lives in
# panels/loader.py (`tileflow_overlay`) so tools/core.py's get_layout and the
# prompt layout descriptor can read it without importing server.


async def _broadcast_panel_frame(frame: dict) -> int:
    """Fan a JSON frame out to every connected panel client. Returns the
    number of sockets reached (best-effort — dead sockets are discarded)."""
    delivered = 0
    for sock in list(_panel_ws):
        try:
            await sock.send_json(frame)
            delivered += 1
        except Exception:
            _panel_ws.discard(sock)
    return delivered


async def broadcast_tileflow_state(instance: str, state: str) -> int:
    """Push a tileflow state change to every connected panel client. Updates
    the loader's `tileflow_overlay` so future connects can replay current
    state."""
    _panel_loader.tileflow_overlay[instance] = state
    return await _broadcast_panel_frame(
        {"type": "tileflow_state", "instance": instance, "state": state}
    )


async def broadcast_layout_updated() -> int:
    """Tell every client the persisted layout sheet changed (pin/floor edits,
    preset applied, app instances launched). Clients re-fetch /api/layout and
    reconcile — see panel-shell.js `layout_updated` subscriber."""
    return await _broadcast_panel_frame({"type": "layout_updated"})


async def broadcast_app_event(evt: dict) -> int:
    """Fan an app-scoped event (queued via harness_ctx.app_event) out to all
    clients. The front-end dispatcher matches on (app_id, app_instance,
    event_name) — see harness.app.subscribe in panel-shell.js. Envelope key
    is `type` like every other frame (app_harness.md §7 said `kind`; the
    codebase convention won)."""
    return await _broadcast_panel_frame({
        "type": "app_event",
        "app_id": evt.get("app_id"),
        "app_instance": evt.get("app_instance"),
        "event_name": evt.get("event_name"),
        "payload": evt.get("payload") or {},
    })

# Remote-control queues by session_id. Each live session (hello'd) owns one
# asyncio.Queue; any OTHER websocket may enqueue a synthetic control frame
# (e.g. {"type": "reset"}) that the owning session's receive loop will pick
# up and dispatch through the normal handler path. Enables the web UI to
# fire reset/compact/cancel/set_prompt on a Discord bot session.
_session_control: dict[str, "asyncio.Queue[dict]"] = {}

# Actions any caller is allowed to forward. Kept narrow on purpose — anything
# that touches the filesystem or starts a turn stays local-only.
_REMOTE_CONTROL_ACTIONS = {"reset", "compact", "cancel", "set_prompt"}


# ─── Cross-session control dispatcher ────────────────────────────────────────
# Shared by: the `remote_control` WS handler on this socket, the HTTP
# `/api/session/{sid}/control` endpoint, and any future panel/integration
# that wants to drive another session. Keep all validation + translation in
# here so every caller gets identical semantics.

class RemoteControlError(Exception):
    """Raised when a remote-control request is invalid or not dispatchable."""


def enqueue_session_control(target_session_id: str, action: str, **extra) -> str:
    """Push a synthetic control frame into `target_session_id`'s receive loop.

    Returns a short human message describing what happened. Raises
    RemoteControlError on bad inputs or if the target isn't currently live.

    Example: enqueue_session_control("discord-123", "set_prompt", prompt="chess")
    """
    if not isinstance(target_session_id, str) or not target_session_id:
        raise RemoteControlError("target_session_id required")
    if action not in _REMOTE_CONTROL_ACTIONS:
        raise RemoteControlError(f"action '{action}' not allowed")
    q = _session_control.get(target_session_id)
    if q is None:
        raise RemoteControlError(
            f"session '{target_session_id}' is not live (only online sessions accept controls)"
        )
    frame: dict = {"type": action}
    if action == "set_prompt":
        frame["prompt"] = extra.get("prompt") or "DeetsCode"
    q.put_nowait(frame)
    return f"→ sent `{action}` to `{target_session_id}`"


def list_live_sessions() -> list[str]:
    """Session ids that currently have a registered control queue (i.e.
    are connected and past hello). Useful for inventory panels."""
    return list(_session_control.keys())

# Types of frames we do NOT persist to the event log. Mostly ephemeral UI
# bookkeeping that would bloat the table without debug value.
_EVENT_SKIP_TYPES = {"ctx_length", "hello_ack"}

app = FastAPI()
client = AsyncOpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")

project_dir: Path = Path(".").resolve()
auto_apply_enabled: bool = False


def _load_active_model() -> str:
    # Last UI pick wins over config.MODEL (the boot fallback).
    try:
        saved = paths.ACTIVE_MODEL_FILE.read_text(encoding="utf-8").strip()
        return saved or MODEL
    except (FileNotFoundError, OSError):
        return MODEL


current_model: str = _load_active_model()
current_temperature: float = TEMPERATURE
current_context_length: int = 32768
DEFAULT_NUM_CTX = 32768
DEFAULT_NUM_PREDICT = 8192

SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"}

SESSIONS_DIR = paths.SESSIONS_DIR
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


def save_session(session_id: str, messages: list, prompt: str, temperature: float):
    path = _session_path(session_id)
    if path is None:
        return
    SESSIONS_DIR.mkdir(exist_ok=True)
    payload = {
        "schema": SESSION_SCHEMA,
        "messages": messages,
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


# Project-scoped reference docs (markdown under `manual/`) are accessed
# exclusively via the `list_manual` and `load_manual` tools in tools/core.py.
# History: there was also a `knowledge_packs` chip UI + a system-prompt
# manifest path with a global `packs/` fallback. All retired — usage settled
# on project-scoped manuals reached through the tool surface. If you want a
# manual doc always in scope for a mode, mention it in prompts/<mode>.md.


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


@app.get("/api/prompts")
def get_prompts():
    names = []
    if paths.PROMPTS_DIR.is_dir():
        names = sorted(p.stem for p in paths.PROMPTS_DIR.iterdir() if p.suffix == ".md")
    return JSONResponse({"prompts": names})


@app.get("/pending")
def get_pending():
    return JSONResponse({"writes": dict(pending_writes)})


@app.delete("/pending")
def flush_pending():
    count = len(pending_writes)
    clear_pending_writes()
    return JSONResponse({"flushed": count})


async def _agent_loop_impl(ws: WebSocket, user_content: str, messages: list, state: dict, selected_prompt: str = "DeetsCode", session_id: str | None = None, user_name: str | None = None):
    # Cache the file tree on `state`. Recompute only if invalidated (e.g. after
    # write_file applies). Static over a session — was being re-rendered each
    # turn for no reason.
    tree_text = state.get("file_tree")
    if not tree_text:
        tree_text = build_file_tree(project_dir)
        state["file_tree"] = tree_text
    prompt_template = load_prompt_template(selected_prompt)
    system_prompt = prompt_template.replace("{project_dir}", str(project_dir)).replace("{file_tree}", tree_text)
    # Live layout descriptor (Stage 3): rebuilt every turn so the model sees
    # the bento as it is *now* (pins, floors, runtime states), not a boot
    # snapshot. Rides in every mode because the layout tools are core tools.
    try:
        system_prompt += "\n\n" + _panel_loader.layout_descriptor()
    except Exception:
        pass  # a broken layout file must not take the chat loop down
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
            # Rescue path: Qwen3 sometimes forgets to close </think>, which
            # routes the model's actual reply into the thinking stream and
            # leaves the UI with an empty response. If we produced no visible
            # output and no tool call this turn but DO have a thinking tail,
            # surface that tail as visible text so the user sees something.
            if not stripped and tail_thinking:
                stripped = tail_thinking.strip()
                if stripped:
                    await ws.send_json({"type": "text", "content": stripped})
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
            result = execute_tool(name, args, session_id or "unknown", project_dir, user_name=user_name)
            await ws.send_json({"type": "tool_result", "name": name, "content": result})

            # Auto-refresh the task panel in the UI when update_task writes
            if name == "update_task" and args.get("content", "").strip():
                await ws.send_json({"type": "task_updated"})

            # `set_instance_state` runs side-effect-free in tools/core.py
            # (just validates + formats a confirmation string). The actual
            # WS broadcast happens here so the tool can stay sync and so
            # every connected client — not just the chat WS — sees it.
            if name == "set_instance_state":
                inst_id = (args.get("instance") or "").strip()
                st = (args.get("state") or "").strip()
                if inst_id and st in _TILEFLOW_STATES:
                    await broadcast_tileflow_state(inst_id, st)

            # `recompute_layout` nudges the client to re-run its flow pass.
            if name == "recompute_layout":
                await _broadcast_panel_frame({"type": "tileflow_recompute"})

            # Layout-mutating Stage 3 tools write the layout file inside
            # tools/core.py; on success, every client re-syncs from
            # /api/layout. Errors (validator rejections) broadcast nothing.
            if name in _LAYOUT_MUTATING_TOOLS and isinstance(result, str) and result.startswith("OK"):
                await broadcast_layout_updated()

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


async def agent_loop(ws: WebSocket, user_content: str, messages: list, selected_prompt: str = "DeetsCode", session_id: str | None = None, temperature: float | None = None, user_name: str | None = None):
    state: dict = {"usage_tokens": None, "stream": None}
    try:
        await _agent_loop_impl(ws, user_content, messages, state, selected_prompt, session_id, user_name)
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
                save_session(session_id, messages, selected_prompt, temperature if temperature is not None else current_temperature)
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


from fastapi.responses import HTMLResponse, FileResponse
from panels import loader as _panel_loader
from apps import loader as _apps_loader

# Discover apps first (they contribute panels), then panels.
# Hot reload: POST /api/apps/reload (chains both) or /api/panels/reload.
_apps_loader.discover()
_panel_loader.discover()


@app.get("/api/panels")
async def list_panels():
    """Registry of installed panels — name/title/tier/display + any load errors."""
    out = []
    for m in _panel_loader.registry().values():
        out.append({
            "name": m.name, "title": m.title, "tier": m.tier,
            "author": m.author, "anchored": m.anchored,
            "multi_instance": m.multi_instance,
            "display": m.display.model_dump(),
            "url": m.url,
            "iframe_attrs": m.iframe_attrs,
            "tileflow": m.tileflow.model_dump(),
            "app": m.app,
        })
    return JSONResponse({"panels": out, "errors": _panel_loader.errors()})


@app.get("/api/panels/{name}")
async def get_panel(name: str):
    m = _panel_loader.get(name)
    if m is None:
        return JSONResponse({"error": f"unknown panel: {name}"}, status_code=404)
    return JSONResponse(m.model_dump(by_alias=True))


@app.post("/api/panels/reload")
async def reload_panels():
    _apps_loader.discover()  # apps contribute panels — keep the two in sync
    found = _panel_loader.discover()
    return JSONResponse({"loaded": list(found.keys()), "errors": _panel_loader.errors()})


# ── Apps (apps-over-panels primitive — see docs/apps.md) ────────────────────

@app.get("/api/apps")
async def list_apps():
    out = [m.model_dump(by_alias=True) for m in _apps_loader.registry().values()]
    return JSONResponse({"apps": out, "errors": _apps_loader.errors()})


@app.get("/api/apps/{name}")
async def get_app(name: str):
    m = _apps_loader.get(name)
    if m is None:
        return JSONResponse({"error": f"unknown app: {name}"}, status_code=404)
    return JSONResponse(m.model_dump(by_alias=True))


@app.post("/api/apps/reload")
async def reload_apps():
    found = _apps_loader.discover()
    _panel_loader.discover()
    return JSONResponse({
        "loaded": list(found.keys()),
        "errors": _apps_loader.errors(),
        "panel_errors": _panel_loader.errors(),
    })


@app.post("/api/apps/{name}/instances")
async def launch_app_instance(name: str):
    """Launcher: create a new app instance — one fresh layout instance per
    member panel, all sharing an app_instance id. Broadcasts layout_updated
    so every connected client mounts the new panels live."""
    m = _apps_loader.get(name)
    if m is None:
        return JSONResponse({"error": f"unknown app: {name}"}, status_code=404)
    try:
        layout = _panel_loader.load_layout()
    except FileNotFoundError:
        return JSONResponse({"error": "panel_layout.json not found"}, status_code=404)
    existing = [i for i in layout.instances if i.app == name]
    if existing and not m.multi_instance:
        return JSONResponse(
            {"error": f"app '{name}' is single-instance and already mounted"},
            status_code=409,
        )
    app_instance = f"{name}.{uuid.uuid4().hex[:6]}"
    bento = next((r.id for r in layout.regions if r.kind == "bento"), None)
    if bento is None:
        return JSONResponse({"error": "layout has no bento region"}, status_code=500)
    created = []
    for panel_name in m.panels:
        if _panel_loader.get(panel_name) is None:
            return JSONResponse(
                {"error": f"app panel '{panel_name}' failed to load — see /api/panels errors"},
                status_code=500,
            )
        inst_id = f"{app_instance}.{panel_name}"
        layout.instances.append(_panel_loader.LayoutInstance(
            instance=inst_id, panel=panel_name, region=bento,
            app=name, app_instance=app_instance,
        ))
        created.append(inst_id)
    _panel_loader.save_layout(layout)
    await broadcast_layout_updated()
    return JSONResponse({"app": name, "app_instance": app_instance, "instances": created})


@app.post("/api/apps/{name}/update")
async def update_app_bundle(name: str, req: Request, reset_state: int = 0):
    """Replace an app's bundle from an uploaded zip, preserving state/
    (app_harness.md Phase D / TBD-3).

    - multipart/form-data with a `bundle` zip file, or a raw zip body.
    - Empty body + ?reset_state=1 = reset-only mode (wipes state/*.db without
      touching the bundle) — this is what the schema-mismatch banner calls.
    - A state_schema_version change in the incoming app.json requires
      ?reset_state=1, else 409 SCHEMA_MISMATCH.
    - Swap is staged: unzip to temp, validate, rename old → .bak, rename new
      in place, delete .bak (rollback on failure). state/ carries over.
    """
    import io
    import tempfile
    import zipfile

    installed = _apps_loader.get(name)
    target_dir = _apps_loader.app_dir(name)

    # Read the zip payload (multipart field `bundle`, or raw body).
    raw = b""
    ctype = req.headers.get("content-type", "")
    if "multipart/form-data" in ctype:
        form = await req.form()
        up = form.get("bundle")
        if up is not None:
            raw = await up.read()
    else:
        raw = await req.body()

    if not raw:
        # Reset-only mode.
        if not reset_state:
            return JSONResponse({"error": "no bundle uploaded (pass ?reset_state=1 for a state-only reset)"}, status_code=400)
        if installed is None:
            return JSONResponse({"error": f"unknown app: {name}"}, status_code=404)
        state_dir = _apps_loader.state_dir(name)
        removed = 0
        if state_dir.is_dir():
            for db in state_dir.glob("*.db"):
                db.unlink()
                removed += 1
        await broadcast_app_event({
            "app_id": name, "app_instance": None,
            "event_name": "state-reset", "payload": {"removed": removed},
        })
        return JSONResponse({"ok": True, "reset": True, "state_files_removed": removed})

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return JSONResponse({"error": "payload is not a zip"}, status_code=400)

    with tempfile.TemporaryDirectory(prefix=f"app_{name}_") as tmp:
        tmp_path = Path(tmp)
        zf.extractall(tmp_path)
        # Bundle may be rooted at the zip top level or in a single subdir.
        staged = tmp_path
        if not (staged / "app.json").is_file():
            subdirs = [d for d in staged.iterdir() if d.is_dir()]
            if len(subdirs) == 1 and (subdirs[0] / "app.json").is_file():
                staged = subdirs[0]
            else:
                return JSONResponse({"error": "zip has no app.json at its root"}, status_code=400)
        try:
            incoming = _apps_loader.AppManifest.model_validate(
                json.loads((staged / "app.json").read_text(encoding="utf-8"))
            )
        except Exception as e:
            return JSONResponse({"error": f"invalid app.json in bundle: {e}"}, status_code=400)
        if incoming.name != name:
            return JSONResponse(
                {"error": f"bundle is for app '{incoming.name}', URL says '{name}'"},
                status_code=400,
            )
        if (
            installed is not None
            and incoming.state_schema_version != installed.state_schema_version
            and not reset_state
        ):
            return JSONResponse({
                "error": "SCHEMA_MISMATCH",
                "installed_version": installed.state_schema_version,
                "bundle_version": incoming.state_schema_version,
                "hint": "retry with ?reset_state=1 to acknowledge the state wipe",
            }, status_code=409)

        # Carry state/ over into the staged bundle (or wipe it on reset).
        staged_state = staged / "state"
        if staged_state.exists():
            shutil.rmtree(staged_state)  # bundles must not ship state
        live_state = _apps_loader.state_dir(name)
        if not reset_state and live_state.is_dir():
            shutil.copytree(live_state, staged_state)
        else:
            staged_state.mkdir(exist_ok=True)

        # Atomic-ish swap with rollback.
        bak = target_dir.with_name(f"{name}.bak")
        if bak.exists():
            shutil.rmtree(bak)
        had_old = target_dir.exists()
        try:
            if had_old:
                target_dir.rename(bak)
            shutil.copytree(staged, target_dir)
        except Exception as e:  # rollback
            if had_old and not target_dir.exists() and bak.exists():
                bak.rename(target_dir)
            return JSONResponse({"error": f"swap failed (rolled back): {e}"}, status_code=500)
        if bak.exists():
            shutil.rmtree(bak, ignore_errors=True)

    _apps_loader.discover()
    _panel_loader.discover()
    await broadcast_app_event({
        "app_id": name, "app_instance": None,
        "event_name": "updated", "payload": {"version": incoming.version},
    })
    await broadcast_layout_updated()
    return JSONResponse({"ok": True, "app": name, "version": incoming.version, "reset": bool(reset_state)})


@app.delete("/api/apps/{name}/instances/{app_instance}")
async def remove_app_instance(name: str, app_instance: str):
    """Unmount one app instance: drop its layout instances (state DB is left
    on disk — remove apps/<name>/state/<id>.db by hand to forget a game)."""
    try:
        layout = _panel_loader.load_layout()
    except FileNotFoundError:
        return JSONResponse({"error": "panel_layout.json not found"}, status_code=404)
    before = len(layout.instances)
    layout.instances = [
        i for i in layout.instances
        if not (i.app == name and i.app_instance == app_instance)
    ]
    if len(layout.instances) == before:
        return JSONResponse({"error": "no matching app instance"}, status_code=404)
    _panel_loader.save_layout(layout)
    await broadcast_layout_updated()
    return JSONResponse({"removed": before - len(layout.instances)})


@app.get("/panels/{name}/view", response_class=HTMLResponse)
async def panel_view(name: str, instance: Optional[str] = None):
    import html as _html
    m = _panel_loader.get(name)
    if m is None:
        return HTMLResponse(_panel_error_html(name, "panel not found"), status_code=404)
    if m.tier in (0, 2):
        return HTMLResponse(
            _panel_error_html(name, f"tier {m.tier} has no host-rendered view"),
            status_code=400,
        )
    events: list = []
    try:
        html_body = await _panel_loader.render_view(name, instance_id=instance, event_sink=events)
    except _panel_loader.PanelLoadError as e:
        return HTMLResponse(_panel_error_html(name, str(e)), status_code=500)
    except Exception as e:  # noqa: BLE001 — panel handler failures shouldn't kill the page
        import traceback
        return HTMLResponse(
            _panel_error_html(name, f"{type(e).__name__}: {e}", traceback.format_exc()),
            status_code=500,
        )
    for evt in events:
        await broadcast_app_event(evt)
    return HTMLResponse(html_body)


@app.post("/panels/{name}/action/{fn_name}")
async def panel_action(name: str, fn_name: str, req: Request):
    """Invoke a whitelisted tier-3 panel action (manifest `actions` list).
    The function is called as fn(harness_ctx=..., body=<parsed JSON>) and
    should return a JSON-serializable dict. Queued app_events broadcast
    after it returns. This is the panel-route convention app_hoops.md §8.2
    assumes (e.g. the orders panel's turn submission)."""
    from apps.context import StateSchemaMismatch
    m = _panel_loader.get(name)
    if m is None:
        return JSONResponse({"error": f"unknown panel: {name}"}, status_code=404)
    if m.tier != 3:
        return JSONResponse({"error": "actions are tier-3 only"}, status_code=400)
    if fn_name not in m.actions:
        return JSONResponse(
            {"error": f"action '{fn_name}' not in panel '{name}' actions whitelist"},
            status_code=403,
        )
    try:
        body = await req.json()
    except Exception:
        body = {}
    instance = req.query_params.get("instance")
    try:
        fn = _panel_loader._import_handler(name, f"{m.handler.split(':', 1)[0]}:{fn_name}")
    except _panel_loader.PanelLoadError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    ctx = _panel_loader.build_context(m, instance)
    try:
        result = fn(harness_ctx=ctx, body=body)
        if hasattr(result, "__await__"):
            result = await result
    except StateSchemaMismatch as e:
        return JSONResponse({"error": str(e), "schema_mismatch": True}, status_code=409)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    for evt in ctx.pending_events:
        await broadcast_app_event(evt)
    return JSONResponse({"ok": True, "result": result if isinstance(result, (dict, list, str, int, float, bool, type(None))) else str(result)})


def _panel_error_html(name: str, msg: str, trace: str = "") -> str:
    """Visible error placeholder so a broken panel surfaces in the UI instead
    of silently rendering nothing."""
    import html as _html
    body = f'<div class="panel-error-msg">{_html.escape(msg)}</div>'
    if trace:
        body += f'<details class="panel-error-trace"><summary>traceback</summary><pre>{_html.escape(trace)}</pre></details>'
    return f"""
<style>
  [data-panel-content="{name}"] .panel-error-frame {{ padding: 10px; font-family: monospace; font-size: 11px; color: #ff7676; background: rgba(255, 0, 0, 0.06); border-radius: 4px; }}
  [data-panel-content="{name}"] .panel-error-frame .panel-error-name {{ font-weight: bold; opacity: .8; }}
  [data-panel-content="{name}"] .panel-error-frame .panel-error-msg {{ margin-top: 4px; }}
  [data-panel-content="{name}"] .panel-error-trace {{ margin-top: 6px; opacity: .8; }}
  [data-panel-content="{name}"] .panel-error-trace pre {{ white-space: pre-wrap; max-height: 200px; overflow: auto; }}
</style>
<div class="panel-error-frame">
  <div class="panel-error-name">⚠ panel error: {_html.escape(name)}</div>
  {body}
</div>
""".strip()


@app.get("/panels/{name}/static/{file:path}")
async def panel_static(name: str, file: str):
    m = _panel_loader.get(name)
    if m is None:
        return JSONResponse({"error": "unknown panel"}, status_code=404)
    pdir = _panel_loader.panel_dir(name).resolve()
    static_root = (pdir / "static").resolve()
    target = (static_root / file).resolve()
    if not str(target).startswith(str(static_root)):
        return JSONResponse({"error": "path escape"}, status_code=400)
    if not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(target)


@app.get("/api/layout")
async def get_panel_layout():
    """Read layout/panel_layout.json — the declarative region+instance map
    that drives the UI shell. Edited by hand (and, later, by Claude Code) to
    rearrange the UI without touching panel code."""
    try:
        return JSONResponse(_panel_loader.load_layout().model_dump(by_alias=True))
    except FileNotFoundError:
        return JSONResponse({"error": "panel_layout.json not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.put("/api/layout")
async def put_panel_layout(req: Request):
    """Replace the whole layout sheet. Validates pins (bounds + collisions)
    before writing; rejects with 400 if any pin is illegal."""
    try:
        body = await req.json()
        layout = _panel_loader.PanelLayout.model_validate(body)
    except Exception as e:
        return JSONResponse({"error": f"invalid layout: {e}"}, status_code=400)
    pin_errors = _panel_loader.validate_layout_pins(layout)
    if pin_errors:
        return JSONResponse({"error": "pin validation failed", "details": pin_errors}, status_code=400)
    try:
        _panel_loader.save_layout(layout)
    except OSError as e:
        return JSONResponse({"error": f"write failed: {e}"}, status_code=500)
    await broadcast_layout_updated()
    return JSONResponse(layout.model_dump(by_alias=True))


@app.post("/api/layout/instances/{instance_id}/pin")
async def pin_instance(instance_id: str, req: Request):
    """Pin one instance to a `(col, row, cols, rows)` rectangle on the
    bento grid. Validates against the current layout — out-of-bounds and
    collisions return 400 with a focused error message (drag-to-pin UI
    uses this to surface "your span is too narrow / collides with X")."""
    try:
        body = await req.json()
        pin = _panel_loader.InstancePin.model_validate(body)
    except Exception as e:
        return JSONResponse({"error": f"invalid pin: {e}"}, status_code=400)
    try:
        layout = _panel_loader.load_layout()
    except FileNotFoundError:
        return JSONResponse({"error": "panel_layout.json not found"}, status_code=404)
    try:
        _panel_loader.validate_pin_for_instance(layout, instance_id, pin)
    except _panel_loader.PinValidationError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    target = next((i for i in layout.instances if i.instance == instance_id), None)
    if target is None:
        return JSONResponse({"error": f"unknown instance: {instance_id}"}, status_code=404)
    target.pin = pin
    try:
        _panel_loader.save_layout(layout)
    except OSError as e:
        return JSONResponse({"error": f"write failed: {e}"}, status_code=500)
    await broadcast_layout_updated()
    return JSONResponse(layout.model_dump(by_alias=True))


@app.delete("/api/layout/instances/{instance_id}/pin")
async def unpin_instance(instance_id: str):
    """Unpin one instance — falls back to auto-flow placement."""
    try:
        layout = _panel_loader.load_layout()
    except FileNotFoundError:
        return JSONResponse({"error": "panel_layout.json not found"}, status_code=404)
    target = next((i for i in layout.instances if i.instance == instance_id), None)
    if target is None:
        return JSONResponse({"error": f"unknown instance: {instance_id}"}, status_code=404)
    if target.pin is None:
        return JSONResponse(layout.model_dump(by_alias=True))
    target.pin = None
    try:
        _panel_loader.save_layout(layout)
    except OSError as e:
        return JSONResponse({"error": f"write failed: {e}"}, status_code=500)
    await broadcast_layout_updated()
    return JSONResponse(layout.model_dump(by_alias=True))


_TILEFLOW_STATES = {"dormant", "idle", "active", "focused"}

# Tools (tools/core.py) whose success means the persisted layout sheet
# changed — the dispatch site broadcasts `layout_updated` after them.
_LAYOUT_MUTATING_TOOLS = {
    "pin_instance", "unpin_instance", "set_instance_floor", "apply_layout_preset",
}


@app.post("/api/tileflow/state/{instance_id}")
async def set_tileflow_state(instance_id: str, req: Request):
    """Push a runtime state overlay for one instance to all connected
    clients. Transient — not persisted to `panel_layout.json`. Used by the
    `set_instance_state` model tool, panel internals (e.g. youtube on play),
    and ad-hoc testing via curl."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    state = (body.get("state") or "").strip()
    if state not in _TILEFLOW_STATES:
        return JSONResponse(
            {"error": f"state must be one of {sorted(_TILEFLOW_STATES)}"},
            status_code=400,
        )
    delivered = await broadcast_tileflow_state(instance_id, state)
    return JSONResponse({"ok": True, "instance": instance_id, "state": state, "delivered": delivered})


@app.delete("/api/tileflow/state/{instance_id}")
async def clear_tileflow_state(instance_id: str):
    """Drop the runtime overlay for one instance. The client falls back to
    the manifest's `default_state`."""
    had = _panel_loader.tileflow_overlay.pop(instance_id, None) is not None
    # We don't broadcast a "clear" — instead we set back to default_state on
    # the client. Easiest signal: broadcast `idle` (the conventional default
    # for everything except dormant-by-default panels). Callers that want a
    # specific state should POST instead of DELETE.
    delivered = 0
    if had:
        delivered = await broadcast_tileflow_state(instance_id, "idle")
    return JSONResponse({"ok": True, "instance": instance_id, "cleared": had, "delivered": delivered})


# ── system_log endpoints ─────────────────────────────────────────────────────
# Read-only query surface for the UI interaction stream. Writes happen via
# the `system_log` WS message handler (batched insert) — see the WS dispatch.
# Designed so a small analytics panel (or a curl from the CLI) can ask "which
# panels did I use today" without needing direct DB access.

@app.get("/api/system_log")
async def get_system_log(
    since: Optional[int] = None,
    until: Optional[int] = None,
    instance: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 500,
):
    """Recent UI interaction events, newest first. All filters optional.
    `since`/`until` are unix ms timestamps; `limit` is capped at 5000."""
    return JSONResponse({
        "events": storage.query_system_log(
            since=since, until=until, instance=instance, kind=kind, limit=limit,
        ),
    })


@app.get("/api/system_log/summary")
async def get_system_log_summary(window_ms: Optional[int] = None):
    """Per-(instance, kind) interaction counts. Pass `window_ms` to limit to
    the last N ms; omit for all time. The "is this panel ever used" answer."""
    return JSONResponse({"summary": storage.panel_usage_summary(window_ms=window_ms)})


@app.get("/api/themes")
async def get_themes():
    """Parse theme.css and return all discovered themes with their swatch colors.

    Swatch values may be var()/color-mix() references into palette.css —
    they're used as live CSS values in the picker, so they resolve in-page.
    """
    import re as _re
    theme_css = paths.THEME_CSS
    themes = []
    try:
        if not theme_css.is_file():
            return JSONResponse({"themes": themes})
        text = theme_css.read_text(encoding="utf-8", errors="replace")
        # Find each named [data-theme="x"] block (the bare [data-theme]
        # shared/derived block deliberately doesn't match).
        blocks = _re.findall(r'\[data-theme=["\']([\w-]+)["\']\]\s*\{([^}]+)\}', text)
        for theme_id, body in blocks:
            colors = {}
            for line in body.splitlines():
                m = _re.match(r'\s*--([\w-]+)\s*:\s*([^;]+);', line)
                if m:
                    colors[m.group(1)] = m.group(2).strip()
            swatches = [
                colors.get("canvas", "#888"),
                colors.get("accent-1", colors.get("canvas-blob-1", "#888")),
                colors.get("accent-2", colors.get("canvas-blob-2", "#888")),
                colors.get("text", colors.get("response-text", "#888")),
            ]
            themes.append({"id": theme_id, "swatches": swatches})
        return JSONResponse({"themes": themes})
    except Exception as e:
        return JSONResponse({"themes": [], "error": str(e)})


@app.get("/api/skins")
async def get_skins():
    """Parse skin.css and return the available skins (named [data-skin="x"]
    blocks; the bare [data-skin] base block doesn't count)."""
    import re as _re
    skins = []
    try:
        if not paths.SKIN_CSS.is_file():
            return JSONResponse({"skins": skins})
        text = paths.SKIN_CSS.read_text(encoding="utf-8", errors="replace")
        # Strip /* … */ comments first — prose like `[data-skin="x"]` in the
        # file header must not become a picker entry.
        text = _re.sub(r'/\*.*?\*/', '', text, flags=_re.S)
        seen = set()
        for skin_id in _re.findall(r'\[data-skin=["\']([\w-]+)["\']\]\s*\{', text):
            if skin_id not in seen:
                seen.add(skin_id)
                skins.append({"id": skin_id})
        return JSONResponse({"skins": skins})
    except Exception as e:
        return JSONResponse({"skins": [], "error": str(e)})


@app.get("/api/events/sessions")
async def list_event_sessions_endpoint(limit: int = 50):
    # Union event-log sessions (populated once messages flow) with saved
    # session files on disk (populated on first /hello from a channel).
    # This way new bot channels appear before any traffic has been recorded.
    rows = storage.list_event_sessions(limit=limit)
    seen = {r["session_id"] for r in rows}
    if SESSIONS_DIR.is_dir():
        for p in SESSIONS_DIR.glob("*.json"):
            sid = p.stem
            if sid in seen:
                continue
            try:
                mtime_ms = int(p.stat().st_mtime * 1000)
            except OSError:
                mtime_ms = 0
            rows.append({"session_id": sid, "n": 0, "last_ts": mtime_ms, "last_id": 0})
    # Flag which sessions are currently connected (have a control queue) so
    # the UI can disable remote-control buttons for offline sessions.
    live = set(list_live_sessions())
    for r in rows:
        r["live"] = r["session_id"] in live
    rows.sort(key=lambda r: r.get("last_ts") or 0, reverse=True)
    return JSONResponse({"sessions": rows[:limit]})


@app.post("/api/session/{session_id}/control")
async def session_control_endpoint(session_id: str, body: dict):
    """HTTP facade over enqueue_session_control. Body: {action, prompt?}.
    Used by panels that don't hold a WS, or by external tooling (curl, etc)."""
    try:
        msg = enqueue_session_control(
            session_id, body.get("action"), prompt=body.get("prompt")
        )
        return JSONResponse({"ok": True, "message": msg})
    except RemoteControlError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


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
    selected_prompt: str = "DeetsCode"
    session_id: str | None = None
    spectating: str | None = None  # session_id this ws is subscribed to (read-only mode)
    # Each hello'd session gets its own control queue so other WSes (the web
    # UI's dev panel) can enqueue synthetic control frames for THIS session.
    control_queue: "asyncio.Queue[dict]" = asyncio.Queue()

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

    # Register for tileflow broadcasts and replay current overlay so a
    # mid-session tab refresh restores the bento arrangement.
    _panel_ws.add(ws)
    for inst_id, st in list(_panel_loader.tileflow_overlay.items()):
        try:
            await ws.send_json({"type": "tileflow_state", "instance": inst_id, "state": st})
        except Exception:
            break

    try:
        while True:
            # Race the WS receive against the control queue so remote-control
            # frames (from another UI socket) get picked up even while this
            # session is idle-blocking on the client. First one to arrive wins;
            # the other task is cancelled. See _session_control above.
            recv_task = asyncio.create_task(ws.receive_json())
            ctrl_task = asyncio.create_task(control_queue.get())
            done, pending = await asyncio.wait(
                {recv_task, ctrl_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()
            try:
                if recv_task in done:
                    data = recv_task.result()
                else:
                    data = ctrl_task.result()
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
                    # `packs` field may exist on old session files — ignored
                    # post-knowledge_packs-removal. Left in the file for
                    # forward compat if we ever restore session-scoped packs.
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
                # Register this session's control queue so the UI can remote-
                # control it via `remote_control` frames. Drained in finally.
                _session_control[session_id] = control_queue
                continue

            if data["type"] == "remote_control":
                # Thin WS wrapper around enqueue_session_control. Any new
                # panel or HTTP route that wants to drive another session
                # should call enqueue_session_control directly — don't
                # reinvent the validation/translation logic here.
                try:
                    msg = enqueue_session_control(
                        data.get("target_session_id"),
                        data.get("action"),
                        prompt=data.get("prompt"),
                    )
                    await ws.send_json({"type": "info", "content": msg})
                except RemoteControlError as e:
                    await ws.send_json({"type": "error", "content": f"remote_control: {e}"})
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
                result = _slash_execute(tool, args, session_id or "unknown", project_dir, user_name=None)
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

            # ── Blog panel ops (mode='blog') ──────────────────────────────
            # All blog_* messages share the service layer with tools/blog.py,
            # so panel actions and model actions stay in sync against the
            # same SQLite. The panels can run without ever talking to the model.
            if isinstance(data.get("type"), str) and data["type"].startswith("blog_"):
                try:
                    from tools import blog_service as _blog_svc
                except Exception as e:
                    await ws.send_json({"type": "blog_error", "op": data["type"], "error": f"blog service load failed: {e}"})
                    continue
                op = data["type"]
                req_id = data.get("req_id")
                try:
                    if op == "blog_list_posts":
                        payload = _blog_svc.list_posts(
                            kind=data.get("kind"),
                            status=data.get("status"),
                            limit=int(data.get("limit") or 200),
                        )
                        await ws.send_json({"type": "blog_posts", "req_id": req_id, "posts": payload})
                    elif op == "blog_get_post":
                        payload = _blog_svc.get_post(data["slug"])
                        await ws.send_json({"type": "blog_post", "req_id": req_id, "post": payload})
                    elif op == "blog_create_post":
                        p = _blog_svc.create_post(
                            kind=data["kind"],
                            title=data["title"],
                            date=data.get("date"),
                            meta=data.get("meta") or {},
                            body_md=data.get("body_md"),
                            locked=bool(data.get("locked", False)),
                            slug=data.get("slug"),
                        )
                        await ws.send_json({"type": "blog_post_saved", "req_id": req_id, "post": p})
                    elif op == "blog_update_post":
                        slug = data["slug"]
                        fields = {k: v for k, v in data.items()
                                  if k not in {"type", "slug", "req_id"}}
                        p = _blog_svc.update_post(slug, **fields)
                        await ws.send_json({"type": "blog_post_saved", "req_id": req_id, "post": p})
                    elif op == "blog_publish":
                        p = _blog_svc.publish(data["slug"])
                        await ws.send_json({"type": "blog_post_saved", "req_id": req_id, "post": p})
                    elif op == "blog_unpublish":
                        p = _blog_svc.unpublish(data["slug"])
                        await ws.send_json({"type": "blog_post_saved", "req_id": req_id, "post": p})
                    elif op == "blog_delete_post":
                        _blog_svc.delete_post(data["slug"])
                        await ws.send_json({"type": "blog_post_deleted", "req_id": req_id, "slug": data["slug"]})
                    elif op == "blog_attach_media":
                        p = _blog_svc.attach_media(data["slug"], data["src_path"])
                        await ws.send_json({"type": "blog_post_saved", "req_id": req_id, "post": p})
                    elif op == "blog_attach_media_blob":
                        import base64
                        raw = base64.b64decode(data["content_b64"])
                        p = _blog_svc.attach_media_bytes(data["slug"], data["filename"], raw)
                        await ws.send_json({"type": "blog_post_saved", "req_id": req_id, "post": p})
                    elif op == "blog_lookup_song":
                        results = await _blog_svc.lookup_song(
                            data["query"], limit=int(data.get("limit") or 10)
                        )
                        await ws.send_json({"type": "blog_song_results", "req_id": req_id, "results": results})
                    elif op == "blog_lookup_movie":
                        results = await _blog_svc.lookup_movie(
                            data["query"], limit=int(data.get("limit") or 5)
                        )
                        await ws.send_json({"type": "blog_movie_results", "req_id": req_id, "results": results})
                    elif op == "blog_list_comments":
                        cs = _blog_svc.list_recent_comments(limit=int(data.get("limit") or 100))
                        await ws.send_json({"type": "blog_comments", "req_id": req_id, "comments": cs})
                    elif op == "blog_delete_comment":
                        _blog_svc.delete_comment(data["comment_id"])
                        await ws.send_json({"type": "blog_comment_deleted", "req_id": req_id, "comment_id": data["comment_id"]})
                    elif op == "blog_preview_url":
                        await ws.send_json({"type": "blog_preview_url", "req_id": req_id, "url": _blog_svc.preview_url()})
                    elif op == "blog_get_passphrase":
                        await ws.send_json({
                            "type": "blog_passphrase", "req_id": req_id,
                            "value": _blog_svc.get_passphrase(),
                        })
                    elif op == "blog_set_passphrase":
                        v = _blog_svc.set_passphrase(str(data.get("value") or ""))
                        await ws.send_json({
                            "type": "blog_passphrase", "req_id": req_id,
                            "value": v, "saved": True,
                        })
                    else:
                        await ws.send_json({"type": "blog_error", "op": op, "error": "unknown op"})
                except Exception as e:
                    await ws.send_json({"type": "blog_error", "op": op, "req_id": req_id, "error": str(e)})
                continue

            if data["type"] == "system_log":
                # Client-batched UI interaction events. Schema enforced in
                # storage.record_system_events — malformed entries are dropped
                # silently. We don't ack; the buffer is fire-and-forget by
                # design (analytics shouldn't add latency to clicks).
                batch = data.get("events", [])
                if isinstance(batch, list):
                    try:
                        storage.record_system_events(batch)
                    except Exception as e:
                        # Don't surface storage errors to the client — the
                        # interaction stream is best-effort. Log server-side.
                        print(f"[system_log] insert failed: {e}", flush=True)
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
                    try:
                        paths.ACTIVE_MODEL_FILE.write_text(current_model + "\n", encoding="utf-8")
                    except OSError as e:
                        await ws.send_json({"type": "info", "content": f"(warning: could not persist model choice: {e})"})
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

                user_name: str | None = None
                try:
                    user_payload = json.loads(data["content"])
                    user_name = user_payload["name"]
                    user_text = user_payload["text"]
                except Exception:
                    user_name = "User"
                    user_text = data["content"]

                if selected_prompt == "dnd":
                    dnd_dir = project_dir / paths.DND_SUBDIR
                    world_state_path = dnd_dir / paths.CAMPAIGN_STATE_FILENAME
                    # Back-compat: migrate an old root-level campaign_state.json.
                    legacy = project_dir / paths.CAMPAIGN_STATE_FILENAME
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
                elif selected_prompt == "chess":
                    user_content = (
                        f"<current_request>\n"
                        f"speaker: {user_name}\n"
                        f"message: {user_text}\n"
                        f"</current_request>"
                    )
                else:
                    user_content = user_text

                messages.append({"role": "user", "content": user_content})
                current_task = asyncio.create_task(agent_loop(ws, user_text, messages, selected_prompt, session_id, current_temperature, user_name))

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
        if session_id and _session_control.get(session_id) is control_queue:
            _session_control.pop(session_id, None)
        _panel_ws.discard(ws)


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    args = sys.argv[1:]
    port = PORT
    if "--port" in args:
        i = args.index("--port")
        port = int(args[i + 1])
        del args[i:i + 2]
    if args:
        project_dir = Path(args[0]).expanduser().resolve()
        print(f"Project dir: {project_dir}")
    uvicorn.run(app, host=HOST, port=port, reload=False)
