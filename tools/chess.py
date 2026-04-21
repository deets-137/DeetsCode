"""
Chess tool pack.

Built on python-chess (https://github.com/niklasf/python-chess) by Niklas Fiekas.
All rules enforcement, notation parsing (SAN/UCI), and state serialization (FEN/PGN)
comes from his library — this module is just a thin harness adapter on top.
GPL-3.0. Huge thanks to Niklas for the decades of maintenance.

Board images are rendered by web-boardimage (https://github.com/niklasf/web-boardimage),
also by Niklas, hosted at backscattering.de. We embed the image URL in tool output
so Discord auto-embeds it inline — zero image-rendering deps on our side.

State shape (stored in storage.games.state_json):
    {
      "v": 1,
      "fen": "<current position>",
      "white": "<display name or 'computer'>",
      "black": "<display name or 'computer'>",
      "result": null          # or "1-0", "0-1", "1/2-1/2"
    }

Per-move rows in storage.moves carry {san, uci, captured, fen_after} as move_json.
Turn enforcement: user_name passed via execute_tool must match whose turn it is,
unless the current side is "computer" (any caller may move as the engine).
"""

import io
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import chess
import chess.pgn

import storage

STATE_VERSION = 1
COMPUTER = "computer"
BOARD_IMAGE_BASE = "https://backscattering.de/web-boardimage/board.png"

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "chess_new",
            "description": (
                "Start a new chess game in the current channel. "
                "`white` and `black` are display names, taken verbatim from the "
                "`speaker` field in the current request envelope — or the literal "
                "string 'computer' for the AI side. "
                "After creating the game, DO NOT make the first move unless that side "
                "is 'computer'. If the human plays White, wait for them to send a move. "
                "Returns a short game_id — quote it in replies so players can reference it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "white": {"type": "string", "description": "Display name of the White player, or 'computer'."},
                    "black": {"type": "string", "description": "Display name of the Black player, or 'computer'."},
                },
                "required": ["white", "black"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chess_move",
            "description": (
                "Make a move in an active game. Accepts SAN (e.g. 'Nf3', 'O-O', 'exd5') "
                "or UCI (e.g. 'g1f3'). The mover is taken automatically from the current "
                "request envelope's `speaker` — you do NOT pass it. The tool rejects the "
                "move if that speaker is not the side whose turn it is, if the move is "
                "illegal, or if the game has ended. If the active side is 'computer', YOU "
                "call this tool on its behalf. Optionally annotate with a PGN symbol like "
                "'!', '?!', '!!', '?', '??'. Auto-renders the board — do NOT follow up "
                "with chess_board."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "game_id":    {"type": "string", "description": "Short id from chess_new / chess_list"},
                    "move":       {"type": "string", "description": "Move in SAN or UCI"},
                    "annotation": {"type": "string", "description": "Optional PGN annotation ('!', '?', '!!', '??', '!?', '?!'). Leave empty if unsure."},
                },
                "required": ["game_id", "move"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chess_board",
            "description": (
                "Re-render the current board (ASCII + FEN + whose turn). "
                "Only call this when the user explicitly asks to see the board again. "
                "chess_move and chess_new already render — do NOT call this after them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                },
                "required": ["game_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chess_legal_moves",
            "description": (
                "List all legal moves in a game, optionally filtered to a source square. "
                "Use this BEFORE proposing a move you're unsure about — it prevents "
                "illegal-move rejections."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                    "square":  {"type": "string", "description": "Optional source square like 'e2' to filter"},
                },
                "required": ["game_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chess_resign",
            "description": (
                "Resign the game on behalf of the current speaker. Their name is taken "
                "automatically from the request envelope — do NOT pass it. Ends the game; "
                "the opposite side wins."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                },
                "required": ["game_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chess_undo",
            "description": "Undo the last ply. Either player may call. Rebuilds the board from the remaining move history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                },
                "required": ["game_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chess_history",
            "description": "Return the full move list as PGN (standard chess notation) including annotations and the game result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "game_id": {"type": "string"},
                },
                "required": ["game_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "chess_list",
            "description": "List chess games in the current channel. Use when a user lost the game_id or wants to see what's going on.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter: 'active' (default), 'ended', or 'all'"},
                },
                "required": [],
            },
        },
    },
]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _load_board(game_id: str) -> tuple[Optional[dict], Optional[chess.Board]]:
    """Return (game_row, chess.Board) or (None, None) if not found."""
    g = storage.load_game(game_id)
    if g is None or g["game_type"] != "chess":
        return None, None
    board = chess.Board(g["state"]["fen"])
    return g, board


def _current_turn_name(state: dict, board: chess.Board) -> str:
    return state["white"] if board.turn == chess.WHITE else state["black"]


def _board_image_url(board: chess.Board) -> str:
    fen_position = board.fen().split(" ")[0]
    orient = "white" if board.turn == chess.WHITE else "black"
    return f"{BOARD_IMAGE_BASE}?fen={quote(fen_position)}&orientation={orient}"


def _render(board: chess.Board, state: dict, extra: str = "") -> str:
    status = _status_str(board, state)
    turn = "White" if board.turn == chess.WHITE else "Black"
    turn_name = _current_turn_name(state, board)
    header = (
        f"FEN: {board.fen()}\n"
        f"Turn: {turn} ({turn_name})\n"
        f"Status: {status}"
    )
    if extra:
        header += f"\n{extra}"
    ascii_board = board.unicode(invert_color=True, borders=True)
    return f"{header}\n\n{ascii_board}\n\n{_board_image_url(board)}"


def _status_str(board: chess.Board, state: dict) -> str:
    if state.get("result") is not None:
        return f"game over ({state['result']})"
    if board.is_checkmate():
        return "checkmate"
    if board.is_stalemate():
        return "stalemate"
    if board.is_insufficient_material():
        return "draw (insufficient material)"
    if board.can_claim_fifty_moves():
        return "draw available (50-move rule)"
    if board.can_claim_threefold_repetition():
        return "draw available (threefold repetition)"
    if board.is_check():
        return "check"
    return "normal"


def _terminal_result(board: chess.Board) -> Optional[str]:
    if board.is_checkmate():
        return "0-1" if board.turn == chess.WHITE else "1-0"
    if board.is_stalemate() or board.is_insufficient_material():
        return "1/2-1/2"
    return None


def _parse_move(board: chess.Board, move_str: str) -> Optional[chess.Move]:
    move_str = move_str.strip()
    try:
        return board.parse_san(move_str)
    except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError):
        pass
    try:
        m = chess.Move.from_uci(move_str.lower())
        if m in board.legal_moves:
            return m
    except (chess.InvalidMoveError, ValueError):
        pass
    return None


