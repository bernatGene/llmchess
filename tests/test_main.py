import chess
from rich.console import Console

from main import apply_san_move, turn_text


def test_turn_text_white_and_black() -> None:
    board = chess.Board()
    assert turn_text(board) == "Turn: White"

    board.push_san("e4")
    assert turn_text(board) == "Turn: Black"


def test_apply_san_move_updates_board_and_reports_turn() -> None:
    board = chess.Board()
    console = Console(record=True, width=120)

    apply_san_move(board, "e4", console)

    assert board.fullmove_number == 1
    assert board.turn == chess.BLACK

    output = console.export_text()
    assert "Move ok: e4." in output
    assert "Turn: Black" in output


def test_apply_san_move_rejects_illegal_move() -> None:
    board = chess.Board()
    console = Console(record=True, width=120)
    start_fen = board.fen()

    apply_san_move(board, "e5", console)

    assert board.fen() == start_fen
    output = console.export_text()
    assert "Illegal move or invalid SAN" in output
