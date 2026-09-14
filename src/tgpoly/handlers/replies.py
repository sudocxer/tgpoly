"""Единая точка для текстовых reply-механик: визард настроек (п.13), досрочный старт "start"
(п.13), ставки аукциона (п.12), выбор суммы в конструкторе сделки (п.7). Один хендлер вместо
нескольких — иначе разные механики на одинаковом фильтре конфликтуют за то, кто обработает
сообщение первым. Команды (текст с "/") сюда не попадают — им нужны свои Command-хендлеры.

Фильтр ловит не только reply, но и обычный текст без reply — ставки аукциона (п.12) принимаются
и голым числом без reply, потому что люди часто забывают ответить именно на нужное сообщение."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from .. import flow, wizard
from ..state import manager

router = Router(name="replies")


@router.message(F.text, ~F.text.startswith("/"))
async def on_reply(message: Message) -> None:
    game = manager.get(message.chat.id)
    if game is None or message.text is None:
        return

    if await wizard.handle_settings_reply(message.bot, game, message):
        return

    if (
        game.join_poll is not None
        and message.reply_to_message is not None
        and message.reply_to_message.message_id == game.join_poll.message_id
        and message.text.strip().lower() == "start"
        and message.from_user is not None
    ):
        await wizard.try_early_start(message.bot, game, message.from_user.id)
        return

    if await flow.register_bid(message.bot, game, message):
        return

    if await flow.handle_money_picker_reply(message.bot, game, message):
        return
