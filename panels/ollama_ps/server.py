"""ollama_ps — tier-3 panel showing GPU/CPU split for currently-running models.

Runs `ollama ps`, parses the PROCESSOR column (e.g. "100% GPU", "47%/53% CPU/GPU"),
and renders one bar per running model. Refresh cadence is owned by the panel
itself via `harness.refresh('ollama_ps', 5)`.
"""
from __future__ import annotations

import html
import re
import shutil
import subprocess


def fetch() -> list[dict]:
    """Return [{name, gpu_pct, cpu_pct, size, processor_raw}, ...] for each
    running model. Empty list if `ollama` not installed or no models loaded."""
    exe = shutil.which("ollama")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "ps"],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if len(lines) <= 1:
        return []
    rows = []
    for ln in lines[1:]:
        # Columns are whitespace-separated but PROCESSOR can contain a space
        # ("47%/53% CPU/GPU"). Take name/id/size, then everything between SIZE
        # and the trailing UNTIL/CONTEXT columns is the processor cell.
        parts = ln.split()
        if len(parts) < 3:
            continue
        name = parts[0]
        size = parts[2] + " " + parts[3] if len(parts) > 3 and parts[3] in {"GB", "MB"} else parts[2]
        processor_raw = " ".join(parts[3:]) if size == parts[2] else " ".join(parts[4:])
        gpu_pct, cpu_pct = _parse_processor(processor_raw)
        rows.append({
            "name": name,
            "size": size,
            "gpu_pct": gpu_pct,
            "cpu_pct": cpu_pct,
            "processor_raw": processor_raw,
        })
    return rows


_PCT_RE = re.compile(r"(\d+)%\s*(GPU|CPU)", re.IGNORECASE)


def _parse_processor(s: str) -> tuple[int, int]:
    """Extract GPU% and CPU% from strings like '100% GPU' or '47%/53% CPU/GPU'."""
    gpu = cpu = 0
    for pct, kind in _PCT_RE.findall(s):
        if kind.upper() == "GPU":
            gpu = int(pct)
        else:
            cpu = int(pct)
    if gpu == 0 and cpu == 0:
        # Fallback: bare "47%/53% CPU/GPU" form (order matters in ollama ps).
        nums = re.findall(r"(\d+)%", s)
        if "CPU/GPU" in s.upper() and len(nums) >= 2:
            cpu, gpu = int(nums[0]), int(nums[1])
        elif "GPU/CPU" in s.upper() and len(nums) >= 2:
            gpu, cpu = int(nums[0]), int(nums[1])
    return gpu, cpu


def view() -> str:
    rows = fetch()
    if not rows:
        body = '<div class="ollama-empty">no models loaded</div>'
    else:
        items = []
        for r in rows:
            items.append(
                f'<div class="ollama-row">'
                f'  <div class="ollama-row-head">'
                f'    <span class="ollama-name">{html.escape(r["name"])}</span>'
                f'    <span class="ollama-size">{html.escape(r["size"])}</span>'
                f'  </div>'
                f'  <div class="ollama-bar">'
                f'    <div class="ollama-bar-gpu" style="width:{r["gpu_pct"]}%" title="GPU {r["gpu_pct"]}%"></div>'
                f'    <div class="ollama-bar-cpu" style="width:{r["cpu_pct"]}%" title="CPU {r["cpu_pct"]}%"></div>'
                f'  </div>'
                f'  <div class="ollama-legend">'
                f'    <span class="ollama-legend-gpu">GPU {r["gpu_pct"]}%</span>'
                f'    <span class="ollama-legend-cpu">CPU {r["cpu_pct"]}%</span>'
                f'  </div>'
                f'</div>'
            )
        body = "".join(items)
    return f"""
<style>
  [data-panel-content="ollama_ps"] {{ padding: 8px 10px; font-family: monospace; font-size: 12px; }}
  [data-panel-content="ollama_ps"] .ollama-empty {{ opacity: .55; }}
  [data-panel-content="ollama_ps"] .ollama-row {{ margin-bottom: 10px; }}
  [data-panel-content="ollama_ps"] .ollama-row-head {{ display: flex; justify-content: space-between; margin-bottom: 4px; }}
  [data-panel-content="ollama_ps"] .ollama-size {{ opacity: .55; }}
  [data-panel-content="ollama_ps"] .ollama-bar {{ display: flex; height: 10px; border-radius: 4px; overflow: hidden; background: rgba(255,255,255,0.05); }}
  [data-panel-content="ollama_ps"] .ollama-bar-gpu {{ background: #4caf50; }}
  [data-panel-content="ollama_ps"] .ollama-bar-cpu {{ background: #ff9800; }}
  [data-panel-content="ollama_ps"] .ollama-legend {{ display: flex; gap: 12px; margin-top: 3px; opacity: .7; font-size: 11px; }}
  [data-panel-content="ollama_ps"] .ollama-legend-gpu::before {{ content: "■ "; color: #4caf50; }}
  [data-panel-content="ollama_ps"] .ollama-legend-cpu::before {{ content: "■ "; color: #ff9800; }}
</style>
{body}
<script>
  if (window.harness && window.harness.refresh) {{
    window.harness.refresh('ollama_ps', 5);
  }}
</script>
""".strip()
