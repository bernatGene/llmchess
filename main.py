from pathlib import Path

import chess
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console

COMMANDS: dict[str, str] = {
    "startgame": "Start a new game",
    "resetgame": "Reset the current game",
    "move": "Make a move: move e4",
    "printboard": "Show the board (ASCII)",
    "turn": "Show whose turn it is",
    "help": "Show this help",
    "quit": "Exit the program",
    "exit": "Exit the program",
}


def turn_text(board: chess.Board) -> str:
    return "Turn: White" if board.turn == chess.WHITE else "Turn: Black"


def print_help(console: Console) -> None:
    console.print("Commands:", style="bold")
    for name, description in COMMANDS.items():
        console.print(f"  {name:<10} {description}")
    console.print("Moves:")
    console.print("  SAN like e4, Nf3, O-O, exd5, Qh5+", style="dim")


def apply_san_move(board: chess.Board, san: str, console: Console) -> None:
    try:
        move = board.parse_san(san)
    except ValueError:
        console.print(f"Illegal move or invalid SAN: {san}", style="red")
        return
    board.push(move)
    console.print(f"Move ok: {san}. {turn_text(board)}", style="green")


def main() -> None:
    console = Console()
    board = chess.Board()
    history_file = Path(".llmchess_history")
    completer = WordCompleter(
        list(COMMANDS.keys()),
        ignore_case=True,
        sentence=True,
        meta_dict=COMMANDS,
    )
    session = PromptSession(
        completer=completer,
        auto_suggest=AutoSuggestFromHistory(),
        history=FileHistory(str(history_file)),
    )

    console.print("LLM Chess CLI. Type 'help' for commands.", style="bold")
    console.print(f"Game started. {turn_text(board)}", style="green")

    while True:
        try:
            raw = session.prompt("llmchess> ")
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye.", style="dim")
            break

        user_input = raw.strip()
        if not user_input:
            continue

        tokens = user_input.split()
        command = tokens[0].lower()

        if command in COMMANDS:
            if command in {"quit", "exit"}:
                console.print("Bye.", style="dim")
                break
            if command in {"startgame", "resetgame"}:
                board = chess.Board()
                console.print(f"Game started. {turn_text(board)}", style="green")
                continue
            if command == "printboard":
                console.print(board)
                console.print(turn_text(board), style="cyan")
                continue
            if command == "turn":
                console.print(turn_text(board), style="cyan")
                continue
            if command == "help":
                print_help(console)
                continue
            if command == "move":
                if len(tokens) < 2:
                    console.print("Usage: move <SAN>", style="yellow")
                    continue
                san = " ".join(tokens[1:]).strip()
                apply_san_move(board, san, console)
                continue

        apply_san_move(board, user_input, console)


if __name__ == "__main__":
    main()
