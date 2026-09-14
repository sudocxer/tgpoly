"""Файловый лог партии — один файл на партию, только для оператора бота (спец. п.14)."""

from __future__ import annotations

import logging
from pathlib import Path

LOGS_DIR = Path("logs")
_loggers: dict[str, logging.Logger] = {}


def get_game_logger(game_id: str) -> logging.Logger:
    if game_id in _loggers:
        return _loggers[game_id]

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"tgpoly.game.{game_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(LOGS_DIR / f"{game_id}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    _loggers[game_id] = logger
    return logger
