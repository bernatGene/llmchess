# CURRENT STATE

This file describes the current state of the app and what are the primary objectives. It
is to keep track of what we have and what we're working towards, but in a more general
view, not the individual task at hand.



## State summary

- Persistent local human-vs-LLM chess games, stored as validated JSON files
- Finite CLI commands for game creation, state, moves, rendering, transcripts,
  and listing; SAN and UCI moves are validated by `python-chess`
- OpenCode `/chess` command and restricted `chess-player` agent for creating and
  resuming games through compact custom tools
- Agent position responses contain FEN, compact ASCII occupancy, and legal moves,
  while the human can run a live board display in a separate terminal
- Vision-capable agents are explicitly instructed to inspect one 256x256
  pixel-art PNG per LLM position as a direct tool attachment; text-only models
  retain the complete textual path
- The chess agent has two shared, non-persistent analysis calls per LLM turn for
  trying lines up to three plies or inspecting one piece's moves and attacks
- LLM moves retain concise public chess explanations and an optional model ID
- The LLM can resign on its turn, persistently awarding the human the win
- Tooling: `uv`, `ruff` (formatter/linter), `ty` (type checker), and `pytest`


## Current objective

Make the persistent local game flow reliable, understandable, and easy for
humans and OpenCode agents to use.

## Superseded direction

The earlier multiplayer/session/watchdog direction has been replaced by
persistent, single-process human-vs-LLM games. There is no multiplayer lobby,
session lifecycle, or watchdog service in the current architecture.
