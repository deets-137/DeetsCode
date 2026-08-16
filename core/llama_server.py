"""llama-server (llama.cpp) runtime introspection and lifecycle.

Replaced core/ollama.py in the Aug 2026 backend swap. Everything here is
plain HTTP against llama-server's router-mode endpoints — no subprocess
parsing, so the old `ollama ps` pipe-inheritance gotcha died with it (git
history on core/ollama.py has the war story).

llama-server runs in *router mode* (launched with no -m): it discovers GGUFs
from --models-dir / the llama.cpp cache, exposes them all behind one
OpenAI-compatible /v1, and loads/unloads on demand as requests name them in
the `model` field. The endpoints used here:

  GET  {root}/health         200 when up
  GET  {root}/models         router listing: data[].id + data[].status.value
                             ("loaded" / "loading" / "unloaded" / ...)
  GET  {root}/props?model=x  per-model props; default_generation_settings.n_ctx
"""
from __future__ import annotations

import atexit
import json
import platform
import shutil
import subprocess
import time
import urllib.parse
import urllib.request

# Last completed turn's llama-server timings, stashed by server.py's stream
# loop from the final chunk's `timings` object (+ "model" and "ts" keys).
# Read by the llm_ops panel. Empty until the first turn finishes.
last_turn_timings: dict = {}


def root_url(base_url: str) -> str:
    """Strip a trailing /v1 from the OpenAI base URL to get server root."""
    return base_url.rstrip("/").removesuffix("/v1")


def _get_json(url: str, timeout: float = 3) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def list_models(base_url: str) -> list[dict] | None:
    """Return [{"name", "status"}] for every model llama-server knows about,
    loaded or not. None if the server is unreachable (so callers can tell
    'not running' from 'running but empty').

    Prefers the router-mode /models listing (has load states and includes
    unloaded models); falls back to /v1/models for a single-model server."""
    data = _get_json(root_url(base_url) + "/models")
    if data is not None:
        rows = []
        for m in data.get("data", []):
            status = m.get("status")
            if isinstance(status, dict):
                status = status.get("value", "unknown")
            rows.append({"name": m.get("id", "?"), "status": status or "unknown"})
        return rows
    data = _get_json(base_url.rstrip("/") + "/models")
    if data is None:
        return None
    return [{"name": m.get("id", "?"), "status": "loaded"} for m in data.get("data", [])]


def model_names(base_url: str) -> list[str] | None:
    rows = list_models(base_url)
    return None if rows is None else [r["name"] for r in rows]


def is_loaded(base_url: str, model: str) -> bool:
    """True only if the router reports `model` as already resident."""
    rows = list_models(base_url)
    if not rows:
        return False
    return any(r["name"] == model and r["status"] == "loaded" for r in rows)


def context_length(base_url: str, model: str) -> int | None:
    """Serving context length for a model, from /props — but ONLY if the model
    is already loaded.

    The status gate is not an optimization, it is the whole point.
    `/props?model=X` on an *unloaded* model does not return its metadata: the
    router starts loading X and blocks until the load finishes. Measured
    2026-08-16 on build 10448 — a 10 s curl against an unloaded model returned
    nothing and left it in state "loading". (This function's docstring used to
    claim the opposite. It was wrong.)

    That made boot destructive. server.py calls this on every WS connect for
    whatever `current_model` says, so opening the UI dragged that model into
    VRAM whether or not the user ever sent a message — and if a *different*
    model was already resident, the machine ended up trying to hold both.

    So: no load is ever started from here. An unloaded model returns None and
    the caller keeps its default. Router mode still loads on demand when a
    real completion request names a model, which is the only place a load
    should originate."""
    if not is_loaded(base_url, model):
        return None
    q = urllib.parse.urlencode({"model": model})
    props = _get_json(f"{root_url(base_url)}/props?{q}")
    if props:
        n_ctx = props.get("default_generation_settings", {}).get("n_ctx")
        if n_ctx:
            return int(n_ctx)
    return None


def is_up(base_url: str) -> bool:
    return _get_json(root_url(base_url) + "/health", timeout=2) is not None


def _post_json(url: str, payload: dict, timeout: float = 30) -> dict | None:
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def load_model(base_url: str, model: str) -> bool:
    """Router-mode load. Slow (tens of seconds for a big model) — call it
    off-loop. Loading also happens implicitly when a request names a model."""
    r = _post_json(root_url(base_url) + "/models/load", {"model": model}, timeout=300)
    return bool(r and r.get("success"))


