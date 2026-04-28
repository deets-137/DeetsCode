"""Shared service layer for the harness `blog` mode.

Used by BOTH the model tools (tools/blog.py) AND the panel WS handlers
(server.py blog_* messages). Single source of truth — if the model creates
a draft, the panel sees it instantly, and vice versa.

Imports the live blog repo at paths.BLOG_DIR by injecting it into sys.path
on first use. The blog has its own venv with pydantic-settings etc., but
we only need stdlib + the lightweight `repo` module + httpx for itunes.

Functions return plain dict / list payloads (JSON-friendly) rather than
formatted strings. The model-tool layer formats them for output; the WS
layer ships them straight to the browser.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import uuid
from datetime import date as _date
from pathlib import Path
from typing import Any

import paths

_BLOG_IMPORTED = False


def _ensure_blog_on_path() -> None:
    """Make the blog repo importable from the harness process.

    The blog's `app.settings` uses pydantic-settings with relative defaults
    (`./data/blog.sqlite`, `./data/media`) that are resolved against the CWD
    of whatever process imports it. From the harness, that CWD is the harness
    dir, which means SQLite would try to open a non-existent path. Pin DB_PATH
    and MEDIA_DIR to absolute paths under BLOG_DIR before the import so the
    blog-side `Settings()` instance picks up the correct location.
    """
    global _BLOG_IMPORTED
    if _BLOG_IMPORTED:
        return
    blog_dir = paths.BLOG_DIR
    db_path = blog_dir / "data" / "blog.sqlite"
    media_dir = blog_dir / "data" / "media"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)
    # Load BLOG_DIR/.env into os.environ first so pydantic-settings (which
    # would otherwise look for `.env` relative to harness CWD and miss it)
    # picks up TMDB_API_KEY, JOURNAL_PASSPHRASE, etc. We set these only when
    # not already in the environment, so a parent shell can still override.
    env_file = blog_dir / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                os.environ.setdefault(k, v)
    # Hard-override (not setdefault) — guards against a stray relative DB_PATH
    # leaking in from harness/.env or a parent shell that would otherwise win
    # over our default and put SQLite at the wrong CWD-relative path.
    os.environ["DB_PATH"]   = str(db_path)
    os.environ["MEDIA_DIR"] = str(media_dir)
    blog_dir_str = str(blog_dir)
    if blog_dir_str not in sys.path:
        sys.path.insert(0, blog_dir_str)
    _BLOG_IMPORTED = True


_REPO_INITED = False


def _repo():
    global _REPO_INITED
    _ensure_blog_on_path()
    from app import repo  # type: ignore
    if not _REPO_INITED:
        # Idempotent — applies schema if the DB file is fresh, no-op otherwise.
        # Belt-and-suspenders in case the harness hits the DB before the blog
        # server has had a chance to run its lifespan startup.
        repo.init_db()
        _REPO_INITED = True
    return repo


def _itunes():
    _ensure_blog_on_path()
    from app import itunes  # type: ignore
    return itunes


def _tmdb():
    _ensure_blog_on_path()
    from app import tmdb  # type: ignore
    return tmdb


# ── Slug helpers ─────────────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = _SLUG_RE.sub("-", s.lower()).strip("-")
    return s or uuid.uuid4().hex[:8]


def _unique_slug(base: str) -> str:
    repo = _repo()
    slug = base
    n = 2
    while repo.get_post_by_slug(slug):
        slug = f"{base}-{n}"
        n += 1
    return slug


def _today_iso() -> str:
    return _date.today().isoformat()


# ── Posts ────────────────────────────────────────────────────────────────────

def list_posts(*, kind: str | None = None, status: str | None = None,
               limit: int = 100) -> list[dict[str, Any]]:
    """List posts (drafts + published by default; pass status to filter)."""
    return _repo().list_posts(kind=kind, status=status, limit=limit)


def get_post(slug: str) -> dict[str, Any] | None:
    return _repo().get_post_by_slug(slug)


def create_post(*, kind: str, title: str, date: str | None = None,
                meta: dict[str, Any] | None = None,
                body_md: str | None = None,
                media_path: str | None = None,
                locked: bool = False,
                slug: str | None = None) -> dict[str, Any]:
    if kind not in ("song", "movie", "journal"):
        raise ValueError(f"unknown kind: {kind}")
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    base = _slugify(slug or title)
    final_slug = _unique_slug(base)
    return _repo().create_post(
        kind=kind, slug=final_slug, title=title,
        date=date or _today_iso(),
        meta=meta or {}, body_md=body_md, media_path=media_path,
        locked=locked,
    )


def update_post(slug: str, **fields: Any) -> dict[str, Any]:
    repo = _repo()
    p = repo.get_post_by_slug(slug)
    if not p:
        raise LookupError(f"no post with slug '{slug}'")
    repo.update_post(p["id"], **fields)
    return repo.get_post_by_slug(slug)  # type: ignore[return-value]


def publish(slug: str) -> dict[str, Any]:
    repo = _repo()
    p = repo.get_post_by_slug(slug)
    if not p:
        raise LookupError(f"no post with slug '{slug}'")
    repo.publish_post(slug)
    return repo.get_post_by_slug(slug)  # type: ignore[return-value]


def unpublish(slug: str) -> dict[str, Any]:
    repo = _repo()
    p = repo.get_post_by_slug(slug)
    if not p:
        raise LookupError(f"no post with slug '{slug}'")
    repo.unpublish_post(slug)
    return repo.get_post_by_slug(slug)  # type: ignore[return-value]


def delete_post(slug: str) -> None:
    repo = _repo()
    p = repo.get_post_by_slug(slug)
    if not p:
        raise LookupError(f"no post with slug '{slug}'")
    repo.delete_post(p["id"])


# ── Media ────────────────────────────────────────────────────────────────────

def attach_media(slug: str, src_path: str | Path) -> dict[str, Any]:
    """Copy a local file into the blog's media dir and set posts.media_path.
    Returns the updated post.
    """
    repo = _repo()
    p = repo.get_post_by_slug(slug)
    if not p:
        raise LookupError(f"no post with slug '{slug}'")

    src = Path(src_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"source file not found: {src}")

    media_root = paths.BLOG_DIR / "data" / "media" / p["id"]
    media_root.mkdir(parents=True, exist_ok=True)
    dest = media_root / src.name
    shutil.copy2(src, dest)

    rel_url = f"/media/{p['id']}/{src.name}"
    repo.update_post(p["id"], media_path=rel_url)
    return repo.get_post_by_slug(slug)  # type: ignore[return-value]


def attach_media_bytes(slug: str, filename: str, content: bytes) -> dict[str, Any]:
    """Write raw bytes (from a drag-drop upload) into the blog's media dir
    and set posts.media_path. Sibling of attach_media — no source path on disk.
    """
    repo = _repo()
    p = repo.get_post_by_slug(slug)
    if not p:
        raise LookupError(f"no post with slug '{slug}'")

    safe_name = Path(filename).name  # strip any directory components
    if not safe_name or safe_name in (".", ".."):
        raise ValueError(f"invalid filename: {filename!r}")

    media_root = paths.BLOG_DIR / "data" / "media" / p["id"]
    media_root.mkdir(parents=True, exist_ok=True)
    dest = media_root / safe_name
    dest.write_bytes(content)

    rel_url = f"/media/{p['id']}/{safe_name}"
    repo.update_post(p["id"], media_path=rel_url)
    return repo.get_post_by_slug(slug)  # type: ignore[return-value]


# ── Comments ─────────────────────────────────────────────────────────────────

def list_recent_comments(limit: int = 50) -> list[dict[str, Any]]:
    return _repo().list_recent_comments(limit=limit)


def list_comments_for(slug: str) -> list[dict[str, Any]]:
    repo = _repo()
    p = repo.get_post_by_slug(slug)
    if not p:
        raise LookupError(f"no post with slug '{slug}'")
    return repo.list_comments(p["id"])


def delete_comment(comment_id: str) -> None:
    _repo().delete_comment(comment_id)


# ── iTunes Search (async) ────────────────────────────────────────────────────

async def lookup_song(query: str, limit: int = 10) -> list[dict[str, Any]]:
    return await _itunes().search_songs(query, limit=limit)


def lookup_song_sync(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Sync wrapper for use from synchronous tool dispatchers."""
    return asyncio.run(lookup_song(query, limit=limit))


