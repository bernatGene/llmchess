from copy import deepcopy

import pytest

from llmchess.game import MoveError, actor_for, apply_move, board_for
from llmchess.models import (
    Actor,
    Color,
    Game,
    GameResult,
    GameStatus,
    GameValidationError,
    Termination,
)


def test_san_and_uci_input_are_saved_in_canonical_form() -> None:
    game = Game(id="canonical", human_color=Color.WHITE)

    human_ply = apply_move(game, " e2e4 ", actor=Actor.HUMAN)
    llm_ply = apply_move(game, "e5", actor=Actor.LLM, explanation="Claims the center.")

    assert (human_ply.uci, human_ply.san) == ("e2e4", "e4")
    assert (llm_ply.uci, llm_ply.san) == ("e7e5", "e5")
    assert board_for(game).fen().split()[0] == "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR"


def test_actor_and_color_must_match_the_side_to_move() -> None:
    game = Game(id="ownership", human_color=Color.WHITE)

    assert actor_for(game, Color.WHITE) is Actor.HUMAN
    assert actor_for(game, Color.BLACK) is Actor.LLM

    with pytest.raises(MoveError, match="expected human actor"):
        apply_move(game, "e4", actor=Actor.LLM, explanation="A move.")
    with pytest.raises(MoveError, match="expected black to move"):
        apply_move(game, "e4", actor=Actor.HUMAN, color=Color.BLACK)

    apply_move(game, "e4", actor=Actor.HUMAN)
    with pytest.raises(MoveError, match="expected llm actor"):
        apply_move(game, "e5", actor=Actor.HUMAN)


@pytest.mark.parametrize("explanation", [None, "", " \t\n "])
def test_llm_moves_require_a_non_whitespace_explanation(explanation: str | None) -> None:
    game = Game(id="explanation", human_color=Color.BLACK)

    with pytest.raises(MoveError, match="require an explanation"):
        apply_move(game, "e4", actor=Actor.LLM, explanation=explanation)

    assert game.plies == []


def test_illegal_move_does_not_mutate_game() -> None:
    game = Game(id="immutable", human_color=Color.WHITE)
    before = deepcopy(game.to_dict())

    with pytest.raises(MoveError, match="illegal or invalid move"):
        apply_move(game, "e5", actor=Actor.HUMAN)

    assert game.to_dict() == before


def test_checkmate_records_the_canonical_terminal_outcome() -> None:
    game = Game(id="mate", human_color=Color.WHITE)

    apply_move(game, "f3", actor=Actor.HUMAN)
    apply_move(game, "e5", actor=Actor.LLM, explanation="Opens the queen.")
    apply_move(game, "g4", actor=Actor.HUMAN)
    winning_ply = apply_move(game, "Qh4#", actor=Actor.LLM, explanation="Checkmate.")

    assert winning_ply.san == "Qh4#"
    assert game.status is GameStatus.TERMINAL
    assert game.result is GameResult.BLACK_WIN
    assert game.termination is Termination.CHECKMATE


def test_replayed_claimable_threefold_repetition_is_a_draw() -> None:
    game = Game(id="repetition", human_color=Color.WHITE)
    moves = ["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6", "Ng1"]

    for index, move in enumerate(moves):
        actor = Actor.HUMAN if index % 2 == 0 else Actor.LLM
        explanation = "Developing." if actor is Actor.LLM else None
        apply_move(game, move, actor=actor, explanation=explanation)

    assert game.status is GameStatus.TERMINAL
    assert game.result is GameResult.DRAW
    assert game.termination is Termination.THREEFOLD_REPETITION


def test_replay_rejects_noncanonical_serialized_san() -> None:
    game = Game(id="serialized", human_color=Color.WHITE)
    apply_move(game, "e4", actor=Actor.HUMAN)
    payload = game.to_dict()
    payload["plies"][0]["san"] = "e2e4"  # type: ignore[index]

    with pytest.raises(GameValidationError):
        Game.from_dict(payload)
