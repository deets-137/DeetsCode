"""HarnessContext — the dependency-injected handle passed to tier-3 panel
handlers (app_harness.md §4, decision D4).

Every tier-3 handler that declares a `harness_ctx` keyword parameter gets one.
Panels belonging to an app can read/write shared per-app-instance state,
open bundled databases, and publish events to their peer panels. Non-app
panels receive a context with `app_id=None` whose state methods raise —
forward-compatible without a signature break.

Windows note: the spec's example instance ids used colons ("hoops:game-3");
colons are illegal in NTFS filenames, so app instance ids use dots instead
("hoops.game-3") and state DB filenames are sanitized defensively anyway.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from apps import loader as apps_loader

_UNSET = object()

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_filename(stem: str) -> str:
    return _FILENAME_SAFE_RE.sub("_", stem) or "default"


class StateSchemaMismatch(Exception):
    """A state row was written under a different app state_schema_version.
    The host catches this at render time and offers a reset (D11 — no
    auto-migration in v1)."""

    def __init__(self, app_id: str, found: int, expected: int):
        self.app_id = app_id
        self.found = found
        self.expected = expected
        super().__init__(
            f"app '{app_id}' state is schema v{found}, bundle expects v{expected}"
        )


class HarnessContext:
    def __init__(
        self,
        app_id: Optional[str],
        instance_id: Optional[str],
        app_instance: Optional[str] = None,
        config: Optional[dict] = None,
    ):
        self.app_id = app_id
        # The *panel* instance id from the layout (e.g. "hoops.g1.board").
        self.instance_id = instance_id
        # The *app* instance id shared by all of one launch's panels
        # (e.g. "hoops.g1"). Defaults to "<app>.default" for app panels
        # placed in the layout by hand rather than the launcher.
        self.app_instance = app_instance or (f"{app_id}.default" if app_id else None)
        self.config = config or {}
        # app_event() appends here; the caller (panels/loader.render_view or
        # the action route in server.py) drains and broadcasts after the
        # handler returns. Keeps the handler synchronous-friendly.
        self.pending_events: list[dict] = []

    # ── paths ────────────────────────────────────────────────────────────
    @property
    def app_dir(self) -> Optional[Path]:
        return apps_loader.app_dir(self.app_id) if self.app_id else None

    def _require_app(self) -> None:
        if not self.app_id:
            raise RuntimeError(
                "this panel does not belong to an app — harness_ctx state/db/"
                "event methods are app-only (D4)"
            )

    # ── bundled data ─────────────────────────────────────────────────────
    def app_db(self, relpath: Optional[str] = None) -> sqlite3.Connection:
        """Open a database inside this app's bundle. Default: the single
        *.db under data/. Pass a bundle-relative path to disambiguate."""
        self._require_app()
        base = self.app_dir
        if relpath is not None:
            target = (base / relpath).resolve()
            if not target.is_relative_to(base.resolve()):
                raise ValueError("app_db path escapes the app bundle")
        else:
            candidates = sorted((base / "data").glob("*.db")) if (base / "data").is_dir() else []
            if len(candidates) != 1:
                raise FileNotFoundError(
                    f"app_db(): expected exactly one data/*.db in app '{self.app_id}', "
                    f"found {len(candidates)} — pass an explicit relpath"
                )
            target = candidates[0]
        if not target.is_file():
            raise FileNotFoundError(f"app_db(): no database at {target}")
        return sqlite3.connect(target)

    # ── shared state ─────────────────────────────────────────────────────
    def _state_db_path(self) -> Path:
        return apps_loader.state_dir(self.app_id) / f"{_safe_filename(self.app_instance)}.db"

    def _state_conn(self) -> sqlite3.Connection:
        path = self._state_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS state (
              key            TEXT PRIMARY KEY,
              value          TEXT NOT NULL,
              schema_version INTEGER NOT NULL,
              updated_at     INTEGER NOT NULL
            )
            """
        )
        return conn

    def _schema_version(self) -> int:
        m = apps_loader.get(self.app_id)
        return m.state_schema_version if m else 1

    def app_state(self, key: str, value: Any = _UNSET) -> Any:
        """Read (one arg) or write (two args) a JSON value in this app
        instance's shared state. Reads of rows written under a different
        state_schema_version raise StateSchemaMismatch — the host renders
        the reset banner (D11)."""
        self._require_app()
        expected = self._schema_version()
        conn = self._state_conn()
        try:
            if value is _UNSET:
                row = conn.execute(
                    "SELECT value, schema_version FROM state WHERE key = ?", (key,)
                ).fetchone()
                if row is None:
                    return None
                if row[1] != expected:
                    raise StateSchemaMismatch(self.app_id, row[1], expected)
                return json.loads(row[0])
            conn.execute(
                "INSERT INTO state (key, value, schema_version, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                (key, json.dumps(value), expected, int(time.time())),
            )
            conn.commit()
            return value
        finally:
            conn.close()

    def reset_state(self) -> int:
        """Delete every state DB for this app instance. Returns files removed.
        Used by the schema-mismatch reset flow."""
        self._require_app()
        path = self._state_db_path()
        if path.is_file():
            path.unlink()
            return 1
        return 0

    # ── app events ───────────────────────────────────────────────────────
    def app_event(self, name: str, payload: Optional[dict] = None) -> None:
        """Publish an event to this app instance's panels (Phase C). Queued
        on the context; the host broadcasts after the handler returns."""
        self._require_app()
        self.pending_events.append({
            "app_id": self.app_id,
            "app_instance": self.app_instance,
            "event_name": str(name),
            "payload": payload if isinstance(payload, dict) else {},
        })
