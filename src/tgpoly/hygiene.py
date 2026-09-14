"""Гигиена сообщений (спец. п.14): удаляются только устаревшие версии одного и того же
процессного сообщения (рендер поля, пересланный визард настроек) — события, реально
повлиявшие на партию (покупка, рента, банкротство, сделка, итог аукциона), не трогаем."""

from __future__ import annotations

import logging

from aiogram import Bot

from .models import GameState

logger = logging.getLogger(__name__)


async def replace_transient(bot: Bot, game: GameState, category: str, new_message_id: int) -> None:
    """Запоминает новое сообщение категории `category`, удаляя предыдущее (если было)."""
    old_id = game.transient_message_ids.get(category)
    game.transient_message_ids[category] = new_message_id
    if old_id is not None and old_id != new_message_id:
        try:
            await bot.delete_message(game.chat_id, old_id)
        except Exception:
            logger.debug("failed to delete transient message %s in chat %s", old_id, game.chat_id, exc_info=True)


async def delete_stray(bot: Bot, chat_id: int, message_id: int) -> None:
    """Удаление левого броска кости не по делу (п.3) или другого мусора игрока."""
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        logger.debug("failed to delete stray message %s in chat %s", message_id, chat_id, exc_info=True)
