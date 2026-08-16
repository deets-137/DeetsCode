"""Ollama runtime introspection — which models are loaded, and on what.

Was the `ollama_ps` panel (a 6x1 bento cell for three numbers). The slot
rework demoted it out of the tile system (docs/slots.md "Demoted out of the
tile system"); the parse lives here now and surfaces as the titlebar status
strip via GET /api/ollama/ps.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

_SPLIT_RE = re.compile(r"(\d+)%\s*/\s*(\d+)%\s*(CPU/GPU|GPU/CPU)", re.IGNORECASE)
_SINGLE_RE = re.compile(r"(\d+)%\s*(GPU|CPU)(?!/)", re.IGNORECASE)


def parse_processor(s: str) -> tuple[int, int]:
    """Extract (gpu%, cpu%) from strings like '100% GPU' or '47%/53% CPU/GPU'."""
    m = _SPLIT_RE.search(s)
    if m:
        a, b, order = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        return (b, a) if order == "CPU/GPU" else (a, b)
    gpu = cpu = 0
    for pct, kind in _SINGLE_RE.findall(s):
        if kind.upper() == "GPU":
            gpu = int(pct)
        else:
            cpu = int(pct)
    return gpu, cpu


def ps() -> list[dict]:
    """Return [{name, size, gpu_pct, cpu_pct, processor_raw}, ...] for each
    running model. Empty list if `ollama` isn't installed or nothing is loaded."""
    exe = shutil.which("ollama")
    if not exe:
        return []
    # Capture to a temp file, not a pipe. With no daemon running, `ollama ps`
    # auto-starts one; that grandchild inherits the pipe's write handle and
    # never closes it, so subprocess's reader threads never see EOF. The final
    # thread join in _communicate takes no timeout, so `timeout=` below cannot
    # save us -- the call blocks forever. A file has no reader threads.
    fd, tmp = tempfile.mkstemp(prefix="ollama-ps-", suffix=".txt")
    try:
        with os.fdopen(fd, "w+", encoding="utf-8", errors="replace") as sink:
            try:
                subprocess.run(
                    [exe, "ps"],
                    stdin=subprocess.DEVNULL,
                    stdout=sink, stderr=subprocess.DEVNULL,
                    timeout=3, check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                return []
            sink.seek(0)
            out = sink.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
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
        gpu_pct, cpu_pct = parse_processor(processor_raw)
        rows.append({
            "name": name,
            "size": size,
            "gpu_pct": gpu_pct,
            "cpu_pct": cpu_pct,
            "processor_raw": processor_raw,
        })
    return rows
