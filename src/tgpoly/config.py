"""Конфигурация бота из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    bot_token: str

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("BOT_TOKEN")
        if not token:
            raise RuntimeError("Переменная окружения BOT_TOKEN не задана (см. .env.example).")
        return cls(bot_token=token)