# ─── Tools ───────────────────────────────────────────────────────────────────

def _tool_new(args: dict, session_id: str) -> str:
    white = args.get("white", "").strip()
    black = args.get("black", "").strip()
    if not (white and black):
        return "Error: white and black are both required"

    board = chess.Board()
    state = {
        "v": STATE_VERSION,
        "fen": board.fen(),
        "white": white,
        "black": black,
        "result": None,
    }
    creator = white if white != COMPUTER else black
    gid = storage.create_game(
        "chess", session_id, state,
        created_by=None if creator == COMPUTER else creator,
    )
    if white != COMPUTER:
        storage.upsert_player(white, white)
    if black != COMPUTER:
        storage.upsert_player(black, black)

    return (
        f"Game `{gid}` started. {white} (White) vs {black} (Black).\n"
        f"{_render(board, state)}\n"
        f"Reference this game by its id `{gid}` in future calls."
    )


def _tool_move(args: dict, user_name: Optional[str]) -> str:
    gid = args.get("game_id", "").strip()
    move_str = args.get("move", "").strip()
    annotation = (args.get("annotation") or "").strip() or None
    if not gid or not move_str:
        return "Error: game_id and move are required"

    g, board = _load_board(gid)
    if g is None:
        return f"Error: no chess game with id `{gid}`"
    state = g["state"]
    if state.get("result") is not None:
        return f"Error: game `{gid}` is already over ({state['result']})"

    current = _current_turn_name(state, board)
    if current != COMPUTER:
        if user_name is None:
            return "Error: caller identity unknown; cannot verify turn"
        if user_name != current:
            return (
                f"Error: not your turn — it's {current}'s move. "
                f"(You are {user_name}.)"
            )

    move = _parse_move(board, move_str)
    if move is None:
        return (
            f"Error: `{move_str}` is not a legal move in this position. "
            f"Call chess_legal_moves for a list."
        )

    captured_piece = None
    if board.is_capture(move):
        if board.is_en_passant(move):
            captured_piece = "p"
        else:
            tgt = board.piece_at(move.to_square)
            captured_piece = tgt.symbol().lower() if tgt else None

    san = board.san(move)
    uci = move.uci()
    board.push(move)

    state["fen"] = board.fen()
    result = _terminal_result(board)
    if result is not None:
        state["result"] = result

    storage.save_state(gid, state)
    storage.record_move(
        gid,
        user_name if current != COMPUTER else None,
        {"san": san, "uci": uci, "captured": captured_piece, "fen_after": board.fen()},
        annotation=annotation,
    )
    if result is not None:
        storage.end_game(gid, status="ended")

    ann_str = f" {annotation}" if annotation else ""
    cap_str = f" (captured {captured_piece})" if captured_piece else ""
    header = f"Move: {san}{ann_str} [{uci}]{cap_str}"
    return _render(board, state, extra=header)


def _tool_board(args: dict) -> str:
    gid = args.get("game_id", "").strip()
    g, board = _load_board(gid)
    if g is None:
        return f"Error: no chess game with id `{gid}`"
    return _render(board, g["state"])


