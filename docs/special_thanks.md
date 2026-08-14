# Special Thanks

This project stands on the shoulders of open-source maintainers. Naming names
matters — if this harness feels good to play with, it's in large part because
of the people below.

## Niklas Fiekas — [@niklasf](https://github.com/niklasf)

The harness's chess mode (removed Aug 2026 pending redesign — see git history)
was powered end-to-end by Niklas's work: he has maintained these libraries for
over a decade, and they are the reason we could ship a rules-correct chess
agent in an afternoon instead of a month.

- **[python-chess](https://github.com/niklasf/python-chess)** — full chess
  engine in pure Python: move generation, legality, FEN/PGN/SAN/UCI parsing,
  check/checkmate/stalemate/draw detection, every rule edge case (en passant,
  castling rights, threefold repetition, 50-move rule, insufficient material).
  GPL-3.0.
- **[web-boardimage](https://github.com/niklasf/web-boardimage)** — the tiny
  service hosted at `backscattering.de` that renders a FEN to a PNG. We just
  embedded the URL and Discord auto-attached the image — no rendering deps on
  our side.

If a chess mode returns and you use or extend it, please consider
[sponsoring Niklas](https://github.com/sponsors/niklasf).
