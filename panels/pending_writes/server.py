"""pending_writes panel — queued file writes awaiting approve / reject / flush.

WS-driven via `harness.subscribe`: re-fetches on `pending_writes`,
`writes_applied`, `writes_rejected`, and `reset_complete` events. The
approve/reject/flush buttons reuse existing global JS handlers
(applyWrites, rejectWrites, flushPending) — once the chat panel migrates
those move into the harness.* API or get inlined here.
"""
from __future__ import annotations

import html


def view() -> str:
    from tools import pending_writes
    files = list(pending_writes.keys())
    count = len(files)
    if count == 0:
        body = '<span class="pending-empty">queue is empty</span>'
    else:
        rows = []
        for f in files:
            content = pending_writes.get(f) or ""
            n_bytes = len(content) if isinstance(content, (str, bytes)) else 0
            rows.append(
                f'<div class="pending-row">'
                f'<span class="pending-path">{html.escape(f)}</span>'
                f'<span class="pending-bytes">{n_bytes:,} B</span>'
                f'</div>'
            )
        body = "".join(rows)
    return f"""
<div data-panel-actions>
  <span class="pending-count">{count}</span>
  <button class="pending-pill apply"  onclick="if(window.applyWrites)applyWrites()"  title="Write queued changes to disk">approve</button>
  <button class="pending-pill reject" onclick="if(window.rejectWrites)rejectWrites()" title="Drop queued changes (via websocket)">reject</button>
  <button class="pending-pill flush"  onclick="if(window.flushPending)flushPending()" title="Force-clear the queue (HTTP DELETE /pending)">flush</button>
</div>
<div class="pending-panel-inner">{body}</div>
<script>
(function () {{
  if (!window.harness || !window.harness.subscribe) return;
  const refresh = () => window.harness.refreshNow('pending_writes');
  window.harness.subscribe('pending_writes', 'pending_writes', refresh);
  window.harness.subscribe('pending_writes', 'writes_applied', refresh);
  window.harness.subscribe('pending_writes', 'writes_rejected', refresh);
  window.harness.subscribe('pending_writes', 'reset_complete', refresh);
}})();
</script>
""".strip()
