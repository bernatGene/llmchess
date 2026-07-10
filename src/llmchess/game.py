"""Chess rules operations; python-chess remains the rules authority."""

from datetime import datetime

import chess

from .models import (
    Actor,
    Color,
    Game,
    GameResult,
    GameStatus,
    GameValidationError,
    Ply,
    Termination,
    utc_now,
)


class MoveError(GameValidationError):
    """Raised when a requested move cannot be applied to a game."""


def board_for(game: Game) -> chess.Board:
    """Rebuild the board from its initial position and complete UCI history."""
    try:
        board = chess.Board(game.initial_fen)
    except ValueError as error:  # Defensive: Game normally already validates this.
        raise GameValidationError("initial FEN is invalid") from error
    for index, ply in enumerate(game.plies, start=1):
        if Color.from_chess(board.turn) is not ply.color:
            raise GameValidationError(f"ply {index} has the wrong color")
        try:
            move = chess.Move.from_uci(ply.uci)
        except ValueError as error:
            raise GameValidationError(f"ply {index} has invalid UCI") from error
        if move not in board.legal_moves:
            raise GameValidationError(f"ply {index} is illegal")
        if board.san(move) != ply.san:
            raise GameValidationError(f"ply {index} SAN is not canonical")
        board.push(move)
    return board


def actor_for(game: Game, color: Color) -> Actor:
    return Actor.HUMAN if color is game.human_color else Actor.LLM


def _move_from_notation(board: chess.Board, notation: str) -> chess.Move:
    text = notation.strip()
    if not text:
        raise MoveError("move notation is required")
    try:
        uci_move = chess.Move.from_uci(text)
    except ValueError:
        uci_move = None
    if uci_move is not None and uci_move in board.legal_moves:
        return uci_move
    try:
        return board.parse_san(text)
    except ValueError as error:
        raise MoveError(f"illegal or invalid move: {notation}") from error


def _set_outcome(game: Game, board: chess.Board) -> None:
    outcome = board.outcome(claim_draw=True)
    if outcome is None:
        game.status = GameStatus.ACTIVE
        game.result = None
        game.termination = None
        return
    game.status = GameStatus.TERMINAL
    game.termination = Termination[outcome.termination.name]
    game.result = (
        GameResult.DRAW
        if outcome.winner is None
        else GameResult.WHITE_WIN
        if outcome.winner
        else GameResult.BLACK_WIN
    )


def synchronize_outcome(game: Game) -> chess.Board:
    """Derive persisted outcome fields from the replayed position."""
    board = board_for(game)
    _set_outcome(game, board)
    return board


def apply_move(
    game: Game,
    notation: str,
    *,
    actor: Actor,
    color: Color | None = None,
    explanation: str | None = None,
    model: str | None = None,
    timestamp: datetime | None = None,
) -> Ply:
    """Validate and record one SAN or UCI move, returning its canonical ply."""
    board = synchronize_outcome(game)
    if game.status is GameStatus.TERMINAL:
        raise MoveError("game is terminal")
    turn = Color.from_chess(board.turn)
    if color is not None and color is not turn:
        raise MoveError(f"expected {color.value} to move, but it is {turn.value}'s turn")
    expected_actor = actor_for(game, turn)
    if actor is not expected_actor:
        raise MoveError(f"expected {expected_actor.value} actor for {turn.value}")
    if actor is Actor.LLM and not (explanation and explanation.strip()):
        raise MoveError("LLM moves require an explanation")

    move = _move_from_notation(board, notation)
    ply = Ply(
        color=turn,
        actor=actor,
        uci=move.uci(),
        san=board.san(move),
        timestamp=timestamp or utc_now(),
        explanation=explanation,
        model=model,
    )
    board.push(move)
    game.plies.append(ply)
    _set_outcome(game, board)
    return ply
