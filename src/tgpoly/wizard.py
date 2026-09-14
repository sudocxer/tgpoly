"""Визард `/newgame` (спец. п.13): подтверждение настроек → сбор игроков через Poll → старт."""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import Message

from . import keyboards
from .flow import log, render_and_send_board, start_ordering
from .models import ALL_SETTINGS_KEYS, GamePhase, GameState, JoinPoll, Player
from .state import manager

MIN_PLAYERS = 2  # фиксировано; максимум — настройка партии max_players (п.10, п.13)


def _max_players(game: GameState) -> int:
    return max(MIN_PLAYERS, game.settings.max_players)


def format_settings_message(game: GameState) -> str:
    lines = ["⚙️ Настройки партии (по умолчанию, из default.xlsx):", ""]
    for key in ALL_SETTINGS_KEYS:
        lines.append(f"{key} = {game.settings.display_value(key)}")
    lines.append("")
    lines.append(
        "Чтобы поменять значение — ответьте (reply) на это сообщение строками вида "
        "«имя_настройки значение» (можно несколько строк за раз)."
    )
    lines.append("Когда готовы — нажмите «Продолжить».")
    return "\n".join(lines)


async def start_wizard(bot: Bot, chat_id: int, group_title: str, admin_telegram_id: int) -> GameState:
    game = manager.start_new(chat_id, group_title, admin_telegram_id)
    msg = await bot.send_message(chat_id, format_settings_message(game), reply_markup=keyboards.newgame_continue_keyboard())
    game.settings_wizard_message_id = msg.message_id
    manager.save(game)
    return game


async def handle_settings_reply(bot: Bot, game: GameState, message: Message) -> bool:
    if game.phase is not GamePhase.SETUP or game.settings_wizard_message_id is None:
        return False
    if message.reply_to_message is None or message.reply_to_message.message_id != game.settings_wizard_message_id:
        return False
    if message.from_user is None or message.from_user.id != game.admin_telegram_id:
        return False
    if not message.text:
        return False

    changed = False
    for line in message.text.strip().splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        key, raw_value = parts
        if key not in ALL_SETTINGS_KEYS:
            continue
        try:
            game.settings.set_raw(key, raw_value)
            changed = True
        except (ValueError, KeyError):
            continue

    if changed:
        manager.save(game)
        await bot.edit_message_text(
            text=format_settings_message(game), chat_id=game.chat_id, message_id=game.settings_wizard_message_id,
            reply_markup=keyboards.newgame_continue_keyboard(),
        )
    return changed


async def continue_to_join(bot: Bot, game: GameState) -> None:
    if game.phase is not GamePhase.SETUP:
        return
    game.phase = GamePhase.COLLECTING
    manager.save(game)
    poll_msg = await bot.send_poll(
        game.chat_id,
        question=f"Кто играет? (минимум {MIN_PLAYERS}, максимум {_max_players(game)})",
        options=["Да", "Нет"],
        is_anonymous=False,
    )
    game.join_poll = JoinPoll(poll_id=poll_msg.poll.id, message_id=poll_msg.message_id)
    manager.save(game)


async def handle_poll_answer(bot: Bot, game: GameState, poll_id: str, user_id: int, option_ids: list[int]) -> None:
    jp = game.join_poll
    if jp is None or jp.poll_id != poll_id or game.phase is not GamePhase.COLLECTING:
        return

    voted_yes = 0 in option_ids
    if voted_yes and user_id not in jp.yes_voters:
        jp.yes_voters.append(user_id)
    elif not voted_yes and user_id in jp.yes_voters:
        jp.yes_voters.remove(user_id)
    manager.save(game)

    if len(jp.yes_voters) >= _max_players(game):
        await finalize_players(bot, game)
    elif len(jp.yes_voters) >= MIN_PLAYERS and jp.start_button_message_id is None:
        msg = await bot.send_message(
            game.chat_id,
            "Минимум игроков набран. Админ партии может запустить партию досрочно кнопкой ниже, "
            "либо ответить (reply) на голосование словом «start».",
            reply_markup=keyboards.newgame_start_keyboard(),
        )
        jp.start_button_message_id = msg.message_id
        manager.save(game)


async def try_early_start(bot: Bot, game: GameState, requester_telegram_id: int) -> bool:
    jp = game.join_poll
    if jp is None or game.phase is not GamePhase.COLLECTING:
        return False
    if requester_telegram_id != game.admin_telegram_id:
        return False
    if len(jp.yes_voters) < MIN_PLAYERS:
        return False
    await finalize_players(bot, game)
    return True


async def finalize_players(bot: Bot, game: GameState) -> None:
    jp = game.join_poll
    if jp is None:
        return
    for telegram_id in jp.yes_voters:
        pid = game.next_game_player_id()
        try:
            member = await bot.get_chat_member(game.chat_id, telegram_id)
            name = member.user.full_name
        except Exception:
            name = str(telegram_id)
        game.players[pid] = Player(
            game_player_id=pid,
            telegram_id=telegram_id,
            name=name,
            money=game.settings.start_money,
            position=0,
            is_admin=(telegram_id == game.admin_telegram_id),
        )
        game.player_order.append(pid)

    game.join_poll = None
    manager.save(game)
    names = ", ".join(game.players[p].name for p in game.player_order)
    log(game, f"game started: {names}")
    await bot.send_message(game.chat_id, f"🎲 Партия началась! Игроки: {names}")
    await render_and_send_board(bot, game)
    await start_ordering(bot, game)
