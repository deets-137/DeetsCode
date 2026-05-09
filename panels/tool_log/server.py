"""tool_log panel — live tool_call/tool_result stream.

Hybrid: server provides the chrome + #tool-panel-inner container; the
existing app.js handlers (addToolEntry, updateToolResult, clearToolPanel)
fill it as WS messages arrive. Migrating those into the panel itself can
come once the chat panel migrates.

Renders a clear button that hits the existing handler. Subscribes to
reset/run-start events to clear via clearToolPanel().
"""
from __future__ import annotations


def view() -> str:
    return """
<div data-panel-actions>
  <button onclick="if(window.clearToolPanel)clearToolPanel()" title="Clear tool log">✕</button>
</div>
<div class="tool-panel-inner" id="tool-panel-inner"></div>
""".strip()
