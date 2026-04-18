import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

DEFAULT_PROMPT = """You are working on the project at: {project_dir}

File tree:
{file_tree}

Do exactly what the user asks. Use the provided tools. Keep responses short."""


def strip_think(text: str) -> str:
    return THINK_BLOCK_RE.sub("", text).strip()


def load_prompt_template(name: str = "default") -> str:
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
from tools import TOOL_DEFINITIONS, clear_pending_writes, clear_read_files, execute_tool, pending_writes

app = FastAPI()
client = AsyncOpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")

project_dir: Path = Path(".").resolve()
packs_dir: Path = Path(__file__).parent / "packs"
auto_apply_enabled: bool = False
current_model: str = MODEL
current_temperature: float = TEMPERATURE
current_context_length: int = 262144

SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"}


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
    if not names:
        return ""
    parts = []
    for name in names:
        safe = Path(name).name  # strip any path separators
        for _scope, src in _pack_sources():
            path = src / f"{safe}.md"
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                parts.append(f"### {safe}\n\n{text.strip()}")
            except OSError:
                pass
            break
    if not parts:
        return ""
    return "## Reference Documentation\n\nThe following domain docs are loaded for this session. Consult them before using general knowledge.\n\n" + "\n\n---\n\n".join(parts)


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
        action = "IF step finished → call update_task marking this [x] and next [/] | ELSE → continue step work"
    else:
        todo_idx = next((i for i, (s, _) in enumerate(steps) if s == " "), None)
        if todo_idx is None:
            return "<system>\nSTATE: ALL_STEPS_COMPLETE\nACTION: emit final reply to user and stop. do not call tools.\n</system>"
        state = "STEP_PENDING_START"
        step_label = f"{todo_idx + 1}/{total}"
        step_text = steps[todo_idx][1]
        next_text = steps[todo_idx + 1][1] if todo_idx + 1 < total else "(none — final step)"
        action = "call update_task marking this step [/] before doing any work"

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


async def _agent_loop_impl(ws: WebSocket, user_content: str, messages: list, state: dict, selected_packs: list[str], selected_prompt: str = "default"):
    tree_text = build_file_tree(project_dir)
    prompt_template = load_prompt_template(selected_prompt)
    system_prompt = prompt_template.replace("{project_dir}", str(project_dir)).replace("{file_tree}", tree_text)
    pack_block = load_packs(selected_packs)
    if pack_block:
        system_prompt = f"{system_prompt}\n\n{pack_block}"
    loop_messages = [{"role": "system", "content": system_prompt}] + messages
    MAX_ITERATIONS = 25
    iteration = 0

    while True:
        iteration += 1
        if iteration > MAX_ITERATIONS:
            await ws.send_json({"type": "error", "content": f"Stopped: exceeded {MAX_ITERATIONS} tool-call iterations (likely stuck in a loop)."})
            messages.append({"role": "assistant", "content": "[stopped: iteration cap reached]"})
            break
        stream = await client.chat.completions.create(
            model=current_model,
            messages=loop_messages,
            tools=TOOL_DEFINITIONS,
            stream=True,
            temperature=current_temperature,
            stream_options={"include_usage": True},
            extra_body={
                "options": {
                    "num_ctx":262144,
                    "num_predict":4096,
                    "num_gpu": 99
                }
            }
        )

        reasoning_buf = ""
        content_buf = ""
        tool_calls_buf: dict[int, dict] = {}

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
                await ws.send_json({"type": "text", "content": delta.content})

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
            result = execute_tool(name, args, project_dir)
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
            applied = []
            for rel_path, content in pending_writes.items():
                full_path = project_dir / rel_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content, encoding="utf-8")
                applied.append(rel_path)
            clear_pending_writes()
            await ws.send_json({"type": "writes_applied", "files": applied})
            loop_messages.append({
                "role": "system",
                "content": f"<system>\nEVENT: queued files written to disk: {', '.join(applied)}\nACTION: proceed with the next step.\n</system>"
            })
            continue

    if pending_writes:
        await ws.send_json({"type": "pending_writes", "writes": dict(pending_writes)})


async def agent_loop(ws: WebSocket, user_content: str, messages: list, selected_packs: list[str], selected_prompt: str = "default"):
    state = {"usage_tokens": None}
    try:
        await _agent_loop_impl(ws, user_content, messages, state, selected_packs, selected_prompt)
    except asyncio.CancelledError:
        try:
            await ws.send_json({"type": "info", "content": "Cancelled."})
        except Exception:
            pass
        raise
    finally:
        try:
            if state["usage_tokens"]:
                await ws.send_json({"type": "usage", "total": state["usage_tokens"], "max": current_context_length})
            await ws.send_json({"type": "done"})
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


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global current_model, auto_apply_enabled, current_temperature
    await ws.accept()
    messages: list[dict] = []
    current_task: asyncio.Task | None = None
    selected_packs: list[str] = []

    try:
        while True:
            data = await ws.receive_json()

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
                result = execute_tool(tool, args, project_dir)
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

            if data["type"] == "reset":
                if current_task and not current_task.done():
                    current_task.cancel()
                messages.clear()
                clear_pending_writes()
                clear_read_files()
                await ws.send_json({"type": "reset_complete"})
                continue

            if data["type"] == "apply_writes":
                applied = []
                for rel_path, content in pending_writes.items():
                    full_path = project_dir / rel_path
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(content, encoding="utf-8")
                    applied.append(rel_path)
                clear_pending_writes()
                await ws.send_json({"type": "writes_applied", "files": applied})
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
                    global current_context_length
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
            if data["type"] == "message":
                if current_task and not current_task.done():
                    current_task.cancel()

                # 1. Parse the JSON packet from discord_bot.py
                import json
                try:
                    user_payload = json.loads(data["content"])
                    user_name = user_payload["name"]
                    user_text = user_payload["text"]
                except:
                    # Fallback if it's not JSON (e.g., from the web UI)
                    user_name = "User"
                    user_text = data["content"]

                # 2. LOAD WORLD STATE (Recommendation #2)
                world_state_path = project_dir / "campaign_state.json"
                world_state = "{}"
                if world_state_path.exists():
                    world_state = world_state_path.read_text(encoding="utf-8")

                # 3. TAGGING & INSTRUCTIONS (Recommendation #3)
                # We replace your old 'wrapped' logic with structured XML
                for m in messages:
                    if m.get("role") == "user" and isinstance(m.get("content"), str) and "<current_action>" in m["content"]:
                        m["content"] = m["content"].replace("<current_action>", "<prior_action>").replace("</current_action>", "</prior_action>")

                rich_prompt = f"""
        <world_state>
        {world_state}
        </world_state>

        <current_action>
        Player: {user_name}
        Action: {user_text}
        </current_action>

        <dm_instructions>
        - You are the Dungeon Master. 
        - Use the current_action to progress the story.
        - If stats change, use write_file to update campaign_state.json.
        - Maintain the 'vibe' of the current world_state.
        </dm_instructions>
        """
            messages.append({"role": "user", "content": rich_prompt})
            current_task = asyncio.create_task(agent_loop(ws, user_text, messages, list(selected_packs)))

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
            await ws.send_json({"type": "done"})
        except Exception:
            pass
        clear_pending_writes()
        clear_read_files()


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        project_dir = Path(sys.argv[1]).expanduser().resolve()
        print(f"Project dir: {project_dir}")
    uvicorn.run(app, host=HOST, port=PORT, reload=False)
