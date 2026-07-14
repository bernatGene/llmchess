"""Coordinate two OpenCode agents through complementary persisted games."""

import json
import os
import random
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from rich.console import Console

from .game import apply_move, board_for
from .models import Actor, Color, Game, GameStatus, Ply, Termination
from .render import render_board
from .store import JsonGameStore

TIMEOUT_WARNING = (
    "Time is up, you need to decide on a move without thinking; if warned again, "
    "a random move will be played for you."
)
FALLBACK_EXPLANATION = "Clock: Random legal move selected after two timeouts."
_STDERR_LIMIT = 2_000
_TERMINATE_GRACE = 2.0


class MatchError(ValueError):
    """Raised when match orchestration cannot continue safely."""


class MatchInterrupted(KeyboardInterrupt):
    """Raised after an active OpenCode process has been reaped."""

    def __init__(self, result: "ProcessResult | None" = None) -> None:
        super().__init__()
        self.result = result


@dataclass(frozen=True, slots=True)
class RunRequest:
    model: str
    game_id: str
    prompt: str
    timeout: float
    cwd: Path
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class MoveEvent:
    game_id: str
    explanation: str
    san: str | None


@dataclass(frozen=True, slots=True)
class ResignEvent:
    game_id: str
    reason: str
    result: str


ToolEvent = MoveEvent | ResignEvent


@dataclass(frozen=True, slots=True)
class ParsedEvents:
    session_id: str | None
    action: ToolEvent | None
    events: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class MatchSummary:
    white_game_id: str
    black_game_id: str
    plies: int
    stopped_at_limit: bool


Runner = Callable[[RunRequest], ProcessResult]


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MatchError(f"{name} must be an object")
    return cast(dict[str, Any], value)


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise MatchError(f"{name} must be a non-empty string")
    return value


def parse_opencode_events(
    stdout: str,
    *,
    game_id: str,
    expected_session_id: str | None = None,
) -> ParsedEvents:
    """Parse newline-delimited OpenCode events and one completed chess action."""
    events: list[dict[str, Any]] = []
    session_ids: set[str] = set()
    actions: list[ToolEvent] = []

    for line_number, line in enumerate(stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as error:
            raise MatchError(f"invalid OpenCode JSON event on line {line_number}") from error
        event = _object(decoded, f"OpenCode event on line {line_number}")
        events.append(event)
        session_id = event.get("sessionID")
        if session_id is not None:
            session_ids.add(_required_string(session_id, "OpenCode session ID"))

        if event.get("type") != "tool_use":
            continue
        part = _object(event.get("part"), "tool event part")
        tool_name = part.get("tool")
        if tool_name not in {"chess_llm_move", "chess_resign"}:
            continue
        state = _object(part.get("state"), f"{tool_name} state")
        status = state.get("status")
        if status in {"error", "failed"}:
            raise MatchError(f"{tool_name} failed")
        if status != "completed":
            continue
        inputs = _object(state.get("input"), f"{tool_name} input")
        event_game_id = _required_string(inputs.get("game_id"), f"{tool_name} game_id")
        if event_game_id != game_id:
            raise MatchError(f"{tool_name} referred to game {event_game_id}, expected {game_id}")
        output_text = _required_string(state.get("output"), f"{tool_name} output")
        try:
            output = _object(json.loads(output_text), f"{tool_name} decoded output")
        except json.JSONDecodeError as error:
            raise MatchError(f"{tool_name} returned invalid JSON") from error

        if tool_name == "chess_llm_move":
            explanation = _required_string(inputs.get("explanation"), "move explanation")
            accepted = output.get("accepted")
            san = _required_string(accepted, "accepted SAN") if accepted is not None else None
            if san is None and not (
                isinstance(output.get("result"), str) and isinstance(output.get("termination"), str)
            ):
                raise MatchError("chess_llm_move returned no accepted move")
            actions.append(MoveEvent(event_game_id, explanation, san))
        else:
            reason = _required_string(inputs.get("reason"), "resignation reason")
            result = _required_string(output.get("result"), "resignation result")
            if output.get("termination") != Termination.RESIGNATION.value:
                raise MatchError("chess_resign returned the wrong termination")
            actions.append(ResignEvent(event_game_id, reason, result))

    if len(session_ids) > 1:
        raise MatchError("OpenCode invocation emitted inconsistent session IDs")
    session_id = next(iter(session_ids), None)
    if expected_session_id is not None and session_id != expected_session_id:
        raise MatchError("OpenCode continuation emitted the wrong session ID")
    if len(actions) > 1:
        raise MatchError("OpenCode invocation completed multiple chess actions")
    return ParsedEvents(session_id, actions[0] if actions else None, tuple(events))


def _stop_process(process: subprocess.Popen[str], interrupt: int) -> tuple[str, str]:
    try:
        os.killpg(process.pid, interrupt)
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=_TERMINATE_GRACE)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.communicate()


