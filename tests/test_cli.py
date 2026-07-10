import json

from llmchess.cli import main


def test_cli_json_human_vs_llm_game_persists_explanation(tmp_path, capsys) -> None:
    data_dir = tmp_path / "games"

    assert main(["--data-dir", str(data_dir), "new", "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    game_id = created["id"]
    assert created["turn"] == "white"
    assert created["expected_actor"] == "human"

    assert main(["--data-dir", str(data_dir), "state", game_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["plies"] == []

    assert (
        main(["--data-dir", str(data_dir), "move", game_id, "e2e4", "--actor", "human", "--json"])
        == 0
    )
    human_move = json.loads(capsys.readouterr().out)
    assert human_move["applied"]["san"] == "e4"

    explanation = "I contest the center with a pawn."
    assert (
        main(
            [
                "--data-dir",
                str(data_dir),
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

    assert main(["--data-dir", str(data_dir), "show", game_id, "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["plies"][-1]["explanation"] == explanation
    assert shown["turn"] == "white"


def test_cli_returns_nonzero_json_error_for_invalid_move(tmp_path, capsys) -> None:
    data_dir = tmp_path / "games"
    assert main(["--data-dir", str(data_dir), "new", "--json"]) == 0
    game_id = json.loads(capsys.readouterr().out)["id"]

    result = main(["--data-dir", str(data_dir), "move", game_id, "e4", "--actor", "llm", "--json"])
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error": "expected human actor for white",
        "type": "MoveError",
    }
