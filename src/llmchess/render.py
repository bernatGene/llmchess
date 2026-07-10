"""Rich presentation helpers kept outside the chess domain."""

from importlib.resources import files

import chess
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .game import board_for
from .models import Color, Game

_LARGE_SQUARE_SIZE = 8
_LIGHT_SQUARE = "#e0b97d"
_DARK_SQUARE = "#b58863"
_BLACK_PIECE = "#000000"
_WHITE_PIECE = "#ffffff"


def _load_piece_mask(name: str) -> tuple[str, ...]:
    mask = tuple(
        files("llmchess.pieces").joinpath(f"{name}.txt").read_text(encoding="ascii").splitlines()
    )
    if len(mask) != _LARGE_SQUARE_SIZE or any(
        len(row) != _LARGE_SQUARE_SIZE or set(row) > {".", "#"} for row in mask
    ):
        raise ValueError(f"invalid 8x8 piece mask: {name}")
    return mask


_LARGE_PIECES = {
    chess.KING: _load_piece_mask("king"),
    chess.QUEEN: _load_piece_mask("queen"),
    chess.ROOK: _load_piece_mask("rook"),
    chess.BISHOP: _load_piece_mask("bishop"),
    chess.KNIGHT: _load_piece_mask("knight"),
    chess.PAWN: _load_piece_mask("pawn"),
}


def _large_piece_rows(piece: chess.Piece | None, square_color: str) -> list[Text]:
    if piece is None:
        return [
            Text(
                "█" * _LARGE_SQUARE_SIZE,
                style=f"{square_color} on {square_color}",
            )
            for _ in range(_LARGE_SQUARE_SIZE // 2)
        ]

    def pixel_color(pixel: str) -> str:
        if pixel == "#":
            return _WHITE_PIECE if piece.color else _BLACK_PIECE
        return square_color

    mask = _LARGE_PIECES[piece.piece_type]
    rows: list[Text] = []
    for top_mask, bottom_mask in zip(mask[::2], mask[1::2], strict=True):
        row = Text()
        for top_pixel, bottom_pixel in zip(top_mask, bottom_mask, strict=True):
            top_color = pixel_color(top_pixel)
            bottom_color = pixel_color(bottom_pixel)
            if top_color == bottom_color:
                if top_color == square_color:
                    row.append("█", style=f"{square_color} on {square_color}")
                else:
                    row.append("█", style=f"{top_color} on {square_color}")
            elif top_color == square_color:
                row.append("▄", style=f"{bottom_color} on {square_color}")
            elif bottom_color == square_color:
                row.append("▀", style=f"{top_color} on {square_color}")
            else:
                raise AssertionError("piece masks must use one foreground color")
        rows.append(row)
    return rows


def board_table(
    board: chess.Board,
    perspective: Color = Color.WHITE,
    *,
    unicode_pieces: bool = False,
    large_pieces: bool = False,
) -> Table:
    files = "abcdefgh" if perspective is Color.WHITE else "hgfedcba"
    file_indices = range(8) if perspective is Color.WHITE else range(7, -1, -1)
    ranks = range(8, 0, -1) if perspective is Color.WHITE else range(1, 9)
    table = Table.grid(padding=(0, 0))
    table.add_column(justify="right", width=2)
    for _ in files:
        table.add_column(justify="center", width=_LARGE_SQUARE_SIZE if large_pieces else 2)
    table.add_column(justify="left", width=2)
    labels = [Text(" "), *(Text(file, style="bold") for file in files), Text(" ")]
    table.add_row(*labels)
    for rank in ranks:
        if large_pieces:
            large_rows = [
                [Text(str(rank), style="bold") if line == 1 else Text(" ")]
                for line in range(_LARGE_SQUARE_SIZE // 2)
            ]
            for file_index in file_indices:
                piece = board.piece_at(chess.square(file_index, rank - 1))
                background = _LIGHT_SQUARE if (file_index + rank) % 2 == 0 else _DARK_SQUARE
                for row, piece_row in zip(
                    large_rows,
                    _large_piece_rows(piece, background),
                    strict=True,
                ):
                    row.append(piece_row)
            for line, row in enumerate(large_rows):
                row.append(Text(str(rank), style="bold") if line == 1 else Text(" "))
                table.add_row(*row)
            continue

        row = [Text(str(rank), style="bold")]
        for file_index in file_indices:
            piece = board.piece_at(chess.square(file_index, rank - 1))
            background = _LIGHT_SQUARE if (file_index + rank) % 2 == 0 else _DARK_SQUARE
            if piece is None:
                row.append(Text("  ", style=f"on {background}"))
            else:
                symbol = (
                    piece.unicode_symbol(invert_color=piece.color)
                    if unicode_pieces
                    else piece.symbol()
                )
                foreground = (
                    ("bold bright_white" if unicode_pieces else "white") if piece.color else "black"
                )
                row.append(Text(f" {symbol}", style=f"{foreground} on {background}"))
        row.append(Text(str(rank), style="bold"))
        table.add_row(*row)
    table.add_row(*labels)
    return table


def render_board(
    board: chess.Board,
    console: Console,
    perspective: Color = Color.WHITE,
    *,
    unicode_pieces: bool = False,
    large_pieces: bool = False,
) -> None:
    console.print(
        board_table(
            board,
            perspective,
            unicode_pieces=unicode_pieces,
            large_pieces=large_pieces,
        )
    )


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


def render_game(
    game: Game,
    console: Console,
    perspective: Color = Color.WHITE,
    *,
    unicode_pieces: bool = False,
    large_pieces: bool = False,
) -> None:
    console.print(transcript_table(game))
    render_board(
        board_for(game),
        console,
        perspective,
        unicode_pieces=unicode_pieces,
        large_pieces=large_pieces,
    )
