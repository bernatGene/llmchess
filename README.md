# llmchess

`llmchess` is a local, persistent chess harness for a human playing against an
LLM through OpenCode. `python-chess` is the rules authority: every recorded move
is replayed and validated before it is shown or extended.

## Requirements

- [Git](https://git-scm.com/) to clone the repository.
- [uv](https://docs.astral.sh/uv/) to install Python and the project dependencies.
- [OpenCode](https://opencode.ai/docs/) to run the LLM chess opponent.
- Access to an LLM provider supported by OpenCode.

You do not need to install Python separately. The project requires Python 3.12
or newer, and `uv` can download a compatible version while syncing the project.

Install `uv` on macOS or Linux with its standalone installer:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or use Homebrew:

```sh
brew install uv
```

Install OpenCode with its standalone installer:

```sh
curl -fsSL https://opencode.ai/install | bash
```

Or use the current OpenCode Homebrew tap:

```sh
brew install anomalyco/tap/opencode
```

See the linked official documentation for Windows and other installation
methods. After either installer finishes, open a new terminal if `uv` or
`opencode` is not yet on your `PATH`.

## Installation

Clone the repository, enter it, and sync its dependencies:

```sh
git clone https://github.com/bernatGene/llmchess.git
cd llmchess
uv sync
```

The repository already contains its OpenCode project configuration, `/chess`
command, chess agent, and custom tools. Do not run `/init`; start OpenCode from
the repository root so it loads this configuration:

```sh
opencode
```

The first time you use OpenCode, connect an LLM provider by entering `/connect`
and following the prompts. You can use OpenCode Zen or another supported
provider for which you have credentials. Provider authentication belongs to
OpenCode and is not stored by this project.

## Play

Open two side-by-side terminals, both in the repository root:

1. In the first terminal, run `opencode`, then enter `/chess white` or
   `/chess black`.
2. OpenCode creates the game and reports its `GAME_ID`.
3. In the second terminal, run `uv run llmchess live GAME_ID` to display the
   board.
4. Enter moves in OpenCode using SAN or UCI, for example `e4`, `Nf3`, or
   `e2e4`. OpenCode plays the LLM replies while the board terminal updates.

To continue an existing game, enter `/chess GAME_ID` in OpenCode and run the
same `live` command in the second terminal.

## Storage

Games are stored as JSON files in the repository's `games/` directory. This
directory is gitignored, so games remain local and are not included in commits.
Always run OpenCode and the CLI from the repository root so every command uses
the same game store.

## CLI commands

Most commands perform one bounded operation; add `--json` where supported for
machine-readable output. `live` is the human-facing exception: it watches a
game until completion or Ctrl-C and redraws when the position changes.

```text
llmchess new [--human white|black] [--unicode|--large] [--json]
llmchess list [--json]
llmchess state GAME_ID [--json]
llmchess move GAME_ID MOVE --actor human|llm [--explanation TEXT] [--model ID] [--json]
llmchess show GAME_ID [--perspective white|black] [--unicode|--large] [--json]
llmchess live GAME_ID [--perspective white|black] [--unicode|--large]
llmchess transcript GAME_ID [--json]
```

`MOVE` may be SAN or UCI. The expected actor is determined from the saved game:
the human can only move their chosen color and the LLM moves the other color.
LLM moves require a non-empty `--explanation`; `--model` records an optional
model identifier. `state --json` includes the current FEN, expected actor,
canonical legal moves, and latest recorded move.

Board-producing commands use ASCII piece letters by default. Add `--unicode` to
`new`, `show`, or `live` to render filled Unicode chess symbols colored for each
side. Alternatively, `--large` renders each square as an 8-column by 4-row area
with pixel-art pieces loaded from the 8-by-8 masks in `src/llmchess/pieces/`.
The two display options are mutually exclusive and neither requires a Nerd Font.

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
