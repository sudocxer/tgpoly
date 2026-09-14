"""Админские команды партии: добавление игрока, передача управления, ручной reload (спец. п.8, п.11).

`/addplayer` и `/transfer` работают через reply на сообщение нужного пользователя — не через
`@username`, по той же причине, что и `/swap` (п.11): не у всех есть публичный username."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from .. import flow, rules, storage
from ..models import GamePhase, Player
from ..state import manager

router = Router(name="admin")


@router.message(Command("addplayer"))
async def cmd_addplayer(message: Message) -> None:
    game = manager.get(message.chat.id)
    if game is None or message.from_user is None:
        return
    if message.from_user.id != game.admin_telegram_id:
        await message.reply("Только админ партии может добавить игрока.")
        return
    if message.reply_to_message is None or message.reply_to_message.from_user is None:
        await message.reply("Ответьте (reply) этой командой на сообщение нового игрока: /addplayer <сумма>")
        return
    if len(game.player_order) >= game.settings.max_players:
        await message.reply(f"Уже максимум игроков ({game.settings.max_players}).")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.reply("Формат: ответом на сообщение игрока — /addplayer <сумма>")
        return
    amount = int(parts[1].strip())
    cap = rules.new_player_money_cap(game)
    if amount > cap:
        await message.reply(f"Слишком много — лимит 50% от общей суммы в игре: {cap} (п.8).")
        return

    new_user = message.reply_to_message.from_user
    if game.player_by_telegram_id(new_user.id) is not None:
        await message.reply("Этот пользователь уже в партии.")
        return

    pid = game.next_game_player_id()
    game.players[pid] = Player(
        game_player_id=pid, telegram_id=new_user.id, name=new_user.full_name, money=amount, position=0,
    )
    game.player_order.append(pid)
    manager.save(game)
    await message.reply(f"{new_user.full_name} добавлен(а) в партию с {amount} на счету, встаёт на «Старт».")


@router.message(Command("transfer"))
async def cmd_transfer(message: Message) -> None:
    game = manager.get(message.chat.id)
    if game is None or message.from_user is None:
        return
    if message.from_user.id != game.admin_telegram_id:
        await message.reply("Только админ партии может передать управление.")
        return
    if message.reply_to_message is None or message.reply_to_message.from_user is None:
        await message.reply("Ответьте (reply) этой командой на сообщение нового владельца: /transfer <game_player_id>")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.reply("Формат: ответом на сообщение нового владельца — /transfer P2")
        return
    pid = parts[1].strip().upper()
    player = game.players.get(pid)
    if player is None:
        await message.reply("Такого игрового id нет.")
        return

    new_user = message.reply_to_message.from_user
    player.telegram_id = new_user.id
    manager.save(game)
    await message.reply(f"Партия {pid} ({player.name}) теперь под управлением {new_user.full_name}.")


@router.message(Command("reload_state"))
async def cmd_reload(message: Message) -> None:
    game = manager.get(message.chat.id)
    if game is None or message.from_user is None:
        return
    if message.from_user.id != game.admin_telegram_id:
        await message.reply("Только админ партии может перечитать состояние.")
        return
    reloaded = manager.reload(message.chat.id)
    if reloaded is None:
        await message.reply("Активной партии нет.")
        return
    await message.reply("Состояние перечитано из Excel-файла партии.")


@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    game = manager.get(message.chat.id)
    if game is None or message.from_user is None:
        await message.reply("Активной партии нет.")
        return
    if message.from_user.id != game.admin_telegram_id:
        await message.reply("Только админ партии может поставить партию на паузу.")
        return
    if game.phase is not GamePhase.IN_PROGRESS:
        await message.reply("Паузу можно поставить только во время игры (не во время настройки/сбора игроков).")
        return
    blocker = flow.blocking_action_reason(game)
    if blocker is not None:
        await message.reply(f"Дождитесь завершения текущего действия ({blocker}), затем поставьте на паузу.")
        return
    await flow.pause_game(message.bot, game)


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    if message.from_user is None:
        return
    if manager.get(message.chat.id) is not None:
        await message.reply("В этой группе уже есть активная партия.")
        return
    entry = storage.get_registry_entry(message.chat.id)
    if entry is None or entry.status != storage.STATUS_PAUSED:
        await message.reply("Партии на паузе нет.")
        return
    if message.from_user.id != entry.admin_telegram_id:
        await message.reply("Только админ партии может продолжить.")
        return
    await flow.resume_game(message.bot, message.chat.id)
