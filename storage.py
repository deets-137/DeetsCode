"""
Shared SQLite store for game state across tool packs (chess, dnd, mafia, ...).

Design:
  - One DB file at project root: storage.db (gitignored).
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

_DB_PATH = Path(__file__).parent / "storage.db"
_conn: Optional[sqlite3.Connection] = None

_ID_ALPHABET = string.digits + string.ascii_lowercase  # base36
_ID_LEN = 4
_ID_MAX_RETRIES = 20


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
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
