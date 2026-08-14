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
<script>
(function () {
  // Entries can arrive before this panel mounts (it defaults to the tray
  // and is woken by app.js's signalPanel on the first tool_call). Drain
  // whatever app.js buffered in the gap.
  if (window._flushToolLogBuffer) window._flushToolLogBuffer();
  if (!window.harness || !window.harness.subscribe) return;
  // Tileflow: active while a tool call is in flight, idle once the
  // turn ends. Wake/sleep (dormant ↔ bento) is owned by app.js via
  // harness.signalContent — these only pulse a mounted panel.
  window.harness.subscribe('tool_log', 'tool_call', () => {
    if (window.harness.setState) window.harness.setState('tool_log', 'active');
  });
  window.harness.subscribe('tool_log', 'done', () => {
    if (window.harness.setState) window.harness.setState('tool_log', 'idle');
  });
  window.harness.subscribe('tool_log', 'error', () => {
    if (window.harness.setState) window.harness.setState('tool_log', 'idle');
  });
})();
</script>
""".strip()
