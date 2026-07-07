"""hello_mirror — reference app panel: reads shared state written by
hello_counter and re-renders on its `count-changed` app event. No writes.
Coordination flows through harness_ctx.app_event only — this panel doesn't
know hello_counter exists by name (see app_hoops.md §10)."""


def view(harness_ctx):
    count = harness_ctx.app_state("count")
    shown = "–" if count is None else count
    inst = harness_ctx.instance_id or "hello_mirror"
    return f"""
<div style="padding:8px;font-size:13px;">mirror sees: <b>{shown}</b></div>
<script>
  harness.app.subscribe("count-changed", () => {{
    harness.refreshNow("{inst}");
  }});
</script>"""
