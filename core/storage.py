"""
Shared SQLite store for game state across tool packs (chess, dnd, mafia, ...).

Design:
  - One DB file under .harness/: storage.db (gitignored).
  - Stable table schema, mutable payloads in `state_json`. Adding a game
    attribute = change the JSON shape, no migration.
  - Additive-only DDL. Safe to re-run on every startup.
  - Short base36 game IDs, user-quotable. Collision-retry on insert.

Tables:
  games    — one row per game, any type. state_json holds the game-specific blob.
  moves    — append-only move log per game. Useful for replay/reporting.
  players  — display-name cache keyed by Discord user_id.
"""

import json
import random
import sqlite3
import string
import time
from pathlib import Path
from typing import Any, Optional

from paths import DB_PATH as _DB_PATH
_conn: Optional[sqlite3.Connection] = None

_ID_ALPHABET = string.digits + string.ascii_lowercase  # base36
_ID_LEN = 4
_ID_MAX_RETRIES = 20


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(_DB_PATH, isolation_level=None)  # autocommit
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _init_schema(_conn)
    return _conn


def _init_schema(c: sqlite3.Connection) -> None:
    # Additive-only: every statement is idempotent. Never DROP or rename.
    c.executescript("""
        CREATE TABLE IF NOT EXISTS games (
            game_id       TEXT PRIMARY KEY,
            game_type     TEXT NOT NULL,
            channel_id    TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'active',
            started_at    INTEGER NOT NULL,
            ended_at      INTEGER,
            created_by    TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            state_json    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_games_channel_status
            ON games(channel_id, status);
        CREATE INDEX IF NOT EXISTS idx_games_type_status
            ON games(game_type, status);
        CREATE INDEX IF NOT EXISTS idx_games_created_by
            ON games(created_by);

        CREATE TABLE IF NOT EXISTS moves (
            game_id     TEXT NOT NULL,
            seq         INTEGER NOT NULL,
            player_id   TEXT,
            move_json   TEXT NOT NULL,
            annotation  TEXT,
            ts          INTEGER NOT NULL,
            PRIMARY KEY (game_id, seq),
            FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS players (
            user_id       TEXT PRIMARY KEY,
            display_name  TEXT NOT NULL,
            last_seen     INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name     TEXT NOT NULL,
            channel_name  TEXT NOT NULL,
            text          TEXT NOT NULL,
            ts            INTEGER NOT NULL,
            status        TEXT NOT NULL DEFAULT 'open',
            closed_at     INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_notes_channel_ts
            ON notes(channel_name, ts);

        CREATE TABLE IF NOT EXISTS stats (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_name  TEXT NOT NULL,
            user_name     TEXT NOT NULL,
            mode          TEXT,
            model         TEXT,
            status        TEXT NOT NULL,
            duration_ms   INTEGER NOT NULL,
            started_at    INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_stats_channel_ts
            ON stats(channel_name, started_at);
        CREATE INDEX IF NOT EXISTS idx_stats_model
            ON stats(model);

        CREATE TABLE IF NOT EXISTS events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            ts           INTEGER NOT NULL,
            type         TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_session_id
            ON events(session_id, id);
        CREATE INDEX IF NOT EXISTS idx_events_type
            ON events(type);

        -- system_log: append-only stream of UI interaction events emitted by
        -- panel-shell.js (clicks, state transitions, bin migrations, custom
        -- panel events). Primary purpose is analytics — "is this panel ever
        -- used" — with a secondary "what just happened" debug surface. Kept
        -- intentionally generic so future signal types (lifecycle, focus,
        -- scroll) can be added by emitting a new `kind` value without DDL.
        --   ts        unix ms (client-side at emission time)
        --   instance  layout-instance id (e.g. "tool_log"), or "_shell" for
        --             non-panel events
        --   panel     manifest name (denormalized — saves a join for the
        --             common "usage by panel-type" aggregation). Nullable for
        --             shell-level events.
        --   kind      "click" | "state" | "bin" | "custom" | future...
        --   meta_json arbitrary JSON payload — schema is per-kind, set by
        --             panel-shell's auto-instrument or by panel scripts.
        CREATE TABLE IF NOT EXISTS system_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          INTEGER NOT NULL,
            instance    TEXT NOT NULL,
            panel       TEXT,
            kind        TEXT NOT NULL,
            meta_json   TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_system_log_ts
            ON system_log(ts);
        CREATE INDEX IF NOT EXISTS idx_system_log_instance_ts
            ON system_log(instance, ts);
        CREATE INDEX IF NOT EXISTS idx_system_log_kind_ts
            ON system_log(kind, ts);
    """)
    # Additive migrations for pre-existing DBs. Each statement is safe to
    # re-run; we swallow "duplicate column" errors rather than version-track.
    _additive = [
        "ALTER TABLE games ADD COLUMN created_by TEXT",
        "ALTER TABLE games ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE moves ADD COLUMN annotation TEXT",
        "ALTER TABLE notes ADD COLUMN status TEXT NOT NULL DEFAULT 'open'",
        "ALTER TABLE notes ADD COLUMN closed_at INTEGER",
    ]
    for stmt in _additive:
        try:
            c.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
    # Indexes that reference additive columns must be created after migrations.
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_channel_status "
        "ON notes(channel_name, status)"
    )


