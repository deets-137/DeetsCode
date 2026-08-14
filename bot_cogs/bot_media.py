"""Per-tool media extractors for the Discord bot.

When a tool_result frame arrives on the WS, the bot asks this registry whether
that tool produces postable media (image URLs, attachment links, etc). Any URLs
returned get posted to the channel after the model's reply, so Discord's
auto-embed renders them — without relying on the model to paste them verbatim.

To surface media from a tool, write a function that takes the tool_result
content string and returns a list of URLs, then register it in EXTRACTORS
below under that tool's name.

EXTRACTORS is empty today: the chess board-image extractors were removed with
the game modes in Aug 2026, and no DeetsCode tool emits media yet. `extract()`
returns [] for every tool until something is registered.
"""
from typing import Callable

EXTRACTORS: dict[str, Callable[[str], list[str]]] = {}


def extract(tool_name: str, content: str) -> list[str]:
    """Return a list of URLs the bot should post to Discord for this tool_result.
    Empty list for tools that produce no media (or on extractor failure)."""
    fn = EXTRACTORS.get(tool_name)
    if fn is None:
        return []
    try:
        return fn(content or "")
    except Exception:
        return []
