# Special Thanks

This project stands on the shoulders of open-source maintainers. Naming names
matters — if this harness feels good to play with, it's in large part because
of the people below.

## Niklas Fiekas — [@niklasf](https://github.com/niklasf)

Every chess feature in this repo is powered by Niklas's work. He has maintained
these libraries for over a decade and they are, frankly, the reason we could
ship a rules-correct chess agent in an afternoon instead of a month.

- **[python-chess](https://github.com/niklasf/python-chess)** — full chess
  engine in pure Python: move generation, legality, FEN/PGN/SAN/UCI parsing,
  check/checkmate/stalemate/draw detection, every rule edge case (en passant,
  castling rights, threefold repetition, 50-move rule, insufficient material).
  GPL-3.0.
- **[web-boardimage](https://github.com/niklasf/web-boardimage)** — the tiny
  service hosted at `backscattering.de` that renders a FEN to a PNG. We just
  embed the URL and Discord auto-attaches the image — no rendering deps on
  our side.

### Files in this repo that depend on Niklas's code

- [`tools/chess.py`](tools/chess.py) — imports `chess` and `chess.pgn`;
  embeds web-boardimage URLs in tool output.
- [`requirements.txt`](requirements.txt) — lists `chess` as a dependency.

If you end up using or extending the chess tools, please consider
[sponsoring Niklas](https://github.com/sponsors/niklasf).
