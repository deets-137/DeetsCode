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
import shutil
import subprocess
import urllib.parse
import urllib.request


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


def context_length(base_url: str, model: str) -> int | None:
    """Serving context length for a model, from /props. Loads nothing: an
    unloaded model just returns None and the caller keeps its default."""
    q = urllib.parse.urlencode({"model": model})
    for url in (f"{root_url(base_url)}/props?{q}", f"{root_url(base_url)}/props"):
        props = _get_json(url)
        if props:
            n_ctx = props.get("default_generation_settings", {}).get("n_ctx")
            if n_ctx:
                return int(n_ctx)
    return None


def is_up(base_url: str) -> bool:
    return _get_json(root_url(base_url) + "/health", timeout=2) is not None


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
