"""Rich presentation helpers kept outside the chess domain."""

import chess
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .game import board_for
from .models import Color, Game


def board_table(board: chess.Board, perspective: Color = Color.WHITE) -> Table:
    files = "abcdefgh" if perspective is Color.WHITE else "hgfedcba"
    file_indices = range(8) if perspective is Color.WHITE else range(7, -1, -1)
    ranks = range(8, 0, -1) if perspective is Color.WHITE else range(1, 9)
    table = Table.grid(padding=(0, 0))
    table.add_column(justify="right", width=2)
    for _ in files:
        table.add_column(justify="center", width=2)
    table.add_column(justify="left", width=2)
    labels = [Text(" "), *(Text(file, style="bold") for file in files), Text(" ")]
    table.add_row(*labels)
    for rank in ranks:
        row = [Text(str(rank), style="bold")]
        for file_index in file_indices:
            piece = board.piece_at(chess.square(file_index, rank - 1))
            background = "#f0d9b5" if (file_index + rank) % 2 == 0 else "#b58863"
            if piece is None:
                row.append(Text("  ", style=f"on {background}"))
            else:
                foreground = "white" if piece.color else "black"
                row.append(Text(f" {piece.symbol()}", style=f"{foreground} on {background}"))
        row.append(Text(str(rank), style="bold"))
        table.add_row(*row)
    table.add_row(*labels)
    return table


def render_board(board: chess.Board, console: Console, perspective: Color = Color.WHITE) -> None:
    console.print(board_table(board, perspective))


def transcript_table(game: Game) -> Table:
    """Build a Rich move transcript without adding Rich types to the domain."""
    table = Table(title="Transcript")
    table.add_column("#", justify="right")
    table.add_column("Color")
    table.add_column("Actor")
    table.add_column("Move")
    table.add_column("Explanation")
    for number, ply in enumerate(game.plies, start=1):
        table.add_row(str(number), ply.color.value, ply.actor.value, ply.san, ply.explanation or "")
    return table


def render_game(game: Game, console: Console, perspective: Color = Color.WHITE) -> None:
    render_board(board_for(game), console, perspective)
    console.print(transcript_table(game))
