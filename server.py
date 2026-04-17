import asyncio
import json
import sys
from pathlib import Path

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

SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"}


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


@app.get("/pending")
def get_pending():
    return JSONResponse({"writes": dict(pending_writes)})


async def agent_loop(ws: WebSocket, user_content: str, messages: list):
    tree_text = build_file_tree(project_dir)
    prompt_template = Path("prompt.md").read_text(encoding="utf-8")
    system_prompt = prompt_template.replace("{project_dir}", str(project_dir)).replace("{file_tree}", tree_text)
    loop_messages = [{"role": "system", "content": system_prompt}] + messages
    usage_tokens = None

    while True:
        stream = await client.chat.completions.create(
            model=MODEL,
            messages=loop_messages,
            tools=TOOL_DEFINITIONS,
            stream=True,
            temperature=TEMPERATURE,
            stream_options={"include_usage": True},
        )

        reasoning_buf = ""
        content_buf = ""
        tool_calls_buf: dict[int, dict] = {}

        async for chunk in stream:
            if chunk.usage:
                usage_tokens = chunk.usage.total_tokens
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
            messages.append({"role": "assistant", "content": content_buf})
            break

        assembled_tool_calls = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            }
            for tc in tool_calls_buf.values()
        ]
        loop_messages.append({
            "role": "assistant",
            "content": content_buf or None,
            "tool_calls": assembled_tool_calls,
        })

        for tc in tool_calls_buf.values():
            name = tc["name"]
            try:
                args = json.loads(tc["arguments"])
            except json.JSONDecodeError:
                args = {}

            await ws.send_json({"type": "tool_call", "name": name, "args": args})
            result = execute_tool(name, args, project_dir)
            await ws.send_json({"type": "tool_result", "name": name, "content": result})

            loop_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": f"{result}\n\n[Original task: {user_content}]",
            })

    if pending_writes:
        await ws.send_json({"type": "pending_writes", "writes": dict(pending_writes)})

    if usage_tokens:
        await ws.send_json({"type": "usage", "total": usage_tokens, "max": 131072})

    await ws.send_json({"type": "done"})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    messages: list[dict] = []
    current_task: asyncio.Task | None = None

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
                await ws.send_json({"type": "info", "content": f"Project set to: {project_dir}"})
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

            if data["type"] == "message":
                if current_task and not current_task.done():
                    current_task.cancel()
                user_content = data["content"]
                messages.append({"role": "user", "content": user_content})
                current_task = asyncio.create_task(agent_loop(ws, user_content, messages))

    except WebSocketDisconnect:
        if current_task and not current_task.done():
            current_task.cancel()
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


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        project_dir = Path(sys.argv[1]).expanduser().resolve()
        print(f"Project dir: {project_dir}")
    uvicorn.run(app, host=HOST, port=PORT, reload=False)
