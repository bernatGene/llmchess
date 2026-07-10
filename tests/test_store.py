import json

import pytest

from llmchess.models import Color, Game
from llmchess.store import GameStoreError, JsonGameStore


@pytest.mark.parametrize("game_id", ["../escape", "nested/game", ".hidden", "name.json"])
def test_path_for_rejects_unsafe_or_traversing_game_ids(tmp_path, game_id: str) -> None:
    store = JsonGameStore(tmp_path / "games")

    with pytest.raises(GameStoreError, match="unsafe game id"):
        store.path_for(game_id)


def test_store_round_trip_and_rejects_corrupt_json(tmp_path) -> None:
    store = JsonGameStore(tmp_path / "games")
    game = Game(id="round-trip", human_color=Color.BLACK)
    store.create(game)

    assert store.load(game.id).to_dict() == game.to_dict()

    path = store.path_for(game.id)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(GameStoreError, match="could not read game"):
        store.load(game.id)


def test_store_rejects_structurally_invalid_serialized_game(tmp_path) -> None:
    store = JsonGameStore(tmp_path / "games")
    path = store.path_for("bad-game")
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    with pytest.raises(GameStoreError, match="invalid game file"):
        store.load("bad-game")
