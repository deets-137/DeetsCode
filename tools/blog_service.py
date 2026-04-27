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
    global _BLOG_IMPORTED
    if _BLOG_IMPORTED:
        return
    blog_dir = str(paths.BLOG_DIR)
    if blog_dir not in sys.path:
        sys.path.insert(0, blog_dir)
    _BLOG_IMPORTED = True


def _repo():
    _ensure_blog_on_path()
    from app import repo  # type: ignore
    return repo


def _itunes():
    _ensure_blog_on_path()
    from app import itunes  # type: ignore
    return itunes


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


# ── Preview / deploy ─────────────────────────────────────────────────────────

def preview_url(port: int = 8080) -> str:
    return f"http://localhost:{port}"


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
