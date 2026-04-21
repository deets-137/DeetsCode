"""
Tool packs, gated by prompt mode.

`core` is always loaded (read_file, write_file, search, run_command, roll_dice,
etc — things any agent wants). Mode-specific packs add game tools on top.

Usage from server.py:
    from tools import load_tools
    TOOL_DEFINITIONS, execute_tool = load_tools(prompt_mode)
    ...
    result = execute_tool(name, args, session_id, project_dir, user_name=user_name)

Adding a new mode:
    1. Create tools/<mode>.py exporting TOOL_DEFINITIONS + execute_tool(
       name, args, session_id, project_dir, user_name=None) → str
    2. Register it in _MODE_PACKS below.
    3. Drop a matching prompts/<mode>.md.
"""

import importlib
from pathlib import Path
from typing import Callable

from .core import (
    TOOL_DEFINITIONS as CORE_TOOLS,
    pending_writes,
    read_files,
    clear_pending_writes,
    clear_read_files,
)

# Mode name (matches prompts/<mode>.md) → module name under tools/.
_MODE_PACKS: dict[str, str] = {
    "DeetsCode": "coding",
    "default":   "coding",  # backcompat alias for pre-rename sessions
    "chess":     "chess",
    # "dnd":   "dnd",    # future
    # "mafia": "mafia",  # future
}


def load_tools(mode: str) -> tuple[list[dict], Callable]:
    """Return (tool_definitions, execute_fn) merged for the given mode.

    execute_fn signature:
        execute_tool(name, args, session_id, project_dir, user_name=None) -> str

    Core tools accept but ignore session_id/user_name. Game packs use them for
    per-channel state and per-player action enforcement.
    """
    from . import core as _core_mod

    defs: list[dict] = list(CORE_TOOLS)
    # dispatch[name] = module with execute_tool(name, args, session_id, project_dir, user_name=None)
    dispatch: dict[str, object] = {
        t["function"]["name"]: _core_mod for t in CORE_TOOLS
    }

    pack_name = _MODE_PACKS.get(mode)
    if pack_name:
        mod = importlib.import_module(f"tools.{pack_name}")
        for t in mod.TOOL_DEFINITIONS:
            tname = t["function"]["name"]
            if tname in dispatch:
                raise RuntimeError(
                    f"Tool '{tname}' in pack '{pack_name}' collides with core tool"
                )
            defs.append(t)
            dispatch[tname] = mod

    def execute_tool(
        name: str,
        args: dict,
        session_id: str,
        project_dir: Path,
        user_name: str | None = None,
    ) -> str:
        mod = dispatch.get(name)
        if mod is None:
            return f"Unknown tool: {name}"
        return mod.execute_tool(name, args, session_id, project_dir, user_name=user_name)

    return defs, execute_tool


__all__ = [
    "load_tools",
    "pending_writes",
    "read_files",
    "clear_pending_writes",
    "clear_read_files",
]
