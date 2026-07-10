import json
from io import StringIO

import chess
from rich.console import Console

import llmchess.cli as cli
from llmchess.cli import main
from llmchess.game import apply_move
from llmchess.models import Actor, Color, Game
from llmchess.render import render_board


def test_board_rendering_uses_ascii_by_default_and_unicode_when_requested() -> None:
    ascii_stream = StringIO()
    unicode_stream = StringIO()

    render_board(chess.Board(), Console(file=ascii_stream, force_terminal=False))
    render_board(
        chess.Board(),
        Console(file=unicode_stream, force_terminal=False),
        unicode_pieces=True,
    )

    assert "K" in ascii_stream.getvalue()
    assert "♔" not in ascii_stream.getvalue()
    assert "♔" in unicode_stream.getvalue()
    assert "♚" in unicode_stream.getvalue()


def test_unicode_flag_is_available_on_all_board_commands() -> None:
    parser = cli.build_parser()

    assert parser.parse_args(["new", "--unicode"]).unicode_pieces is True
    assert parser.parse_args(["show", "game-id", "--unicode"]).unicode_pieces is True
    assert parser.parse_args(["live", "game-id", "--unicode"]).unicode_pieces is True


def test_cli_json_human_vs_llm_game_persists_explanation(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["new", "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    game_id = created["id"]
    assert created["turn"] == "white"
    assert created["expected_actor"] == "human"

    assert main(["state", game_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["plies"] == []

    assert main(["move", game_id, "e2e4", "--actor", "human", "--json"]) == 0
    human_move = json.loads(capsys.readouterr().out)
    assert human_move["applied"]["san"] == "e4"

    explanation = "I contest the center with a pawn."
    assert (
        main(
            [
                "move",
                game_id,
                "e7e5",
                "--actor",
                "llm",
                "--explanation",
                explanation,
                "--json",
            ]
        )
        == 0
    )
    llm_move = json.loads(capsys.readouterr().out)
    assert llm_move["applied"]["explanation"] == explanation

    assert main(["show", game_id, "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["plies"][-1]["explanation"] == explanation
    assert shown["turn"] == "white"


def test_cli_returns_nonzero_json_error_for_invalid_move(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "--json"]) == 0
    game_id = json.loads(capsys.readouterr().out)["id"]

    result = main(["move", game_id, "e4", "--actor", "llm", "--json"])
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error": "expected human actor for white",
        "type": "MoveError",
    }


def test_live_frame_shows_latest_move_and_explanation_before_board(monkeypatch) -> None:
    game = Game(id="live", human_color=Color.WHITE)
    apply_move(game, "e4", actor=Actor.HUMAN)
    explanation = "Position: Balanced | Candidates: e7e5/e5 | Choice: e7e5 controls the center"
    apply_move(game, "e5", actor=Actor.LLM, explanation=explanation)
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, width=100)
    monkeypatch.setattr(
        cli,
        "render_board",
        lambda board, output, perspective, **kwargs: output.print("BOARD"),
    )

    cli._live_frame(game, console, Color.WHITE, len(game.plies) - 1)

    rendered = stream.getvalue()
    assert "1. e4" not in rendered
    assert rendered.index("1... e5 (llm)") < rendered.index(explanation)
    assert rendered.index(explanation) < rendered.index("Game live")
    assert rendered.index("Game live") < rendered.index("BOARD")


def test_live_exits_after_redrawing_a_terminal_update(monkeypatch) -> None:
    active = Game(id="live-terminal", human_color=Color.WHITE)
    apply_move(active, "f3", actor=Actor.HUMAN)
    apply_move(active, "e5", actor=Actor.LLM, explanation="Opens the queen.")
    apply_move(active, "g4", actor=Actor.HUMAN)
    terminal = Game.from_dict(active.to_dict())
    apply_move(terminal, "Qh4#", actor=Actor.LLM, explanation="Checkmate.")

    class Store:
        def load(self, game_id: str) -> Game:
            assert game_id == active.id
            return terminal

    frames: list[tuple[int, int]] = []
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        cli,
        "_live_frame",
        lambda game, console, perspective, first_ply, **kwargs: frames.append(
            (len(game.plies), first_ply)
        ),
    )

    cli._live(Store(), active, Console(file=StringIO()), Color.WHITE)  # type: ignore[arg-type]

    assert frames == [(3, 2), (4, 3)]
