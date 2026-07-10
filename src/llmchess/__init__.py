"""Chess game domain, JSON persistence, and optional Rich rendering."""

from .game import MoveError, actor_for, apply_move, board_for, synchronize_outcome
from .models import (
    Actor,
    Color,
    Game,
    GameResult,
    GameStatus,
    GameValidationError,
    Ply,
    Termination,
)
from .store import GameStoreError, JsonGameStore

__all__ = [
    "Actor",
    "Color",
    "Game",
    "GameResult",
    "GameStatus",
    "GameStoreError",
    "GameValidationError",
    "JsonGameStore",
    "MoveError",
    "Ply",
    "Termination",
    "actor_for",
    "apply_move",
    "board_for",
    "synchronize_outcome",
]
