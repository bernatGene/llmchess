---
description: Start or resume a persistent game against the OpenCode chess player.
agent: chess-player
---

Start or resume a chess game according to `$ARGUMENTS`.

- With no arguments, ask whether the human wants white or black, then create a game.
- With `white` or `black`, create a game with that human color.
- With a game ID, load and resume that game.

Use only the compact tools described by the chess-player agent. Report the game
ID, but leave board rendering to the player's `llmchess live` terminal. If the
loaded position says the LLM is to move, provide the public explanation and play
one legal move immediately. If it is the human's turn, ask for their move.
