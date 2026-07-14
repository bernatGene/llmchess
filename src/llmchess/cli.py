"""Finite command-line interface for humans and OpenCode agents."""

import argparse
import base64
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import chess
from rich.console import Console

from .game import actor_for, apply_move, board_after_line, board_for, resign_game
from .models import Actor, Color, Game, GameStatus
from .render import board_png, render_board, render_game, transcript_table
from .store import GameStoreError, JsonGameStore


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def _add_board_style_flags(parser: argparse.ArgumentParser) -> None:
    styles = parser.add_mutually_exclusive_group()
    styles.add_argument(
        "--unicode",
        action="store_const",
        const="unicode",
        dest="board_style",
        help="render pieces as Unicode chess symbols",
    )
    styles.add_argument(
        "--minimal",
        action="store_const",
        const="minimal",
        dest="board_style",
        help="render a compact board with ASCII pieces",
    )
    parser.set_defaults(board_style="large")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llmchess", description="Play chess against an LLM")
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="create a human-vs-LLM game")
    new.add_argument("--human", choices=Color, default=Color.WHITE, type=Color)
    _add_board_style_flags(new)
    _add_json_flag(new)

    state = commands.add_parser("state", help="show complete machine-readable game state")
    state.add_argument("game_id")
    _add_json_flag(state)

    move = commands.add_parser("move", help="validate and record one move")
    move.add_argument("game_id")
    move.add_argument("notation", help="SAN or UCI move")
    move.add_argument("--actor", choices=Actor, required=True, type=Actor)
    move.add_argument("--explanation", help="public explanation required for LLM moves")
    move.add_argument("--model", help="optional model identifier")
    _add_json_flag(move)

    resign = commands.add_parser("resign", help="end the game by LLM resignation")
    resign.add_argument("game_id")
    resign.add_argument("--actor", choices=Actor, required=True, type=Actor)
    _add_json_flag(resign)

    try_line = commands.add_parser("try-line", help="inspect a non-persistent move line")
    try_line.add_argument("game_id")
    try_line.add_argument("notations", nargs="+")
    _add_json_flag(try_line)

    piece_moves = commands.add_parser(
        "piece-moves", help="inspect one piece after an optional line"
    )
    piece_moves.add_argument("game_id")
    piece_moves.add_argument("square")
    piece_moves.add_argument("--line", nargs="*", default=[])
    _add_json_flag(piece_moves)

    show = commands.add_parser("show", help="render the board and transcript")
    show.add_argument("game_id")
    show.add_argument("--perspective", choices=Color, type=Color)
    _add_board_style_flags(show)
    _add_json_flag(show)

    live = commands.add_parser("live", help="watch a game and redraw the latest position")
    live.add_argument("game_id")
    live.add_argument("--perspective", choices=Color, type=Color)
    _add_board_style_flags(live)

    image = commands.add_parser("image", help="render the current board as a PNG")
    image.add_argument("game_id")
    image.add_argument("--perspective", choices=Color, default=Color.WHITE, type=Color)
    image.add_argument("--output", type=Path, help="write the PNG to this path")
    _add_json_flag(image)

    transcript = commands.add_parser("transcript", help="show all recorded moves")
    transcript.add_argument("game_id")
    _add_json_flag(transcript)

    listing = commands.add_parser("list", help="list saved games")
    _add_json_flag(listing)
    return parser


def _board_rows(board: chess.Board) -> list[str]:
    def symbol(file_index: int, rank_index: int) -> str:
        piece = board.piece_at(chess.square(file_index, rank_index))
        return piece.symbol() if piece else "."

    return [
        "".join(symbol(file_index, rank_index) for file_index in range(8))
        for rank_index in range(7, -1, -1)
    ]


def _position(board: chess.Board) -> dict[str, object]:
    legal_moves = [{"uci": move.uci(), "san": board.san(move)} for move in board.legal_moves]
    return {
        "fen": board.fen(),
        "board": _board_rows(board),
        "side_to_move": Color.from_chess(board.turn).value,
        "legal_moves": legal_moves,
    }


