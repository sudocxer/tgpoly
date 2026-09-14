"""Диспетчер партий: одна активная партия на чат, in-memory + Excel-снэпшот (спец. п.10)."""

from __future__ import annotations

from pathlib import Path

from . import storage
from .models import GameState


class GameManager:
    def __init__(self) -> None:
        self.games: dict[int, GameState] = {}

    def get(self, chat_id: int) -> GameState | None:
        return self.games.get(chat_id)

    def start_new(self, chat_id: int, group_title: str, admin_telegram_id: int) -> GameState:
        settings = storage.load_default_settings()
        game_id = storage.next_game_id()
        path = storage.game_file_path(group_title, game_id)
        storage.create_game_file(path, settings)
        game = GameState(
            chat_id=chat_id,
            game_id=game_id,
            group_title=group_title,
            file_path=str(path),
            settings=settings,
            admin_telegram_id=admin_telegram_id,
        )
        self.games[chat_id] = game
        storage.upsert_registry(game, storage.STATUS_ACTIVE)
        return game

    def save(self, game: GameState) -> None:
        storage.save_game(game)

    def reload(self, chat_id: int) -> GameState | None:
        """/reload_state (п.10, п.11) — перечитать файл партии с диска поверх памяти."""
        game = self.games.get(chat_id)
        if game is None:
            return None
        reloaded = storage.load_game(Path(game.file_path), chat_id, game.game_id, game.group_title)
        self.games[chat_id] = reloaded
        return reloaded

    def attach(self, game: GameState) -> None:
        """Партия, поднятая с диска (/resume) — подключаем к диспетчеру как активную."""
        self.games[game.chat_id] = game

    def remove(self, chat_id: int) -> None:
        self.games.pop(chat_id, None)


manager = GameManager()
