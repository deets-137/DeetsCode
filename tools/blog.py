"""Blog tool pack — model-callable tools for the `blog` mode.

Wraps tools/blog_service.py. Both this module and the WS panel handlers
in server.py share that service layer, so anything the model does is
visible in the UI panels and vice versa.

Tools surface:
    blog_new_post       create a draft (kind, title, fields, body, locked)
    blog_list_posts     list drafts/published, optionally filtered by kind
    blog_update_post    edit any field on an existing draft
    blog_publish        flip draft → published
    blog_unpublish      reverse
    blog_delete_post    delete a draft outright
    blog_attach_media   copy a local file into media/ and set media_path
    blog_lookup_song    iTunes Search (returns top candidates)
    blog_list_comments  recent comments across all posts (moderation)
    blog_delete_comment delete a comment by id
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from . import blog_service as svc

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "blog_new_post",
            "description": (
                "Create a draft post in the DeetsOTD blog. `kind` is one of "
                "'song', 'movie', 'journal'. `title` is the public-facing title "
                "(also used to derive a URL slug). `fields` is a kind-specific "
                "dict — for song: {artist, album, genre, length_seconds, itunes_url, art_url}; "
                "for movie: {director, year, length_minutes, genre, rating}; "
                "for journal: {} (title is the header). `body_md` is Markdown — "
                "used for journal entries and movie notes; ignored for songs. "
                "`locked=true` only meaningful for journals (site-wide passphrase gate). "
                "Returns the new post (still draft — call blog_publish to publish)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind":    {"type": "string", "enum": ["song", "movie", "journal"]},
                    "title":   {"type": "string"},
                    "date":    {"type": "string", "description": "ISO date (YYYY-MM-DD). Defaults to today."},
                    "fields":  {"type": "object", "description": "Kind-specific metadata (see description)."},
                    "body_md": {"type": "string", "description": "Markdown body. Use for journals and movie notes."},
                    "locked":  {"type": "boolean", "description": "Journal only: hide behind site passphrase."},
                },
                "required": ["kind", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blog_list_posts",
            "description": "List posts. Filter by kind ('song'|'movie'|'journal') and/or status ('draft'|'published').",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind":   {"type": "string", "enum": ["song", "movie", "journal"]},
                    "status": {"type": "string", "enum": ["draft", "published"]},
                    "limit":  {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blog_update_post",
            "description": (
                "Edit fields on an existing post by slug. Pass any subset of "
                "{title, date, body_md, locked, status} as top-level keys, "
                "or `meta` as a dict to replace kind-specific metadata wholesale."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slug":    {"type": "string"},
                    "title":   {"type": "string"},
                    "date":    {"type": "string"},
                    "body_md": {"type": "string"},
                    "locked":  {"type": "boolean"},
                    "meta":    {"type": "object"},
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blog_publish",
            "description": "Flip a draft to published. Sets published_at to now.",
            "parameters": {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blog_unpublish",
            "description": "Revert a published post back to draft.",
            "parameters": {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blog_delete_post",
            "description": "Delete a post outright. Asks for slug. Comments on it are removed via FK cascade.",
            "parameters": {
                "type": "object",
                "properties": {"slug": {"type": "string"}},
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blog_attach_media",
            "description": (
                "Copy a local file (album art, movie poster, journal photo) into "
                "the blog's media folder and set posts.media_path. `src_path` "
                "must be readable on this machine."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slug":     {"type": "string"},
                    "src_path": {"type": "string"},
                },
                "required": ["slug", "src_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blog_lookup_movie",
            "description": (
                "Search TMDB for a movie. Returns up to N candidates with "
                "{title, year, director, length_minutes, genre, genres, "
                "poster_url, tmdb_url, overview}. Pre-fills movie-post "
                "metadata; pass the chosen result's fields into blog_new_post "
                "as `fields={director, year, length_minutes, genre, rating}`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text query, e.g. 'parasite 2019'"},
                    "limit": {"type": "integer", "description": "Max candidates (default 5)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blog_lookup_song",
            "description": (
                "Search iTunes for a song. Returns up to N candidates with "
                "{title, artist, album, genre, length_ms, art_url, itunes_url, "
                "preview_url, release}. Pre-fills song-of-the-day metadata; "
                "you typically pass the top result's fields into blog_new_post."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text query, e.g. 'pearl jam black'"},
                    "limit": {"type": "integer", "description": "Max candidates to return (default 5)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blog_list_comments",
            "description": "Recent comments across all posts. Use for moderation triage.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blog_delete_comment",
            "description": "Delete a comment by id (UUID from blog_list_comments).",
            "parameters": {
                "type": "object",
                "properties": {"comment_id": {"type": "string"}},
                "required": ["comment_id"],
            },
        },
    },
]


def _summary(p: dict[str, Any]) -> str:
    lines = [
        f"slug:    {p['slug']}",
        f"kind:    {p['kind']}",
        f"title:   {p['title']}",
        f"date:    {p['date']}",
        f"status:  {p['status']}",
    ]
    if p.get("locked"):
        lines.append("locked:  true")
    if p.get("media_path"):
        lines.append(f"media:   {p['media_path']}")
    if p.get("meta"):
        lines.append(f"meta:    {json.dumps(p['meta'], ensure_ascii=False)}")
    if p.get("body_md"):
        body = p["body_md"]
        if len(body) > 240:
            body = body[:240] + "…"
        lines.append(f"body:    {body}")
    return "\n".join(lines)


def execute_tool(
    name: str,
    args: dict,
    session_id: str,
    project_dir: Path,
    user_name: Optional[str] = None,
) -> str:
    try:
        if name == "blog_new_post":
            kind   = args["kind"]
            title  = args["title"]
            date_  = args.get("date")
            fields = args.get("fields") or {}
            body   = args.get("body_md")
            locked = bool(args.get("locked", False))
            p = svc.create_post(
                kind=kind, title=title, date=date_,
                meta=fields, body_md=body, locked=locked,
            )
            return f"Draft created.\n{_summary(p)}"

        if name == "blog_list_posts":
            posts = svc.list_posts(
                kind=args.get("kind"),
                status=args.get("status"),
                limit=int(args.get("limit") or 100),
            )
            if not posts:
                return "No posts."
            lines = [f"{len(posts)} post(s):"]
            for p in posts:
                lines.append(
                    f"  [{p['status']:9}] {p['kind']:7}  {p['date']}  {p['slug']:30}  {p['title']}"
                )
            return "\n".join(lines)

        if name == "blog_update_post":
            slug = args["slug"]
            update_fields = {k: v for k, v in args.items() if k != "slug"}
            p = svc.update_post(slug, **update_fields)
            return f"Updated.\n{_summary(p)}"

        if name == "blog_publish":
            p = svc.publish(args["slug"])
            return f"Published.\n{_summary(p)}"

        if name == "blog_unpublish":
            p = svc.unpublish(args["slug"])
            return f"Unpublished.\n{_summary(p)}"

        if name == "blog_delete_post":
            svc.delete_post(args["slug"])
            return f"Deleted post '{args['slug']}'."

        if name == "blog_attach_media":
            p = svc.attach_media(args["slug"], args["src_path"])
            return f"Media attached.\n{_summary(p)}"

        if name == "blog_lookup_movie":
            limit = int(args.get("limit") or 5)
            results = svc.lookup_movie_sync(args["query"], limit=limit)
            if not results:
                return "No matches."
            lines = [f"{len(results)} result(s):"]
            for i, r in enumerate(results, 1):
                yr = f" ({r['year']})" if r.get("year") else ""
                rt = f" · {r['length_minutes']} min" if r.get("length_minutes") else ""
                lines.append(
                    f"  {i}. {r.get('title')}{yr} — dir. {r.get('director') or '?'}"
                    f" · {r.get('genre') or ''}{rt}"
                )
                lines.append(f"     poster: {r.get('poster_url')}")
                lines.append(f"     tmdb:   {r.get('tmdb_url')}")
            return "\n".join(lines)

        if name == "blog_lookup_song":
            limit = int(args.get("limit") or 5)
            results = svc.lookup_song_sync(args["query"], limit=limit)
            if not results:
                return "No matches."
            lines = [f"{len(results)} result(s):"]
            for i, r in enumerate(results, 1):
                length = ""
                if r.get("length_ms"):
                    secs = int(r["length_ms"] // 1000)
                    length = f" [{secs // 60}:{secs % 60:02d}]"
                lines.append(
                    f"  {i}. {r.get('title')} — {r.get('artist')}"
                    f" · {r.get('album', '')} · {r.get('genre', '')}{length}"
                )
                lines.append(f"     art: {r.get('art_url')}")
                lines.append(f"     url: {r.get('itunes_url')}")
            return "\n".join(lines)

        if name == "blog_list_comments":
            limit = int(args.get("limit") or 50)
            cs = svc.list_recent_comments(limit=limit)
            if not cs:
                return "No comments yet."
            lines = [f"{len(cs)} comment(s):"]
            for c in cs:
                body = c["body"].replace("\n", " ")
                if len(body) > 80:
                    body = body[:80] + "…"
                lines.append(f"  {c['id'][:8]}  on {c['post_id'][:8]}  {c['author']}: {body}")
            return "\n".join(lines)

        if name == "blog_delete_comment":
            svc.delete_comment(args["comment_id"])
            return f"Deleted comment {args['comment_id']}."

        return f"Unknown blog tool: {name}"
    except Exception as e:
        return f"Error: {e}"
