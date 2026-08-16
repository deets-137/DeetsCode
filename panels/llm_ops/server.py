"""llm_ops — llama-server control + telemetry.

One tile answering three questions: which models exist and what state are
they in (with load/unload buttons), how fast was the last turn (prompt +
generation tok/s from llama-server's final-chunk timings), and where is the
memory (dedicated VRAM vs GPU-visible system RAM — the spillover — vs
working set, per llama-server process, sampled from Windows GPU perf
counters with a short cache).

Everything blocking (urllib to llama-server, the PowerShell counter sample)
runs through asyncio.to_thread — view() is async and the loader awaits it.
"""
from __future__ import annotations

import asyncio
import html
import time

from core import llama_server as llm

try:
    import config as _cfg
    BASE_URL = getattr(_cfg, "LLM_BASE_URL", "http://localhost:8080/v1")
except Exception:
    BASE_URL = "http://localhost:8080/v1"

_STATUS_TONE = {
    "loaded": "go", "loading": "pause", "downloading": "pause",
    "unloaded": "idle", "sleeping": "idle", "failed": "stop",
}


def _gb(n: float | int | None) -> str:
    return "—" if not n else f"{n / 1024**3:.1f} GB"


def _fmt_age(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago"


async def view() -> str:
    models = await asyncio.to_thread(llm.list_models, BASE_URL)
    mem = await asyncio.to_thread(llm.memory_snapshot)
    t = llm.last_turn_timings

    rows = []
    if models is None:
        rows.append('<div class="llmops-dead">llama-server unreachable at '
                    f'{html.escape(llm.root_url(BASE_URL))}</div>')
    else:
        for m in models:
            name, status = html.escape(m["name"]), m["status"]
            tone = _STATUS_TONE.get(status, "idle")
            if status in ("loaded", "loading"):
                btn = f'<button class="llmops-btn" onclick="llmOpsAct(\'unload_model\', \'{name}\')">unload</button>'
            else:
                btn = f'<button class="llmops-btn" onclick="llmOpsAct(\'load_model\', \'{name}\')">load</button>'
            rows.append(
                f'<div class="llmops-row" data-status="{status}">'
                f'<span class="llmops-dot llmops-dot--{tone}"></span>'
                f'<span class="llmops-name" title="{name}">{name}</span>'
                f'<span class="llmops-state">{html.escape(status)}</span>{btn}</div>'
            )

    if t:
        speed = (
            f'<div class="llmops-kv"><span>last turn</span>'
            f'<b>{t.get("predicted_per_second", 0):.1f}</b> tok/s gen · '
            f'<b>{t.get("prompt_per_second", 0):.0f}</b> tok/s prompt · '
            f'{t.get("predicted_n", 0)} tok out'
            f'<span class="llmops-age">{html.escape(str(t.get("model", "")))} · {_fmt_age(t.get("ts", time.time()))}</span></div>'
        )
    else:
        speed = '<div class="llmops-kv llmops-kv--empty">no turns yet this boot</div>'

    if mem and mem.get("procs"):
        vram = sum(p["vram"] for p in mem["procs"])
        spill = sum(p["shared"] for p in mem["procs"])
        ram = sum(p["ram"] for p in mem["procs"])
        spill_cls = " llmops-spill--hot" if spill > 512 * 1024**2 else ""
        memory = (
            f'<div class="llmops-kv"><span>memory</span>'
            f'<b>{_gb(vram)}</b> VRAM · '
            f'<b class="llmops-spill{spill_cls}">{_gb(spill)}</b> spill · '
            f'{_gb(ram)} RAM'
            f'<span class="llmops-age">adapter total {_gb(mem.get("adapter_total"))}</span></div>'
        )
    elif mem is not None:
        memory = '<div class="llmops-kv llmops-kv--empty">no llama-server process running</div>'
    else:
        memory = ""  # non-Windows or counters unavailable: show nothing, not noise

    return f"""
<style>
  .llmops {{ display: flex; flex-direction: column; gap: var(--space-2); font-size: var(--fs-ui); }}
  .llmops-row {{ display: flex; align-items: center; gap: 8px; padding: 5px 8px;
                 background: var(--surface); border-radius: var(--radius-inner); }}
  .llmops-dot {{ width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }}
  .llmops-dot--go    {{ background: var(--go); }}
  .llmops-dot--pause {{ background: var(--pause); }}
  .llmops-dot--stop  {{ background: var(--stop); }}
  .llmops-dot--idle  {{ background: var(--divider); }}
  .llmops-name  {{ flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
                   white-space: nowrap; font-family: var(--font-mono); color: var(--text); }}
  .llmops-state {{ color: var(--subtext); }}
  .llmops-btn {{ background: var(--surface-input); border: none; cursor: pointer;
                 border-radius: var(--radius-control); padding: 2px 8px;
                 font-family: var(--font-mono); font-size: var(--fs-ui);
                 color: var(--text-input); opacity: 0.75; }}
  .llmops-btn:hover {{ opacity: 1; }}
  .llmops-btn:disabled {{ opacity: 0.35; cursor: wait; }}
  .llmops-kv {{ padding: 5px 8px; color: var(--subtext); line-height: 1.6; }}
  .llmops-kv b {{ color: var(--text); font-weight: 600; }}
  .llmops-kv > span:first-child {{ display: block; font-size: 0.85em; opacity: 0.7;
                                   text-transform: uppercase; letter-spacing: 0.4px; }}
  .llmops-kv--empty {{ opacity: 0.55; font-style: italic; }}
  .llmops-age {{ display: block; font-size: 0.85em; opacity: 0.7; }}
  .llmops-spill--hot {{ color: var(--pause); }}
  .llmops-dead {{ padding: 5px 8px; color: var(--error); }}
</style>
<div class="llmops">
  {''.join(rows)}
  {speed}
  {memory}
</div>
<script>
(function () {{
  window.llmOpsAct = async function (action, model) {{
    const btns = document.querySelectorAll(".llmops-btn");
    btns.forEach(b => b.disabled = true);
    try {{
      await fetch(`/panels/llm_ops/action/${{action}}`, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ model }}),
      }});
    }} catch (e) {{ /* refresh below shows the real state either way */ }}
    harness.refreshNow("llm_ops");
  }};
  harness.refresh("llm_ops", 5);
}})();
</script>
"""


# Async + to_thread: a router-mode load blocks for tens of seconds, and a
# blocking call inline in the action endpoint would freeze the event loop
# (the CLAUDE.md gotcha — every panel would render blank while a model loads).
async def load_model(body: dict) -> dict:
    model = (body or {}).get("model", "")
    if not model:
        return {"ok": False, "error": "missing model"}
    return {"ok": await asyncio.to_thread(llm.load_model, BASE_URL, model)}


async def unload_model(body: dict) -> dict:
    model = (body or {}).get("model", "")
    if not model:
        return {"ok": False, "error": "missing model"}
    return {"ok": await asyncio.to_thread(llm.unload_model, BASE_URL, model)}
