import json
from collections.abc import Callable
from io import StringIO

import pytest
from rich.console import Console

from llmchess.game import apply_move, resign_game
from llmchess.match import (
    FALLBACK_EXPLANATION,
    MatchError,
    MatchInterrupted,
    MoveEvent,
    ProcessResult,
    RunRequest,
    parse_opencode_events,
    run_match,
)
from llmchess.models import Actor, GameStatus, Termination
from llmchess.store import JsonGameStore


def tool_event(
    session_id: str,
    tool: str,
    inputs: dict[str, str],
    output: dict[str, str],
    *,
    status: str = "completed",
) -> str:
    return json.dumps(
        {
            "type": "tool_use",
            "sessionID": session_id,
            "part": {
                "tool": tool,
                "state": {
                    "status": status,
                    "input": inputs,
                    "output": json.dumps(output),
                },
            },
        }
    )


def test_event_parser_extracts_one_move_and_ignores_other_events() -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "text", "sessionID": "session-1", "part": {}}),
            tool_event(
                "session-1",
                "chess_llm_move",
                {"game_id": "game", "move": "e4", "explanation": "Claims the center."},
                {"accepted": "e4"},
            ),
        ]
    )

    parsed = parse_opencode_events(stdout, game_id="game")

    assert parsed.session_id == "session-1"
    assert parsed.action == MoveEvent("game", "Claims the center.", "e4")
    assert len(parsed.events) == 2


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ("not-json", "invalid OpenCode JSON"),
        (
            "\n".join(
                [
                    json.dumps({"type": "text", "sessionID": "one"}),
                    json.dumps({"type": "text", "sessionID": "two"}),
                ]
            ),
            "inconsistent session IDs",
        ),
        (
            tool_event(
                "one",
                "chess_llm_move",
                {"game_id": "other", "explanation": "No."},
                {"accepted": "e4"},
            ),
            "referred to game other",
        ),
    ],
)
def test_event_parser_rejects_invalid_or_ambiguous_output(stdout: str, message: str) -> None:
    with pytest.raises(MatchError, match=message):
        parse_opencode_events(stdout, game_id="game")


def scripted_runner(
    store: JsonGameStore, moves: list[str], requests: list[RunRequest]
) -> Callable[[RunRequest], ProcessResult]:
    def run(request: RunRequest) -> ProcessResult:
        requests.append(request)
        game = store.load(request.game_id)
        move = moves.pop(0)
        explanation = f"Play {move}."
        ply = apply_move(game, move, actor=Actor.LLM, explanation=explanation)
        store.save(game)
        if game.status is GameStatus.TERMINAL:
            assert game.result is not None
            assert game.termination is not None
            output = {"result": game.result.value, "termination": game.termination.value}
        else:
            output = {"accepted": ply.san}
        session = f"session-{ply.color.value}"
        return ProcessResult(
            0,
            tool_event(
                session,
                "chess_llm_move",
                {"game_id": game.id, "move": move, "explanation": explanation},
                output,
            ),
            "",
        )

    return run