def _tool_legal_moves(args: dict) -> str:
    gid = args.get("game_id", "").strip()
    g, board = _load_board(gid)
    if g is None:
        return f"Error: no chess game with id `{gid}`"

    square_str = (args.get("square") or "").strip().lower()
    if square_str:
        try:
            sq = chess.parse_square(square_str)
        except ValueError:
            return f"Error: `{square_str}` is not a valid square (use e.g. 'e2')"
        moves = [m for m in board.legal_moves if m.from_square == sq]
    else:
        moves = list(board.legal_moves)

    if not moves:
        return "No legal moves." if not square_str else f"No legal moves from {square_str}."

    san_list = [board.san(m) for m in moves]
    return f"{len(san_list)} legal move(s): " + ", ".join(san_list)


def _tool_resign(args: dict, user_name: Optional[str]) -> str:
    gid = args.get("game_id", "").strip()
    g, board = _load_board(gid)
    if g is None:
        return f"Error: no chess game with id `{gid}`"
    state = g["state"]
    if state.get("result") is not None:
        return f"Error: game `{gid}` is already over ({state['result']})"
    if user_name is None:
        return "Error: caller identity unknown; cannot attribute resignation"

    if user_name == state["white"]:
        result, winner = "0-1", state["black"]
    elif user_name == state["black"]:
        result, winner = "1-0", state["white"]
    else:
        return f"Error: {user_name} is not a player in this game"

    state["result"] = result
    storage.save_state(gid, state)
    storage.end_game(gid, status="ended")
    storage.record_move(gid, user_name, {"resign": True}, annotation=None)
    return f"Resignation recorded. {winner} wins. Result: {result}."


def _tool_undo(args: dict) -> str:
    gid = args.get("game_id", "").strip()
    g, _ = _load_board(gid)
    if g is None:
        return f"Error: no chess game with id `{gid}`"
    state = g["state"]

    history = storage.game_history(gid)
    while history and history[-1]["move"].get("resign"):
        _pop_last_move(gid, history[-1]["seq"])
        history.pop()
    if not history:
        return "Error: nothing to undo (no moves played yet)"

    _pop_last_move(gid, history[-1]["seq"])
    history.pop()

    board = chess.Board()
    for row in history:
        san_or_uci = row["move"].get("san") or row["move"].get("uci")
        m = _parse_move(board, san_or_uci)
        if m is None:
            return f"Error: could not replay move `{san_or_uci}` during undo — state inconsistent"
        board.push(m)

    state["fen"] = board.fen()
    state["result"] = None
    storage.save_state(gid, state)
    storage._db().execute(
        "UPDATE games SET status='active', ended_at=NULL WHERE game_id=?", (gid,)
    )
    return f"Undid last ply.\n{_render(board, state)}"


def _pop_last_move(game_id: str, seq: int) -> None:
    storage._db().execute(
        "DELETE FROM moves WHERE game_id=? AND seq=?", (game_id, seq)
    )


def _tool_history(args: dict) -> str:
    gid = args.get("game_id", "").strip()
    g, _ = _load_board(gid)
    if g is None:
        return f"Error: no chess game with id `{gid}`"
    state = g["state"]
    history = storage.game_history(gid)

    game = chess.pgn.Game()
    game.headers["White"] = state["white"]
    game.headers["Black"] = state["black"]
    game.headers["Result"] = state.get("result") or "*"
    node = game
    board = chess.Board()
    for row in history:
        mv = row["move"]
        if mv.get("resign"):
            continue
        m = _parse_move(board, mv.get("san") or mv.get("uci"))
        if m is None:
            continue
        node = node.add_variation(m)
        if row.get("annotation"):
            node.comment = row["annotation"]
        board.push(m)

    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=True)
    return game.accept(exporter)


def _tool_list(args: dict, session_id: str) -> str:
    status_arg = (args.get("status") or "active").lower()
    status_filter = None if status_arg == "all" else status_arg
    rows = storage.list_games(
        channel_id=session_id, game_type="chess", status=status_filter
    )
    if not rows:
        return "No chess games in this channel."
    lines = [f"{len(rows)} chess game(s) in this channel:"]
    for r in rows:
        g = storage.load_game(r["game_id"])
        s = g["state"] if g else {}
        lines.append(
            f"  `{r['game_id']}`  {s.get('white', '?')} vs {s.get('black', '?')}  [{r['status']}]"
        )
    return "\n".join(lines)


# ─── Dispatch ────────────────────────────────────────────────────────────────

def execute_tool(
    name: str,
    args: dict,
    session_id: str,
    project_dir: Path,
    user_name: Optional[str] = None,
) -> str:
    try:
        if name == "chess_new":
            return _tool_new(args, session_id)
        if name == "chess_move":
            return _tool_move(args, user_name)
        if name == "chess_board":
            return _tool_board(args)
        if name == "chess_legal_moves":
            return _tool_legal_moves(args)
        if name == "chess_resign":
            return _tool_resign(args, user_name)
        if name == "chess_undo":
            return _tool_undo(args)
        if name == "chess_history":
            return _tool_history(args)
        if name == "chess_list":
            return _tool_list(args, session_id)
        return f"Unknown chess tool: {name}"
    except Exception as e:
        return f"Error: {e}"
