import base64
import json
from io import BytesIO, StringIO

import chess
import pytest
from PIL import Image
from rich.console import Console

import llmchess.cli as cli
from llmchess.cli import main
from llmchess.game import apply_move
from llmchess.models import Actor, Color, Game
from llmchess.render import board_png, render_board
from llmchess.store import JsonGameStore


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
    assert "♚" not in ascii_stream.getvalue()
    assert "♚" in unicode_stream.getvalue()
    assert "♔" not in unicode_stream.getvalue()


def test_large_board_uses_multicell_pixel_art() -> None:
    stream = StringIO()

    render_board(
        chess.Board(),
        Console(file=stream, force_terminal=False, width=100),
        large_pieces=True,
    )

    rendered = stream.getvalue()
    assert "▀" in rendered
    assert "▄" in rendered
    assert "█" in rendered
    assert len(rendered.splitlines()) == 34
    assert len(rendered.splitlines()[1]) >= 66
    assert " " not in rendered.splitlines()[1].strip()


def test_board_png_upscales_pixel_masks_and_respects_perspective() -> None:
    board = chess.Board(None)
    board.set_piece_at(chess.A8, chess.Piece(chess.KING, chess.WHITE))

    white = Image.open(BytesIO(board_png(board, Color.WHITE)))
    black = Image.open(BytesIO(board_png(board, Color.BLACK)))

    assert white.size == (256, 256)
    assert white.format == "PNG"
    assert white.getpixel((14, 6)) == (255, 255, 255)
    assert black.getpixel((238, 230)) == (255, 255, 255)
    assert white.getpixel((14, 6)) != white.getpixel((10, 6))


def test_board_style_flags_are_available_on_all_board_commands() -> None:
    parser = cli.build_parser()

    assert parser.parse_args(["new"]).board_style == "large"
    assert parser.parse_args(["show", "game-id"]).board_style == "large"
    assert parser.parse_args(["live", "game-id"]).board_style == "large"
    assert parser.parse_args(["new", "--unicode"]).board_style == "unicode"
    assert parser.parse_args(["show", "game-id", "--unicode"]).board_style == "unicode"
    assert parser.parse_args(["live", "game-id", "--unicode"]).board_style == "unicode"
    assert parser.parse_args(["new", "--minimal"]).board_style == "minimal"
    assert parser.parse_args(["show", "game-id", "--minimal"]).board_style == "minimal"
    assert parser.parse_args(["live", "game-id", "--minimal"]).board_style == "minimal"
    match = parser.parse_args(
        ["match", "--white-model", "test/white", "--black-model", "test/black"]
    )
    assert match.board_style == "large"
    assert match.move_timeout == 120.0
    assert match.max_plies is None


@pytest.mark.parametrize(
    "arguments",
    [
        ["--move-timeout", "0"],
        ["--move-timeout", "-1"],
        ["--move-timeout", "nan"],
        ["--move-timeout", "inf"],
        ["--max-plies", "0"],
        ["--max-plies", "-1"],
    ],
)
def test_match_parser_rejects_nonpositive_limits(arguments: list[str]) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "match",
                "--white-model",
                "test/white",
                "--black-model",
                "test/black",
                *arguments,
            ]
        )


def test_match_parser_rejects_model_without_provider() -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["match", "--white-model", "white", "--black-model", "test/black"])


def test_match_cli_returns_130_after_coordinator_interruption(monkeypatch) -> None:
    def interrupt(*args, **kwargs) -> None:
        raise cli.MatchInterrupted

    monkeypatch.setattr(cli, "run_match", interrupt)

    assert main(["match", "--white-model", "test/white", "--black-model", "test/black"]) == 130


def test_cli_uses_large_board_by_default_and_allows_compact_styles(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    styles: list[tuple[bool, bool]] = []
    monkeypatch.setattr(
        cli,
        "render_game",
        lambda *args, **kwargs: styles.append((kwargs["unicode_pieces"], kwargs["large_pieces"])),
    )

    assert main(["new"]) == 0
    assert main(["new", "--minimal"]) == 0
    assert main(["new", "--unicode"]) == 0

    assert styles == [(False, True), (False, False), (True, False)]


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


def test_cli_image_returns_current_fen_and_png(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "--json"]) == 0
    game_id = json.loads(capsys.readouterr().out)["id"]

    assert main(["image", game_id, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    image = Image.open(BytesIO(base64.b64decode(payload["png_base64"])))

    assert payload["game_id"] == game_id
    assert payload["fen"] == chess.Board().fen()
    assert payload["perspective"] == "white"
    assert payload["mime"] == "image/png"
    assert image.size == (256, 256)


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


def test_cli_llm_resignation_persists_terminal_result(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "--human", "black", "--json"]) == 0
    game_id = json.loads(capsys.readouterr().out)["id"]

    assert main(["resign", game_id, "--actor", "llm", "--json"]) == 0
    resigned = json.loads(capsys.readouterr().out)

    assert resigned["status"] == "terminal"
    assert resigned["result"] == "0-1"
    assert resigned["termination"] == "resignation"
    assert main(["state", game_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["termination"] == "resignation"


def test_cli_analysis_shows_board_and_piece_attacks_without_persisting(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    game = Game(
        id="analysis",
        human_color=Color.WHITE,
        initial_fen="rn1qkb1r/ppN1pppp/2p2n2/3p1b2/3P1B2/8/PPP1PPPP/R2QKBNR b KQkq - 1 5",
    )
    store = JsonGameStore()
    store.create(game)
    saved = store.path_for(game.id).read_text(encoding="utf-8")

    assert main(["try-line", game.id, "d8c7", "--json"]) == 0
    tried = json.loads(capsys.readouterr().out)
    assert tried["board"][1] == "ppq.pppp"
    assert {move["san"] for move in tried["legal_moves"]} >= {"Bxc7"}

    assert main(["piece-moves", game.id, "f4", "--json"]) == 0
    piece = json.loads(capsys.readouterr().out)
    assert piece["piece"] == "white bishop"
    assert {"e5", "d6", "c7"} <= set(piece["attacks"])
    assert piece["legal_moves"] == []
    assert store.path_for(game.id).read_text(encoding="utf-8") == saved


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