def test_match_alternates_sessions_and_completes_synchronized_checkmate(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = JsonGameStore()
    requests: list[RunRequest] = []
    output = StringIO()

    summary = run_match(
        store,
        Console(file=output, force_terminal=False, width=100),
        white_model="test/white",
        black_model="test/black",
        board_style="minimal",
        runner=scripted_runner(store, ["f3", "e5", "g4", "Qh4#"], requests),
    )

    white = store.load(summary.white_game_id)
    black = store.load(summary.black_game_id)
    assert [ply.uci for ply in white.plies] == [ply.uci for ply in black.plies]
    assert white.status is black.status is GameStatus.TERMINAL
    assert white.termination is black.termination is Termination.CHECKMATE
    assert [request.session_id for request in requests] == [
        None,
        None,
        "session-white",
        "session-black",
    ]
    assert [ply.model for ply in white.plies] == ["test/white", None, "test/white", None]
    assert [ply.model for ply in black.plies] == [None, "test/black", None, "test/black"]


def test_match_stops_at_max_plies_without_recording_a_draw(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = JsonGameStore()

    summary = run_match(
        store,
        Console(file=StringIO(), force_terminal=False),
        white_model="test/white",
        black_model="test/black",
        max_plies=1,
        board_style="minimal",
        runner=scripted_runner(store, ["e4"], []),
    )

    assert summary.stopped_at_limit
    for game_id in (summary.white_game_id, summary.black_game_id):
        game = store.load(game_id)
        assert len(game.plies) == 1
        assert game.status is GameStatus.ACTIVE
        assert game.result is None


def test_second_timeout_applies_one_random_fallback_and_resets_next_turn(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = JsonGameStore()
    requests: list[RunRequest] = []

    def runner(request: RunRequest) -> ProcessResult:
        requests.append(request)
        if len(requests) == 2:
            return ProcessResult(-2, "", "", timed_out=True)
        return ProcessResult(
            -2,
            json.dumps({"type": "text", "sessionID": f"session-{request.game_id}"}),
            "",
            timed_out=True,
        )

    summary = run_match(
        store,
        Console(file=StringIO(), force_terminal=False),
        white_model="test/white",
        black_model="test/black",
        max_plies=1,
        board_style="minimal",
        runner=runner,
        choose_move=lambda moves: "e2e4",
    )

    white = store.load(summary.white_game_id)
    black = store.load(summary.black_game_id)
    assert len(requests) == 2
    assert requests[0].session_id is None
    assert requests[1].session_id == f"session-{summary.white_game_id}"
    assert white.plies[0].uci == black.plies[0].uci == "e2e4"
    assert white.plies[0].explanation == FALLBACK_EXPLANATION


def test_move_persisted_at_timeout_is_relayed_without_retry(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = JsonGameStore()
    calls = 0

    def runner(request: RunRequest) -> ProcessResult:
        nonlocal calls
        calls += 1
        game = store.load(request.game_id)
        apply_move(game, "e4", actor=Actor.LLM, explanation="Claims the center.")
        store.save(game)
        return ProcessResult(
            -2,
            json.dumps({"type": "text", "sessionID": "session-white"}),
            "",
            timed_out=True,
        )

    summary = run_match(
        store,
        Console(file=StringIO(), force_terminal=False),
        white_model="test/white",
        black_model="test/black",
        max_plies=1,
        board_style="minimal",
        runner=runner,
    )

    assert calls == 1
    assert len(store.load(summary.white_game_id).plies) == 1
    assert len(store.load(summary.black_game_id).plies) == 1


def test_interruption_reports_recovery_and_preserves_games(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = JsonGameStore()
    output = StringIO()

    def runner(request: RunRequest) -> ProcessResult:
        raise MatchInterrupted(ProcessResult(-2, "", ""))

    with pytest.raises(MatchInterrupted):
        run_match(
            store,
            Console(file=output, force_terminal=False),
            white_model="test/white",
            black_model="test/black",
            board_style="minimal",
            runner=runner,
        )

    games = store.list_games()
    assert len(games) == 2
    assert all(not game.plies for game in games)
    assert "Match interrupted" in output.getvalue()
    assert "Games: white=" in output.getvalue()


def test_resignation_stops_without_corrupting_mirror(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = JsonGameStore()

    def runner(request: RunRequest) -> ProcessResult:
        game = store.load(request.game_id)
        resign_game(game, actor=Actor.LLM)
        store.save(game)
        assert game.result is not None
        return ProcessResult(
            0,
            tool_event(
                "session-white",
                "chess_resign",
                {"game_id": game.id, "reason": "No winning chances."},
                {"result": game.result.value, "termination": "resignation"},
            ),
            "",
        )

    summary = run_match(
        store,
        Console(file=StringIO(), force_terminal=False),
        white_model="test/white",
        black_model="test/black",
        board_style="minimal",
        runner=runner,
    )

    assert store.load(summary.white_game_id).termination is Termination.RESIGNATION
    assert store.load(summary.black_game_id).status is GameStatus.ACTIVE