# ─── Game IDs ────────────────────────────────────────────────────────────────

def _gen_id() -> str:
    return "".join(random.choices(_ID_ALPHABET, k=_ID_LEN))


# ─── Games ───────────────────────────────────────────────────────────────────

def create_game(
    game_type: str,
    channel_id: str,
    state: dict,
    created_by: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Insert a new game, return its short id. Retries on PK collision."""
    c = _db()
    now = int(time.time())
    payload = json.dumps(state)
    meta = json.dumps(metadata or {})
    for _ in range(_ID_MAX_RETRIES):
        gid = _gen_id()
        try:
            c.execute(
                "INSERT INTO games (game_id, game_type, channel_id, status, started_at, "
                "created_by, metadata_json, state_json) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
                (gid, game_type, channel_id, now, created_by, meta, payload),
            )
            return gid
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError(f"Could not allocate unique game_id after {_ID_MAX_RETRIES} tries")


def load_game(game_id: str) -> Optional[dict]:
    """Return a dict with all game columns + parsed state, or None."""
    row = _db().execute("SELECT * FROM games WHERE game_id = ?", (game_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["state"] = json.loads(out.pop("state_json"))
    out["metadata"] = json.loads(out.pop("metadata_json") or "{}")
    return out


def save_state(game_id: str, state: dict) -> None:
    _db().execute(
        "UPDATE games SET state_json = ? WHERE game_id = ?",
        (json.dumps(state), game_id),
    )


def end_game(game_id: str, status: str = "ended") -> None:
    _db().execute(
        "UPDATE games SET status = ?, ended_at = ? WHERE game_id = ?",
        (status, int(time.time()), game_id),
    )


def list_games(
    channel_id: Optional[str] = None,
    game_type: Optional[str] = None,
    status: Optional[str] = None,
    created_by: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    clauses, params = [], []
    if channel_id is not None:
        clauses.append("channel_id = ?"); params.append(channel_id)
    if game_type is not None:
        clauses.append("game_type = ?"); params.append(game_type)
    if status is not None:
        clauses.append("status = ?"); params.append(status)
    if created_by is not None:
        clauses.append("created_by = ?"); params.append(created_by)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = _db().execute(
        f"SELECT game_id, game_type, channel_id, status, started_at, ended_at, created_by "
        f"FROM games{where} ORDER BY started_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def update_metadata(game_id: str, metadata: dict) -> None:
    """Overwrite the metadata blob. Caller passes the full dict."""
    _db().execute(
        "UPDATE games SET metadata_json = ? WHERE game_id = ?",
        (json.dumps(metadata), game_id),
    )


# ─── Moves ───────────────────────────────────────────────────────────────────

def record_move(
    game_id: str,
    player_id: Optional[str],
    move: dict,
    annotation: Optional[str] = None,
) -> int:
    """Append a move; returns the assigned seq. `annotation` is a PGN NAG
    like '!', '?!', '!!' or freeform text — optional."""
    c = _db()
    row = c.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM moves WHERE game_id = ?",
        (game_id,),
    ).fetchone()
    seq = row["next"]
    c.execute(
        "INSERT INTO moves (game_id, seq, player_id, move_json, annotation, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (game_id, seq, player_id, json.dumps(move), annotation, int(time.time())),
    )
    return seq


def game_history(game_id: str) -> list[dict]:
    rows = _db().execute(
        "SELECT seq, player_id, move_json, annotation, ts FROM moves "
        "WHERE game_id = ? ORDER BY seq",
        (game_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["move"] = json.loads(d.pop("move_json"))
        out.append(d)
    return out


# ─── Players ─────────────────────────────────────────────────────────────────

def upsert_player(user_id: str, display_name: str) -> None:
    _db().execute(
        "INSERT INTO players (user_id, display_name, last_seen) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET display_name = excluded.display_name, "
        "last_seen = excluded.last_seen",
        (user_id, display_name, int(time.time())),
    )


def get_player(user_id: str) -> Optional[dict]:
    row = _db().execute("SELECT * FROM players WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


# ─── Notes ───────────────────────────────────────────────────────────────────

def add_note(user_name: str, channel_name: str, text: str) -> int:
    cur = _db().execute(
        "INSERT INTO notes (user_name, channel_name, text, ts) VALUES (?, ?, ?, ?)",
        (user_name, channel_name, text, int(time.time())),
    )
    return cur.lastrowid


def list_notes(
    channel_name: Optional[str] = None,
    status: Optional[str] = "open",
    limit: int = 20,
) -> list[dict]:
    """List notes. status='open'|'closed'|None (None → all)."""
    clauses, params = [], []
    if channel_name is not None:
        clauses.append("channel_name = ?"); params.append(channel_name)
    if status is not None:
        clauses.append("status = ?"); params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = _db().execute(
        f"SELECT id, user_name, channel_name, text, ts, status, closed_at "
        f"FROM notes{where} ORDER BY ts DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


# ─── Stats ───────────────────────────────────────────────────────────────────

def record_stat(
    channel_name: str,
    user_name: str,
    duration_ms: int,
    status: str,
    mode: Optional[str] = None,
    model: Optional[str] = None,
    started_at: Optional[int] = None,
) -> int:
    cur = _db().execute(
        "INSERT INTO stats (channel_name, user_name, mode, model, status, "
        "duration_ms, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            channel_name,
            user_name,
            mode,
            model,
            status,
            int(duration_ms),
            int(started_at if started_at is not None else time.time()),
        ),
    )
    return cur.lastrowid


def stats_summary(
    channel_name: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = 200,
) -> dict:
    """Aggregate the last N stat rows. Filtered to a channel and/or mode."""
    clauses, params = [], []
    if channel_name is not None:
        clauses.append("channel_name = ?"); params.append(channel_name)
    if mode is not None:
        clauses.append("mode = ?"); params.append(mode)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = _db().execute(
        f"SELECT duration_ms, status, model, mode FROM stats{where} "
        f"ORDER BY started_at DESC LIMIT ?",
        params,
    ).fetchall()
    rows = [dict(r) for r in rows]
    if not rows:
        return {"count": 0, "by_model": {}}
    completed = [r for r in rows if r["status"] == "completed"]
    durations = [r["duration_ms"] for r in completed]
    by_model: dict[str, dict] = {}
    for r in completed:
        key = r["model"] or "(unknown)"
        d = by_model.setdefault(key, {"count": 0, "total_ms": 0, "max_ms": 0})
        d["count"] += 1
        d["total_ms"] += r["duration_ms"]
        d["max_ms"] = max(d["max_ms"], r["duration_ms"])
    for d in by_model.values():
        d["avg_ms"] = d["total_ms"] // d["count"]
    return {
        "count": len(rows),
        "completed": len(completed),
        "errors": len(rows) - len(completed),
        "avg_ms": (sum(durations) // len(durations)) if durations else 0,
        "min_ms": min(durations) if durations else 0,
        "max_ms": max(durations) if durations else 0,
        "by_model": by_model,
    }


# ─── Events (audit log for the spectator/debugger) ───────────────────────────

def record_event(session_id: str, type_: str, payload: dict) -> int:
    """Append one event. Returns the assigned id (monotonically increasing)."""
    cur = _db().execute(
        "INSERT INTO events (session_id, ts, type, payload_json) VALUES (?, ?, ?, ?)",
        (session_id, int(time.time() * 1000), type_, json.dumps(payload)),
    )
    return cur.lastrowid


def query_events(
    session_id: Optional[str] = None,
    since_id: int = 0,
    types: Optional[list[str]] = None,
    limit: int = 500,
) -> list[dict]:
    """Query events. since_id is exclusive — pass the last id you saw to tail."""
    clauses = ["id > ?"]
    params: list[Any] = [since_id]
    if session_id is not None:
        clauses.append("session_id = ?"); params.append(session_id)
    if types:
        placeholders = ",".join("?" for _ in types)
        clauses.append(f"type IN ({placeholders})")
        params.extend(types)
    where = " WHERE " + " AND ".join(clauses)
    params.append(limit)
    rows = _db().execute(
        f"SELECT id, session_id, ts, type, payload_json FROM events{where} "
        f"ORDER BY id LIMIT ?",
        params,
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json"))
        out.append(d)
    return out


def list_event_sessions(limit: int = 50) -> list[dict]:
    """Recent sessions that have events, with counts + last activity."""
    rows = _db().execute(
        "SELECT session_id, COUNT(*) AS n, MAX(ts) AS last_ts, MAX(id) AS last_id "
        "FROM events GROUP BY session_id ORDER BY last_ts DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_note_status(note_id: int, status: str) -> bool:
    """Set a note's status. Returns True if the row existed."""
    if status not in ("open", "closed"):
        raise ValueError("status must be 'open' or 'closed'")
    closed_at = int(time.time()) if status == "closed" else None
    cur = _db().execute(
        "UPDATE notes SET status = ?, closed_at = ? WHERE id = ?",
        (status, closed_at, note_id),
    )
    return cur.rowcount > 0


# ── system_log (UI interaction stream) ───────────────────────────────────────
# Append-only; read mostly by analytics aggregations. See the table's
# block comment in _init_schema for the schema rationale and `kind` values.

def record_system_event(
    instance: str,
    panel: Optional[str],
    kind: str,
    meta: Optional[dict] = None,
    ts: Optional[int] = None,
) -> int:
    """Insert a single interaction event. Returns the rowid."""
    cur = _db().execute(
        "INSERT INTO system_log(ts, instance, panel, kind, meta_json) "
        "VALUES(?, ?, ?, ?, ?)",
        (
            ts if ts is not None else int(time.time() * 1000),
            instance, panel, kind,
            json.dumps(meta or {}, ensure_ascii=False),
        ),
    )
    return cur.lastrowid


def record_system_events(batch: list[dict]) -> int:
    """Bulk-insert a debounced batch from the client. Each dict needs
    `instance` + `kind`; `ts`, `panel`, `meta` are optional. Returns the
    number of rows inserted. Malformed entries are skipped silently — we
    don't want a junk frame to torpedo a whole batch."""
    if not batch:
        return 0
    rows: list[tuple] = []
    now = int(time.time() * 1000)
    for e in batch:
        if not isinstance(e, dict):
            continue
        instance = e.get("instance")
        kind = e.get("kind")
        if not isinstance(instance, str) or not isinstance(kind, str):
            continue
        panel = e.get("panel")
        if panel is not None and not isinstance(panel, str):
            panel = None
        ts = e.get("ts")
        if not isinstance(ts, int):
            ts = now
        meta = e.get("meta") if isinstance(e.get("meta"), dict) else {}
        rows.append((ts, instance, panel, kind, json.dumps(meta, ensure_ascii=False)))
    if not rows:
        return 0
    _db().executemany(
        "INSERT INTO system_log(ts, instance, panel, kind, meta_json) "
        "VALUES(?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def query_system_log(
    since: Optional[int] = None,
    until: Optional[int] = None,
    instance: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    """Recent interaction events, newest first. All filters are optional;
    `since`/`until` are unix ms timestamps. `limit` is capped at 5000 so a
    runaway query can't OOM the response."""
    clauses: list[str] = []
    params: list[Any] = []
    if since is not None:
        clauses.append("ts >= ?")
        params.append(since)
    if until is not None:
        clauses.append("ts <= ?")
        params.append(until)
    if instance is not None:
        clauses.append("instance = ?")
        params.append(instance)
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(min(max(1, limit), 5000))
    rows = _db().execute(
        f"SELECT id, ts, instance, panel, kind, meta_json FROM system_log{where} "
        f"ORDER BY ts DESC LIMIT ?",
        params,
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            d["meta"] = json.loads(d.pop("meta_json") or "{}")
        except json.JSONDecodeError:
            d["meta"] = {}
        out.append(d)
    return out


def panel_usage_summary(window_ms: Optional[int] = None) -> list[dict]:
    """Per-(instance, kind) interaction counts, newest-active first.
    `window_ms` filters to events within the last N ms (None = all time).
    Designed for the "which panels do I actually use" question."""
    clause = ""
    params: list[Any] = []
    if window_ms is not None:
        clause = " WHERE ts >= ?"
        params.append(int(time.time() * 1000) - int(window_ms))
    rows = _db().execute(
        f"SELECT instance, panel, kind, COUNT(*) AS n, MAX(ts) AS last_ts "
        f"FROM system_log{clause} "
        f"GROUP BY instance, kind "
        f"ORDER BY last_ts DESC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def prune_system_log(older_than_ms: int) -> int:
    """Delete events older than the given absolute unix-ms timestamp.
    Returns the number of rows deleted. Not called automatically — wire it
    into a maintenance task or call ad-hoc if storage.db grows uncomfortably."""
    cur = _db().execute("DELETE FROM system_log WHERE ts < ?", (older_than_ms,))
    return cur.rowcount
