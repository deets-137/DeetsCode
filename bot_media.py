"""Per-tool media extractors for the Discord bot.

When a tool_result frame arrives on the WS, the bot asks this registry whether
that tool produces postable media (image URLs, attachment links, etc). Any URLs
returned get posted to the channel after the model's reply, so Discord's
auto-embed renders them — without relying on the model to paste them verbatim.

Add a new game by writing a function that takes the tool_result content string
and returns a list of URLs, then registering it in EXTRACTORS below.
"""
import re
from typing import Callable

_CHESS_BOARD_URL = re.compile(r"https?://\S*web-boardimage\S+")


def _chess_extract(content: str) -> list[str]:
    return _CHESS_BOARD_URL.findall(content)


EXTRACTORS: dict[str, Callable[[str], list[str]]] = {
    "chess_new":   _chess_extract,
    "chess_move":  _chess_extract,
    "chess_board": _chess_extract,
    "chess_undo":  _chess_extract,
}


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
