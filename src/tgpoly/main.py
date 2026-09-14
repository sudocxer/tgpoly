"""Точка входа. Боту нужны права администратора группы + разрешение удалять сообщения (п.9)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from dotenv import load_dotenv

from .config import Config
from .handlers import router
from .render import ensure_board_template
from .storage import ensure_default_template

COMMANDS = [
    BotCommand(command="newgame", description="Создать новую партию"),
    BotCommand(command="pause", description="Поставить партию на паузу (админ)"),
    BotCommand(command="resume", description="Продолжить партию с паузы (админ)"),
    BotCommand(command="build", description="Постройка домов"),
    BotCommand(command="mortgage", description="Заложить / снять залог"),
    BotCommand(command="swap", description="Обмен с другим игроком"),
    BotCommand(command="bankrupt", description="Досрочно объявить банкротство"),
    BotCommand(command="me", description="Мои деньги и собственность"),
    BotCommand(command="board", description="Прислать поле ещё раз"),
    BotCommand(command="addplayer", description="Добавить игрока (админ, reply)"),
    BotCommand(command="transfer", description="Передать партию другому (админ, reply)"),
    BotCommand(command="reload_state", description="Перечитать состояние из Excel (админ)"),
]


async def run() -> None:
    load_dotenv()  # подхватывает .env из корня репозитория (или родительских каталогов)
    logging.basicConfig(level=logging.INFO)
    ensure_default_template()
    ensure_board_template()

    config = Config.from_env()
    # без parse_mode: игровые тексты (названия клеток, ники, подсказки команд вида "<сумма>")
    # не размечены под HTML/Markdown и не должны парситься как разметка — иначе Telegram падает
    # с "can't parse entities" на любом литеральном "<...>" (как и было до этого фикса).
    bot = Bot(token=config.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    await bot.set_my_commands(COMMANDS)
    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
