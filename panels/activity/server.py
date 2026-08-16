"""activity panel — "what did / will Claude do", in one tile.

The slot rework merged `tool_log` + `pending_writes` here (docs/slots.md
"Merges -> the pool"). Both answered the same question and neither earned a
tile of its own.

Shape: a scrolling tool-call stream (client-push, filled by app.js's
addToolEntry / updateToolResult) with queued writes riding above it as an
**actionable banner** rather than peer rows — approve/reject must never be
buried under 40 tool calls. The header carries the queue count as a badge.

Ids `#tool-panel-inner`, `#pending-panel-inner`, `#pending-count` are load
bearing: app.js writes into them directly.
"""
from __future__ import annotations

import html


def view() -> str:
    from tools import pending_writes
    files = list(pending_writes.keys())
    count = len(files)

    if count == 0:
        banner = ""
    else:
        rows = "".join(
            f'<div class="pending-row">'
            f'<span class="pending-path">{html.escape(f)}</span>'
            f'<span class="pending-bytes">{len(pending_writes.get(f) or ""):,} B</span>'
            f'</div>'
            for f in files
        )
        banner = f"""
<div class="activity-banner" id="activity-banner">
  <div class="activity-banner-head">
    <span class="activity-banner-title">{count} write{'' if count == 1 else 's'} awaiting approval</span>
    <span class="activity-banner-acts">
      <button class="pending-pill apply"  onclick="if(window.applyWrites)applyWrites()"  title="Write queued changes to disk">approve</button>
      <button class="pending-pill reject" onclick="if(window.rejectWrites)rejectWrites()" title="Drop queued changes">reject</button>
      <button class="pending-pill flush"  onclick="if(window.flushPending)flushPending()" title="Force-clear the queue">flush</button>
    </span>
  </div>
  <div class="pending-panel-inner" id="pending-panel-inner">{rows}</div>
</div>"""

    return f"""
<div data-panel-actions>
  <span class="activity-badge{'' if count else ' is-empty'}" id="pending-count"
        title="queued writes">{count}</span>
  <button onclick="if(window.clearToolPanel)clearToolPanel()" title="Clear tool log">✕</button>
</div>
{banner}
<div class="tool-panel-inner" id="tool-panel-inner"></div>
<script>
(function () {{
  // Tool entries accumulate in app.js's session-long buffer whether or not
  // this panel is placed; drain it into the fresh (empty) container.
  if (window._flushToolLogBuffer) window._flushToolLogBuffer();
  if (!window.harness) return;

  // Re-render on anything that changes the write queue. The tool stream
  // needs no refetch — app.js pushes into #tool-panel-inner directly.
  const refresh = () => window.harness.refreshNow('activity');
  for (const evt of ['pending_writes', 'writes_applied', 'writes_rejected', 'reset_complete']) {{
    window.harness.subscribe('activity', evt, refresh);
  }}

  // Summon: a queued write is the one thing here worth interrupting for.
  if ({count} > 0 && window.harness.requestPanel) window.harness.requestPanel('activity');
  // Tool calls only mark the tile — never yank a slot out from under you.
  window.harness.subscribe('activity', 'tool_call', () => window.harness.notify('activity'));
}})();
</script>
""".strip()