def unload_model(base_url: str, model: str) -> bool:
    r = _post_json(root_url(base_url) + "/models/unload", {"model": model}, timeout=30)
    return bool(r and r.get("success"))


# ── GPU / RAM sampling (Windows) ─────────────────────────────────────────────
# Perf counters via a PowerShell one-shot. ~1-2 s per sample, so results are
# cached; callers go through asyncio.to_thread (subprocess = blocking).

_MEM_CACHE: dict = {"ts": 0.0, "data": None}
_MEM_TTL = 5.0

_MEM_PS = (
    "$s=(Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage',"
    "'\\GPU Process Memory(*)\\Shared Usage' -ErrorAction SilentlyContinue).CounterSamples;"
    "$a=((Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue"
    ").CounterSamples | Where-Object {$_.CookedValue -gt 0} | Measure-Object CookedValue -Sum).Sum;"
    "$procs=@(Get-Process llama-server -ErrorAction SilentlyContinue | ForEach-Object {"
    "$p=$_.Id; $tag=('pid_'+$p+'_');"
    "[pscustomobject]@{pid=$p;"
    "vram=[long](($s | Where-Object {$_.Path -like '*dedicated*' -and $_.InstanceName -like ('*'+$tag+'*')} | Measure-Object CookedValue -Sum).Sum);"
    "shared=[long](($s | Where-Object {$_.Path -like '*shared*' -and $_.InstanceName -like ('*'+$tag+'*')} | Measure-Object CookedValue -Sum).Sum);"
    "ram=[long]$_.WorkingSet64} });"
    "@{adapter_total=[long]$a; procs=$procs} | ConvertTo-Json -Compress"
)


def memory_snapshot() -> dict | None:
    """{'adapter_total': bytes, 'procs': [{'pid','vram','shared','ram'}]} for
    every llama-server process, or None off-Windows / on failure. `shared` is
    GPU-visible system RAM — the spillover number. Cached for a few seconds;
    blocking (~1-2 s on a cache miss) so call via asyncio.to_thread."""
    if platform.system() != "Windows":
        return None
    now = time.time()
    if _MEM_CACHE["data"] is not None and now - _MEM_CACHE["ts"] < _MEM_TTL:
        return _MEM_CACHE["data"]
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _MEM_PS],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        data = json.loads(out) if out else None
        if data is not None and isinstance(data.get("procs"), dict):
            data["procs"] = [data["procs"]]  # ConvertTo-Json unwraps 1-elem arrays
    except Exception:
        data = None
    _MEM_CACHE.update(ts=now, data=data)
    return data


_spawned: subprocess.Popen | None = None


def ensure_running(base_url: str, exe: str, args: list[str], log_path) -> str:
    """Health-check llama-server; if down and `exe` resolves, spawn it
    detached in router mode and wait for /health. Returns a one-line status
    for the preflight log. The spawned process is terminated atexit so a
    harness restart doesn't strand a second server on the port."""
    global _spawned
    if is_up(base_url):
        return f"llama-server up at {base_url}"
    path = shutil.which(exe)
    if not path:
        return (f"llama-server not reachable at {base_url} and '{exe}' not on PATH — "
                f"start it yourself or fix LLAMA_SERVER_EXE in config.py")
    # stdout/stderr go to a log file, never a pipe: nothing reads the pipe, and
    # an unread pipe eventually fills and deadlocks the child.
    log = open(log_path, "a", encoding="utf-8", errors="replace")
    try:
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        _spawned = subprocess.Popen(
            [path, *args],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    except OSError as e:
        log.close()
        return f"failed to spawn llama-server: {e}"
    atexit.register(_stop_spawned)
    import time
    for _ in range(60):  # models-dir scan + first bind can take a few seconds
        if _spawned.poll() is not None:
            return (f"llama-server exited immediately (rc={_spawned.returncode}) — "
                    f"see {log_path}")
        if is_up(base_url):
            return f"spawned llama-server (pid {_spawned.pid}), log: {log_path}"
        time.sleep(0.5)
    return f"spawned llama-server (pid {_spawned.pid}) but /health never came up — see {log_path}"


def _stop_spawned() -> None:
    global _spawned
    if _spawned is not None and _spawned.poll() is None:
        _spawned.terminate()
        try:
            _spawned.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _spawned.kill()
    _spawned = None
