"""Typed, serializable records for an LLM chess game."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self, cast

import chess


class GameValidationError(ValueError):
    """Raised when persisted game data does not satisfy the domain schema."""


class Color(StrEnum):
    WHITE = "white"
    BLACK = "black"

    @property
    def chess_color(self) -> chess.Color:
        return self is Color.WHITE

    @classmethod
    def from_chess(cls, color: chess.Color) -> Self:
        return cls("white" if color else "black")


class Actor(StrEnum):
    HUMAN = "human"
    LLM = "llm"


class GameStatus(StrEnum):
    ACTIVE = "active"
    TERMINAL = "terminal"


class GameResult(StrEnum):
    WHITE_WIN = "1-0"
    BLACK_WIN = "0-1"
    DRAW = "1/2-1/2"


class Termination(StrEnum):
    CHECKMATE = "checkmate"
    STALEMATE = "stalemate"
    INSUFFICIENT_MATERIAL = "insufficient_material"
    SEVENTYFIVE_MOVES = "seventyfive_moves"
    FIVEFOLD_REPETITION = "fivefold_repetition"
    FIFTY_MOVES = "fifty_moves"
    THREEFOLD_REPETITION = "threefold_repetition"
    VARIANT_WIN = "variant_win"
    VARIANT_LOSS = "variant_loss"
    VARIANT_DRAW = "variant_draw"
    RESIGNATION = "resignation"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GameValidationError(f"{name} must be an object")
    return cast(dict[str, Any], value)


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise GameValidationError(f"{name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise GameValidationError(f"{name} is not a valid timestamp") from error
    if parsed.tzinfo is None:
        raise GameValidationError(f"{name} must include a timezone")
    return parsed


@dataclass(slots=True)
class Ply:
    color: Color
    actor: Actor
    uci: str
    san: str
    timestamp: datetime = field(default_factory=utc_now)
    explanation: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.uci, str) or not isinstance(self.san, str):
            raise GameValidationError("ply UCI and SAN must be strings")
        if self.explanation is not None and not isinstance(self.explanation, str):
            raise GameValidationError("ply explanation must be a string or null")
        if self.model is not None and not isinstance(self.model, str):
            raise GameValidationError("ply model must be a string or null")
        try:
            chess.Move.from_uci(self.uci)
        except ValueError as error:
            raise GameValidationError("ply UCI is invalid") from error
        if not self.san:
            raise GameValidationError("ply SAN is required")
        if self.timestamp.tzinfo is None:
            raise GameValidationError("ply timestamp must include a timezone")
        if self.actor is Actor.LLM and not (self.explanation and self.explanation.strip()):
            raise GameValidationError("LLM plies require an explanation")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "color": self.color.value,
            "actor": self.actor.value,
            "uci": self.uci,
            "san": self.san,
            "timestamp": self.timestamp.isoformat(),
            "explanation": self.explanation,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        data = _require_mapping(value, "ply")
        try:
            return cls(
                color=Color(data["color"]),
                actor=Actor(data["actor"]),
                uci=str(data["uci"]),
                san=str(data["san"]),
                timestamp=_timestamp(data["timestamp"], "ply.timestamp"),
                explanation=data.get("explanation"),
                model=data.get("model"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GameValidationError("invalid ply") from error


@dataclass(slots=True)
class Game:
    id: str
    human_color: Color
    initial_fen: str = chess.STARTING_FEN
    plies: list[Ply] = field(default_factory=list)
    status: GameStatus = GameStatus.ACTIVE
    result: GameResult | None = None
    termination: Termination | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise GameValidationError("game id is required")
        if not isinstance(self.initial_fen, str):
            raise GameValidationError("initial FEN must be a string")
        try:
            board = chess.Board(self.initial_fen)
        except ValueError as error:
            raise GameValidationError("initial FEN is invalid") from error
        for index, ply in enumerate(self.plies, start=1):
            color = Color.from_chess(board.turn)
            if ply.color is not color:
                raise GameValidationError(f"ply {index} has the wrong color")
            if ply.actor is not (Actor.HUMAN if color is self.human_color else Actor.LLM):
                raise GameValidationError(f"ply {index} has the wrong actor")
            move = chess.Move.from_uci(ply.uci)
            if move not in board.legal_moves:
                raise GameValidationError(f"ply {index} is illegal")
            if board.san(move) != ply.san:
                raise GameValidationError(f"ply {index} SAN is not canonical")
            board.push(move)

        outcome = board.outcome(claim_draw=True)
        if outcome is None:
            if self.termination is Termination.RESIGNATION:
                expected_result = (
                    GameResult.BLACK_WIN
                    if self.human_color is Color.BLACK
                    else GameResult.WHITE_WIN
                )
                if self.status is not GameStatus.TERMINAL or self.result is not expected_result:
                    raise GameValidationError("resignation must record a win for the human")
                return
            if (
                self.status is not GameStatus.ACTIVE
                or self.result is not None
                or self.termination is not None
            ):
                raise GameValidationError("non-terminal position has terminal outcome fields")
            return
        expected_result = (
            GameResult.DRAW
            if outcome.winner is None
            else GameResult.WHITE_WIN
            if outcome.winner
            else GameResult.BLACK_WIN
        )
        expected_termination = Termination[outcome.termination.name]
        if (
            self.status is not GameStatus.TERMINAL
            or self.result is not expected_result
            or self.termination is not expected_termination
        ):
            raise GameValidationError("terminal outcome fields do not match the position")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "id": self.id,
            "human_color": self.human_color.value,
            "initial_fen": self.initial_fen,
            "plies": [ply.to_dict() for ply in self.plies],
            "status": self.status.value,
            "result": self.result.value if self.result else None,
            "termination": self.termination.value if self.termination else None,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        data = _require_mapping(value, "game")
        if data.get("schema_version") != 1:
            raise GameValidationError("unsupported or missing game schema version")
        plies_data = data.get("plies")
        if not isinstance(plies_data, list):
            raise GameValidationError("game plies must be an array")
        try:
            return cls(
                id=data["id"],
                human_color=Color(data["human_color"]),
                initial_fen=data["initial_fen"],
                plies=[Ply.from_dict(ply) for ply in plies_data],
                status=GameStatus(data["status"]),
                result=GameResult(data["result"]) if data.get("result") is not None else None,
                termination=Termination(data["termination"])
                if data.get("termination") is not None
                else None,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GameValidationError("invalid game") from error