def run_opencode(request: RunRequest) -> ProcessResult:
    """Run one bounded OpenCode turn and reap its entire process group."""
    command = [
        "opencode",
        "run",
        "--format",
        "json",
        "--agent",
        "chess-player",
        "--model",
        request.model,
    ]
    if request.session_id is not None:
        command.extend(["--session", request.session_id])
    command.append(request.prompt)
    try:
        process = subprocess.Popen(
            command,
            cwd=request.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as error:
        raise MatchError("OpenCode executable not found") from error

    try:
        stdout, stderr = process.communicate(timeout=request.timeout)
    except subprocess.TimeoutExpired:
        stdout, stderr = _stop_process(process, signal.SIGINT)
        return ProcessResult(process.returncode, stdout, stderr[-_STDERR_LIMIT:], timed_out=True)
    except KeyboardInterrupt as error:
        stdout, stderr = _stop_process(process, signal.SIGINT)
        result = ProcessResult(process.returncode, stdout, stderr[-_STDERR_LIMIT:])
        raise MatchInterrupted(result) from error
    return ProcessResult(process.returncode, stdout, stderr[-_STDERR_LIMIT:])


def _assert_synchronized(white_game: Game, black_game: Game) -> None:
    if white_game.initial_fen != black_game.initial_fen:
        raise MatchError("mirrored games have different initial positions")
    if [ply.uci for ply in white_game.plies] != [ply.uci for ply in black_game.plies]:
        raise MatchError("mirrored games have different move histories")
    if board_for(white_game).fen() != board_for(black_game).fen():
        raise MatchError("mirrored games have different positions")
    if (
        white_game.status,
        white_game.result,
        white_game.termination,
    ) != (
        black_game.status,
        black_game.result,
        black_game.termination,
    ):
        raise MatchError("mirrored games have different outcomes")


def _new_games(store: JsonGameStore) -> tuple[Game, Game]:
    white_game = Game(id=store.generate_id(), human_color=Color.BLACK)
    store.create(white_game)
    black_game = Game(id=store.generate_id(), human_color=Color.WHITE)
    store.create(black_game)
    return white_game, black_game


def _new_ply(before: Game, after: Game, color: Color) -> Ply | None:
    if after.plies == before.plies:
        return None
    if len(after.plies) != len(before.plies) + 1 or after.plies[:-1] != before.plies:
        raise MatchError("source game has an unexpected move-history mutation")
    ply = after.plies[-1]
    if ply.actor is not Actor.LLM or ply.color is not color:
        raise MatchError("source game recorded the move for the wrong actor or color")
    return ply


def _verify_move_event(ply: Ply, action: ToolEvent | None, *, required: bool) -> None:
    if action is None:
        if required:
            raise MatchError("OpenCode persisted a move without a completed move event")
        return
    if not isinstance(action, MoveEvent):
        raise MatchError("persisted move disagrees with OpenCode action")
    if action.san is not None and action.san != ply.san:
        raise MatchError("persisted move disagrees with OpenCode accepted SAN")
    if action.explanation != ply.explanation:
        raise MatchError("persisted move disagrees with OpenCode explanation")


def _verify_resignation(
    game: Game, action: ToolEvent | None, *, required: bool
) -> ResignEvent | None:
    if game.termination is not Termination.RESIGNATION:
        return None
    if action is None:
        if required:
            raise MatchError("OpenCode persisted a resignation without a completed event")
        return None
    if not isinstance(action, ResignEvent) or action.result != game.result:
        raise MatchError("persisted resignation disagrees with OpenCode action")
    return action


def _relay(store: JsonGameStore, source: Game, mirror: Game, model: str) -> tuple[Game, Game, Ply]:
    ply = source.plies[-1]
    if ply.model != model:
        ply.model = model
        store.save(source)
    relayed = apply_move(mirror, ply.uci, actor=Actor.HUMAN, color=ply.color)
    store.save(mirror)
    source = store.load(source.id)
    mirror = store.load(mirror.id)
    _assert_synchronized(source, mirror)
    return source, mirror, relayed


def _show_move(
    console: Console,
    ply: Ply,
    model: str,
    game: Game,
    *,
    board_style: str,
    timeout_marker: str | None = None,
) -> None:
    move_number = (len(game.plies) + 1) // 2
    marker = "." if ply.color is Color.WHITE else "..."
    suffix = f" [{timeout_marker}]" if timeout_marker else ""
    console.print(
        f"{move_number}{marker} {ply.san} ({ply.uci}) - {model}{suffix}",
        style="bold green",
    )
    console.print(ply.explanation or "")
    render_board(
        board_for(game),
        console,
        Color.WHITE,
        unicode_pieces=board_style == "unicode",
        large_pieces=board_style == "large",
    )
    if game.status is GameStatus.TERMINAL:
        console.print(f"Result: {game.result} ({game.termination})", style="bold cyan")
    else:
        console.print(f"{Color.from_chess(board_for(game).turn).value} to move")


def _recovery(
    console: Console,
    white_game_id: str,
    black_game_id: str,
    sessions: dict[Color, str | None],
) -> None:
    console.print(f"Games: white={white_game_id} black={black_game_id}")
    console.print(
        f"Sessions: white={sessions[Color.WHITE] or 'unknown'} "
        f"black={sessions[Color.BLACK] or 'unknown'}"
    )


def run_match(
    store: JsonGameStore,
    console: Console,
    *,
    white_model: str,
    black_model: str,
    move_timeout: float = 120.0,
    max_plies: int | None = None,
    board_style: str = "large",
    runner: Runner = run_opencode,
    cwd: Path | None = None,
    choose_move: Callable[[list[str]], str] = random.choice,
) -> MatchSummary:
    """Create and run a two-game mirrored LLM match."""
    white_game, black_game = _new_games(store)
    sessions: dict[Color, str | None] = {Color.WHITE: None, Color.BLACK: None}
    models = {Color.WHITE: white_model, Color.BLACK: black_model}
    worktree = cwd or Path.cwd()
    console.print(f"White: {white_model} (game {white_game.id})", style="bold")
    console.print(f"Black: {black_model} (game {black_game.id})", style="bold")

    try:
        while True:
            white_game = store.load(white_game.id)
            black_game = store.load(black_game.id)
            _assert_synchronized(white_game, black_game)
            if white_game.status is GameStatus.TERMINAL:
                return MatchSummary(white_game.id, black_game.id, len(white_game.plies), False)
            if max_plies is not None and len(white_game.plies) >= max_plies:
                console.print(f"Stopped at maximum {max_plies} plies.", style="yellow")
                return MatchSummary(white_game.id, black_game.id, len(white_game.plies), True)

            color = Color.from_chess(board_for(white_game).turn)
            source = white_game if color is Color.WHITE else black_game
            mirror = black_game if color is Color.WHITE else white_game
            model = models[color]
            before = source
            accepted: Ply | None = None
            timeout_marker: str | None = None

            for attempt in range(2):
                prompt = (
                    f"Resume game {source.id}. Inspect its current position and make exactly one "
                    "LLM move or resign. Do not create a new game."
                    if attempt == 0
                    else f"Resume game {source.id}. {TIMEOUT_WARNING}"
                )
                request = RunRequest(
                    model=model,
                    game_id=source.id,
                    prompt=prompt,
                    timeout=move_timeout,
                    cwd=worktree,
                    session_id=sessions[color],
                )
                try:
                    result = runner(request)
                except MatchInterrupted as interrupted:
                    current = store.load(source.id)
                    pending = _new_ply(before, current, color)
                    if pending is not None:
                        current, mirror, _ = _relay(store, current, mirror, model)
                    if interrupted.result is not None:
                        try:
                            parsed = parse_opencode_events(
                                interrupted.result.stdout,
                                game_id=source.id,
                            )
                        except MatchError:
                            parsed = None
                        if parsed is not None:
                            if sessions[color] is None:
                                sessions[color] = parsed.session_id
                    raise

                parsed = parse_opencode_events(
                    result.stdout,
                    game_id=source.id,
                    expected_session_id=None if result.timed_out else sessions[color],
                )
                if (
                    result.timed_out
                    and parsed.session_id is not None
                    and sessions[color] is not None
                    and parsed.session_id != sessions[color]
                ):
                    raise MatchError("OpenCode continuation emitted the wrong session ID")
                if not result.timed_out and parsed.session_id is None:
                    raise MatchError("OpenCode invocation emitted no session ID")
                if sessions[color] is None and parsed.session_id is not None:
                    console.print(f"{color.value.title()} session: {parsed.session_id}")
                sessions[color] = parsed.session_id or sessions[color]
                current = store.load(source.id)
                pending = _new_ply(before, current, color)
                resignation = _verify_resignation(
                    current,
                    parsed.action,
                    required=not result.timed_out,
                )
                if resignation is not None or current.termination is Termination.RESIGNATION:
                    reason = (
                        resignation.reason
                        if resignation
                        else "Reason unavailable after interruption."
                    )
                    console.print(
                        f"{color.value} ({model}) resigned: {reason}\n"
                        f"Result: {current.result}; source game {current.id}. "
                        f"Mirror game {mirror.id} remains active.",
                        style="yellow",
                    )
                    return MatchSummary(white_game.id, black_game.id, len(current.plies), False)
                if pending is not None:
                    _verify_move_event(pending, parsed.action, required=not result.timed_out)
                    source = current
                    accepted = pending
                    timeout_marker = "accepted at timeout" if result.timed_out else None
                    break
                if parsed.action is not None:
                    raise MatchError("OpenCode action was not persisted")
                if not result.timed_out:
                    detail = f": {result.stderr.strip()}" if result.stderr.strip() else ""
                    if result.returncode != 0:
                        raise MatchError(f"OpenCode exited with status {result.returncode}{detail}")
                    raise MatchError("OpenCode completed without a chess action")
                if attempt == 0:
                    console.print(TIMEOUT_WARNING, style="yellow")
                    continue

                legal_moves = [move.uci() for move in board_for(current).legal_moves]
                selected = choose_move(legal_moves)
                accepted = apply_move(
                    current,
                    selected,
                    actor=Actor.LLM,
                    color=color,
                    explanation=FALLBACK_EXPLANATION,
                    model=model,
                )
                store.save(current)
                source = current
                timeout_marker = "random after two timeouts"
                break

            if accepted is None:
                raise MatchError("match turn ended without a move")
            source, mirror, _ = _relay(store, source, mirror, model)
            if color is Color.WHITE:
                white_game, black_game = source, mirror
            else:
                black_game, white_game = source, mirror
            _show_move(
                console,
                source.plies[-1],
                model,
                source,
                board_style=board_style,
                timeout_marker=timeout_marker,
            )
    except (MatchInterrupted, KeyboardInterrupt) as error:
        console.print("Match interrupted.", style="yellow")
        _recovery(console, white_game.id, black_game.id, sessions)
        if isinstance(error, MatchInterrupted):
            raise
        raise MatchInterrupted from error
    except Exception:
        _recovery(console, white_game.id, black_game.id, sessions)
        raise
