import json

import pytest

from llmchess.models import Color, Game
from llmchess.store import GameStoreError, JsonGameStore


@pytest.mark.parametrize("game_id", ["../escape", "nested/game", ".hidden", "name.json"])
def test_path_for_rejects_unsafe_or_traversing_game_ids(
    tmp_path, monkeypatch, game_id: str
) -> None:
    monkeypatch.chdir(tmp_path)
    store = JsonGameStore()

    with pytest.raises(GameStoreError, match="unsafe game id"):
        store.path_for(game_id)


def test_store_round_trip_and_rejects_corrupt_json(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = JsonGameStore()
    game = Game(id="round-trip", human_color=Color.BLACK)
    store.create(game)

    assert store.load(game.id).to_dict() == game.to_dict()

    path = store.path_for(game.id)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(GameStoreError, match="could not read game"):
        store.load(game.id)


def test_store_rejects_structurally_invalid_serialized_game(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = JsonGameStore()
    path = store.path_for("bad-game")
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    with pytest.raises(GameStoreError, match="invalid game file"):
        store.load("bad-game")


def test_store_uses_repository_games_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert JsonGameStore().base_dir == tmp_path / "games"


def test_generate_id_increments_base36_and_ignores_legacy_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = JsonGameStore()

    assert store.generate_id() == "0000"
    store.base_dir.mkdir()
    (store.base_dir / "0009.json").touch()
    (store.base_dir / "g_legacy.json").touch()

    assert store.generate_id() == "000a"


def test_generate_id_carries_and_rejects_exhaustion(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = JsonGameStore()
    store.base_dir.mkdir()
    (store.base_dir / "00zz.json").touch()

    assert store.generate_id() == "0100"

    (store.base_dir / "zzzz.json").touch()
    with pytest.raises(GameStoreError, match="game id space exhausted"):
        store.generate_id()
