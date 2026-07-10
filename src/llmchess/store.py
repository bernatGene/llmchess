"""Small local JSON persistence for single-process or best-effort local use."""

import json
import os
import re
import secrets
import tempfile
from pathlib import Path

from .models import Game, GameValidationError

_SAFE_ID = re.compile(r"\A[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")


class GameStoreError(RuntimeError):
    """Raised for local game-store failures."""


class JsonGameStore:
    """JSON files with atomic replacement writes; it makes no concurrency guarantees."""

    def __init__(self) -> None:
        self.base_dir = Path.cwd() / "games"

    def generate_id(self) -> str:
        """Generate an identifier suitable for a game filename."""
        return f"g_{secrets.token_urlsafe(12).rstrip('=')}"

    def path_for(self, game_id: str) -> Path:
        if not _SAFE_ID.fullmatch(game_id):
            raise GameStoreError("unsafe game id")
        return self.base_dir / f"{game_id}.json"

    def create(self, game: Game) -> None:
        path = self.path_for(game.id)
        if path.exists():
            raise GameStoreError(f"game already exists: {game.id}")
        self._write(path, game.to_dict())

    def save(self, game: Game) -> None:
        self._write(self.path_for(game.id), game.to_dict())

    def load(self, game_id: str) -> Game:
        path = self.path_for(game_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, json.JSONDecodeError) as error:
            raise GameStoreError(f"could not read game {game_id}") from error
        try:
            return Game.from_dict(data)
        except GameValidationError as error:
            raise GameStoreError(f"invalid game file {game_id}") from error

    def list_games(self) -> list[Game]:
        if not self.base_dir.exists():
            return []
        games: list[Game] = []
        for path in sorted(self.base_dir.glob("*.json")):
            try:
                games.append(self.load(path.stem))
            except (FileNotFoundError, GameStoreError):
                continue
        return games

    def _write(self, path: Path, data: dict[str, object]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}.", suffix=".tmp", dir=self.base_dir
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except OSError as error:
            raise GameStoreError(f"could not write game {path.stem}") from error
        finally:
            temporary_path.unlink(missing_ok=True)
