# llmchess

A local chess harness for playing against an LLM through
[OpenCode](https://opencode.ai/). Moves are validated by `python-chess` and games
are saved locally.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and
[OpenCode](https://opencode.ai/docs/).

```sh
uv sync
opencode
```

Run OpenCode from the repository root so it loads the included chess agent and
tools. Connect an LLM provider with `/connect` if needed.

## Play

Open two terminals in the repository root.

In OpenCode, start a game:

```text
/chess white
/chess black
```

Then display its board in the other terminal using the reported game ID:

```sh
uv run llmchess live GAME_ID
```

Enter moves in OpenCode as SAN or UCI, such as `e4`, `Nf3`, or `e2e4`. To resume
a game later, use `/chess GAME_ID` and run the same `live` command.

Games are stored in the gitignored `games/` directory.

## Run an LLM Match

Select two models using their OpenCode `provider/model` identifiers:

```sh
uv run llmchess match \
  --white-model PROVIDER/WHITE_MODEL \
  --black-model PROVIDER/BLACK_MODEL \
  --minimal
```

The command creates two complementary games, one for each model, and relays every
accepted move between them. It prints both game IDs and each model's OpenCode
session ID when available. The game IDs remain usable with `state`, `show`,
`transcript`, and `live` if the match stops.

Each move attempt has 120 seconds by default. Use `--move-timeout SECONDS` to
change it. After one timeout the same model session receives an urgent warning;
after a second timeout on that ply, the coordinator plays a random legal move.
A move persisted just before a timeout is accepted and is never duplicated.

Matches run to a chess outcome or resignation by default. `--max-plies N` stops
successfully at an active synchronized position without recording a draw. Ctrl-C
stops and reaps the active OpenCode process, reports recovery IDs, and exits with
status 130.

## CLI

```text
llmchess new [--human white|black] [--minimal|--unicode] [--json]
llmchess list [--json]
llmchess state GAME_ID [--json]
llmchess move GAME_ID MOVE --actor human|llm [--explanation TEXT] [--model ID] [--json]
llmchess resign GAME_ID --actor llm [--json]
llmchess try-line GAME_ID MOVE [MOVE ...] [--json]
llmchess piece-moves GAME_ID SQUARE [--line MOVE ...] [--json]
llmchess show GAME_ID [--perspective white|black] [--minimal|--unicode] [--json]
llmchess live GAME_ID [--perspective white|black] [--minimal|--unicode]
llmchess image GAME_ID [--perspective white|black] [--output PATH] [--json]
llmchess transcript GAME_ID [--json]
llmchess match --white-model PROVIDER/MODEL --black-model PROVIDER/MODEL [--move-timeout SECONDS] [--max-plies N] [--minimal|--unicode]
```

Board commands render the large pixel-art board by default. Use `--minimal` for
the compact board with ASCII piece letters, or `--unicode` for the compact board
with Unicode chess symbols.

`llmchess image` renders the same pixel-art pieces as a 256x256 PNG. Use
`--output board.png` for a file; `--json` returns the PNG as base64 for tool
integrations. The chess agent requests this image once per LLM position when its
model supports image input, while text-only models continue using FEN and the
compact board. The agent can also resign on its turn, which records a human win
with `resignation` as the termination.

Use `uv run llmchess --help` or `uv run llmchess COMMAND --help` for details.

## Development

```sh
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```
