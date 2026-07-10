# llmchess

`llmchess` is a local, persistent chess harness for a human playing against an
LLM through OpenCode. `python-chess` is the rules authority: every recorded move
is replayed and validated before it is shown or extended.

## TL;DR: play against the LLM

From the repository, install the dependencies once:

```sh
uv sync
```

Open two side-by-side terminals in the repository:

1. In the first terminal, start OpenCode with `opencode`, then enter `/chess white`
   or `/chess black` in the chat.
2. OpenCode creates the game and shows its `GAME_ID`. In the second terminal,
   run `uv run llmchess live GAME_ID` to display the board.
3. Enter your moves in the OpenCode chat, for example `e4`, `Nf3`, or `e2e4`.
   OpenCode plays the LLM's replies, while the second terminal updates the board.

To continue an existing game, start OpenCode and enter `/chess GAME_ID`, then
run the same `live` command in the other terminal.

## Install and storage

Games are JSON files under the platform user data directory by default. Set
`LLMCHESS_DATA_DIR` or pass `--data-dir PATH` before the subcommand to use
another games directory:

```sh
uv run llmchess --data-dir .llmchess-data new --human black
```

On macOS the default is `~/Library/Application Support/llmchess/games`; on other
Unix systems it is `$XDG_DATA_HOME/llmchess/games` or
`~/.local/share/llmchess/games`.

## CLI commands

Most commands perform one bounded operation; add `--json` where supported for
machine-readable output. `live` is the human-facing exception: it watches a
game until completion or Ctrl-C and redraws when the position changes.

```text
llmchess new [--human white|black] [--json]
llmchess list [--json]
llmchess state GAME_ID [--json]
llmchess move GAME_ID MOVE --actor human|llm [--explanation TEXT] [--model ID] [--json]
llmchess show GAME_ID [--perspective white|black] [--json]
llmchess live GAME_ID [--perspective white|black]
llmchess transcript GAME_ID [--json]
```

`MOVE` may be SAN or UCI. The expected actor is determined from the saved game:
the human can only move their chosen color and the LLM moves the other color.
LLM moves require a non-empty `--explanation`; `--model` records an optional
model identifier. `state --json` includes the current FEN, expected actor,
canonical legal moves, and latest recorded move.

The display clears and redraws after each saved move. The latest move and its
public explanation appear first, followed by the status and board, so the
current position remains at the bottom of the terminal. Regular `show` output
also places the board after the transcript.

## OpenCode chess play

The repository supplies a `/chess` OpenCode command and the `chess-player`
agent, which is the default agent for this project. Restart OpenCode after
installing or changing this configuration. In OpenCode, use:

```text
/chess white
/chess black
/chess GAME_ID
```

With no argument, `/chess` asks which color the human wants. With a color it
creates a game; with an ID it resumes that game. The agent is restricted to four
project-local chess tools. Those tools retain CLI validation but return only the
current FEN and legal UCI/SAN moves when the LLM must play; they never return the
complete transcript or old explanations. A submitted human move directly
returns the resulting LLM position, avoiding a redundant state refresh. Use
`llmchess live GAME_ID` for the board display and switch to OpenCode's `build`
agent when working on the application itself.

LLM move explanations are deliberately public, concise chess summaries in this
form:

```text
Position: <assessment> | Candidates: <two or three moves> | Choice: <move and reason>
```

They are not hidden reasoning or chain-of-thought. The explanation is displayed
to the player and stored with the LLM move in the transcript.

## Architecture

- `src/llmchess/cli.py` parses and executes the finite commands.
- `src/llmchess/models.py` defines validated, serializable games and plies.
- `src/llmchess/game.py` rebuilds positions and applies legal SAN/UCI moves via
  `python-chess`.
- `src/llmchess/store.py` persists one game per JSON file using atomic file
  replacement.
- `src/llmchess/render.py` provides Rich board and transcript output.
- `.opencode/tools/chess.ts` exposes compact, validated tools to the chess agent.

## Development

```sh
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

## Limitations

- Persistence is local JSON and is intended for a single process or best-effort
  local use; it provides no concurrency or multi-user coordination guarantees.
- There is no network service, multiplayer lobby/session protocol, watchdog, or
  interactive move-entry loop. Persistent human-vs-LLM games and the read-only
  `live` display replace those earlier concepts.
- Standard chess positions and outcomes are supported through `python-chess`;
  clocks, engine analysis, authentication, and remote game sharing are not.
