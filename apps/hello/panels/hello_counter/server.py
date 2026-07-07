"""hello_counter — reference tier-3 app panel (apps-over-panels).

Demonstrates the whole harness_ctx surface in ~30 lines: shared state via
app_state, a whitelisted action, and an app_event that peer panels
(hello_mirror) subscribe to. See docs/apps.md.
"""


def view(harness_ctx):
    count = harness_ctx.app_state("count") or 0
    inst = harness_ctx.instance_id or "hello_counter"
    return f"""
<div style="display:flex;flex-direction:column;gap:8px;padding:8px;">
  <div style="font-size:28px;font-variant-numeric:tabular-nums;">{count}</div>
  <button style="align-self:flex-start;font-size:11px;padding:4px 10px;cursor:pointer;"
          onclick="fetch('/panels/hello_counter/action/increment?instance={inst}', {{method:'POST'}})">
    increment
  </button>
</div>
<script>
  harness.app.subscribe("count-changed", () => {{
    harness.refreshNow("{inst}");
  }});
</script>"""


def increment(harness_ctx, body):
    count = (harness_ctx.app_state("count") or 0) + 1
    harness_ctx.app_state("count", count)
    harness_ctx.app_event("count-changed", {"count": count})
    return {"count": count}