# ── TMDB Search (async) ──────────────────────────────────────────────────────

async def lookup_movie(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search TMDB and enrich with director/runtime per hit."""
    return await _tmdb().search_with_details(query, limit=limit)


def lookup_movie_sync(query: str, limit: int = 5) -> list[dict[str, Any]]:
    return asyncio.run(lookup_movie(query, limit=limit))


# ── Preview / deploy ─────────────────────────────────────────────────────────

def preview_url(port: int = 8080) -> str:
    return f"http://localhost:{port}"


def get_passphrase() -> str:
    """Read the site-wide journal/lock passphrase from BLOG_DIR/.env.
    Falls back to the live os.environ value (which we mirror on import).
    """
    _ensure_blog_on_path()
    env_file = paths.BLOG_DIR / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("JOURNAL_PASSPHRASE="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("JOURNAL_PASSPHRASE", "")


def set_passphrase(value: str) -> str:
    """Update JOURNAL_PASSPHRASE in BLOG_DIR/.env (creating the file if
    needed) and refresh os.environ so the running blog Settings() picks
    it up on its next read. Returns the new value.

    NOTE: pydantic-settings caches `Settings()` at module import time, so
    the public blog server still needs a restart for /unlock to use the
    new passphrase. This function makes the change durable; the harness
    UI surfaces a "restart blog server" hint after a write.
    """
    _ensure_blog_on_path()
    env_file = paths.BLOG_DIR / ".env"
    new_lines: list[str] = []
    found = False
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("JOURNAL_PASSPHRASE="):
                new_lines.append(f"JOURNAL_PASSPHRASE={value}")
                found = True
            else:
                new_lines.append(line)
    if not found:
        new_lines.append(f"JOURNAL_PASSPHRASE={value}")
    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.environ["JOURNAL_PASSPHRASE"] = value
    return value


def run_deploy() -> dict[str, Any]:
    """Invoke the blog repo's deploy.sh. Returns {ok, stdout, stderr}.
    Synchronous + blocking — UI should warn the user before triggering.
    """
    deploy = paths.BLOG_DIR / "deploy.sh"
    if not deploy.exists():
        raise FileNotFoundError(f"{deploy} not found")
    proc = subprocess.run(
        ["bash", str(deploy)],
        cwd=str(paths.BLOG_DIR),
        capture_output=True, text=True, timeout=600,
    )
    return {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