def _state(game: Game) -> dict[str, object]:
    board = board_for(game)
    turn = Color.from_chess(board.turn)
    return {
        **game.to_dict(),
        **_position(board),
        "turn": turn.value if game.status is GameStatus.ACTIVE else None,
        "expected_actor": actor_for(game, turn).value if game.status is GameStatus.ACTIVE else None,
        "last_move": game.plies[-1].to_dict() if game.plies else None,
    }


def _emit_json(payload: object, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")


def _summary(game: Game, console: Console) -> None:
    board = board_for(game)
    if game.status is GameStatus.TERMINAL:
        assert game.result is not None
        assert game.termination is not None
        console.print(
            f"Game {game.id}: {game.result.value} ({game.termination.value})",
            style="bold cyan",
        )
        return
    turn = Color.from_chess(board.turn)
    console.print(
        f"Game {game.id}: {turn.value} to move ({actor_for(game, turn).value})",
        style="bold cyan",
    )


def _live_frame(
    game: Game,
    console: Console,
    perspective: Color,
    first_ply: int,
    *,
    unicode_pieces: bool = False,
    large_pieces: bool = False,
) -> None:
    console.clear()
    for index, ply in enumerate(game.plies[first_ply:], start=first_ply):
        move_number = index // 2 + 1
        marker = "." if ply.color is Color.WHITE else "..."
        console.print(
            f"{move_number}{marker} {ply.san} ({ply.actor.value})",
            style="bold green" if ply.actor is Actor.LLM else "bold",
        )
        if ply.explanation:
            console.print(ply.explanation)
    _summary(game, console)
    render_board(
        board_for(game),
        console,
        perspective,
        unicode_pieces=unicode_pieces,
        large_pieces=large_pieces,
    )


def _live(
    store: JsonGameStore,
    game: Game,
    console: Console,
    perspective: Color,
    *,
    unicode_pieces: bool = False,
    large_pieces: bool = False,
) -> None:
    first_ply = max(0, len(game.plies) - 1)
    _live_frame(
        game,
        console,
        perspective,
        first_ply,
        unicode_pieces=unicode_pieces,
        large_pieces=large_pieces,
    )
    if game.status is GameStatus.TERMINAL:
        return

    try:
        while True:
            time.sleep(0.25)
            updated = store.load(game.id)
            if updated == game:
                continue
            first_ply = (
                len(game.plies)
                if updated.plies[: len(game.plies)] == game.plies
                else max(0, len(updated.plies) - 1)
            )
            game = updated
            _live_frame(
                game,
                console,
                perspective,
                first_ply,
                unicode_pieces=unicode_pieces,
                large_pieces=large_pieces,
            )
            if game.status is GameStatus.TERMINAL:
                return
    except KeyboardInterrupt:
        return


def _run(args: argparse.Namespace, console: Console) -> None:
    store = JsonGameStore()
    if args.command == "new":
        game = Game(id=store.generate_id(), human_color=args.human)
        store.create(game)
        if args.json:
            _emit_json(_state(game))
        else:
            _summary(game, console)
            render_game(
                game,
                console,
                game.human_color,
                unicode_pieces=args.board_style == "unicode",
                large_pieces=args.board_style == "large",
            )
        return

    if args.command == "list":
        games = store.list_games()
        rows = [
            {
                "id": game.id,
                "human_color": game.human_color.value,
                "status": game.status.value,
                "result": game.result.value if game.result else None,
                "plies": len(game.plies),
            }
            for game in games
        ]
        if args.json:
            _emit_json(rows)
        elif not rows:
            console.print("No saved games.", style="yellow")
        else:
            for row in rows:
                console.print(
                    f"{row['id']} {row['status']} human:{row['human_color']} plies:{row['plies']}"
                )
        return

    game = store.load(args.game_id)
    if args.command == "image":
        board = board_for(game)
        png = board_png(board, args.perspective)
        if args.json:
            _emit_json(
                {
                    "game_id": game.id,
                    "fen": board.fen(),
                    "perspective": args.perspective.value,
                    "mime": "image/png",
                    "png_base64": base64.b64encode(png).decode("ascii"),
                }
            )
        else:
            if args.output is None:
                raise ValueError("--output is required unless --json is used")
            args.output.write_bytes(png)
            console.print(f"Board image written to {args.output}", style="green")
        return

    if args.command == "state":
        if args.json:
            _emit_json(_state(game))
        else:
            _summary(game, console)
            board = board_for(game)
            if game.status is GameStatus.ACTIVE:
                legal_moves = " ".join(board.san(move) for move in board.legal_moves)
                console.print(f"Legal moves: {legal_moves}")
        return

    if args.command == "move":
        ply = apply_move(
            game,
            args.notation,
            actor=args.actor,
            explanation=args.explanation,
            model=args.model,
        )
        store.save(game)
        payload = {"applied": ply.to_dict(), "game": _state(game)}
        if args.json:
            _emit_json(payload)
        else:
            console.print(f"Move accepted: {ply.san} ({ply.uci})", style="green")
            _summary(game, console)
        return

    if args.command == "resign":
        resign_game(game, actor=args.actor)
        store.save(game)
        if args.json:
            _emit_json(_state(game))
        else:
            console.print("LLM resigned.", style="yellow")
            _summary(game, console)
        return

    if args.command == "try-line":
        base_fen = board_for(game).fen()
        board = board_after_line(game, args.notations)
        _emit_json({"game_id": game.id, "base_fen": base_fen, **_position(board)})
        return

    if args.command == "piece-moves":
        base_board = board_for(game)
        board = board_after_line(game, args.line) if args.line else base_board
        if game.status is GameStatus.TERMINAL:
            raise ValueError("game is terminal")
        turn = Color.from_chess(base_board.turn)
        if actor_for(game, turn) is not Actor.LLM:
            raise ValueError(f"expected human actor for {turn.value}")
        try:
            square = chess.parse_square(args.square)
        except ValueError as error:
            raise ValueError(f"invalid square: {args.square}") from error
        piece = board.piece_at(square)
        if piece is None:
            raise ValueError(f"no piece on {args.square}")
        legal_moves = [
            {"uci": move.uci(), "san": board.san(move)}
            for move in board.legal_moves
            if move.from_square == square
        ]
        _emit_json(
            {
                "game_id": game.id,
                "base_fen": base_board.fen(),
                "fen": board.fen(),
                "board": _board_rows(board),
                "side_to_move": Color.from_chess(board.turn).value,
                "piece": (
                    f"{'white' if piece.color else 'black'} {chess.piece_name(piece.piece_type)}"
                ),
                "square": args.square,
                "attacks": [chess.square_name(target) for target in sorted(board.attacks(square))],
                "legal_moves": legal_moves,
            }
        )
        return

    if args.command == "show":
        if args.json:
            _emit_json(_state(game))
        else:
            _summary(game, console)
            render_game(
                game,
                console,
                args.perspective or game.human_color,
                unicode_pieces=args.board_style == "unicode",
                large_pieces=args.board_style == "large",
            )
        return

    if args.command == "live":
        _live(
            store,
            game,
            console,
            args.perspective or game.human_color,
            unicode_pieces=args.board_style == "unicode",
            large_pieces=args.board_style == "large",
        )
        return

    if args.command == "transcript":
        if args.json:
            _emit_json([ply.to_dict() for ply in game.plies])
        else:
            console.print(transcript_table(game))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console(stderr=False)
    try:
        _run(args, console)
    except (FileNotFoundError, GameStoreError, ValueError) as error:
        if getattr(args, "json", False):
            _emit_json({"error": str(error), "type": type(error).__name__}, error=True)
        else:
            Console(stderr=True).print(str(error), style="red")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
