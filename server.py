import json
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI

from config import HOST, MODEL, OLLAMA_BASE_URL, PORT
from tools import TOOL_DEFINITIONS, clear_pending_writes, execute_tool, pending_writes

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


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    messages: list[dict] = []

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

            # Chat message — run agent loop
            user_content = data["content"]
            messages.append({"role": "user", "content": user_content})

            tree_text = build_file_tree(project_dir)
            system_prompt = f"""You are a coding assistant working on the project at: {project_dir}

File tree:
{tree_text}

You have tools to read files, write files, list directories, and run allowed shell commands.
Always use paths relative to the project root.
When you write files, the changes are queued for user approval — tell the user when you've queued writes.
Be concise."""

            loop_messages = [{"role": "system", "content": system_prompt}] + messages

            while True:
                response = await client.chat.completions.create(
                    model=MODEL,
                    messages=loop_messages,
                    tools=TOOL_DEFINITIONS,
                )
                msg = response.choices[0].message

                if msg.content:
                    await ws.send_json({"type": "text", "content": msg.content})

                if not msg.tool_calls:
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    break

                # Append assistant message with tool calls to loop context
                loop_messages.append(msg.model_dump(exclude_none=True))

                for tc in msg.tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}

                    await ws.send_json({"type": "tool_call", "name": name, "args": args})

                    result = execute_tool(name, args, project_dir)

                    await ws.send_json({"type": "tool_result", "name": name, "content": result})

                    loop_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

            if pending_writes:
                await ws.send_json({
                    "type": "pending_writes",
                    "writes": dict(pending_writes),
                })

            await ws.send_json({"type": "done"})

    except WebSocketDisconnect:
        pass


app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        project_dir = Path(sys.argv[1]).expanduser().resolve()
        print(f"Project dir: {project_dir}")
    uvicorn.run(app, host=HOST, port=PORT, reload=False)
