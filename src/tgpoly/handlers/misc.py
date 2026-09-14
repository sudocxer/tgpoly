"""`/me`, `/board` (спец. п.11)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from .. import board, flow, hygiene, keyboards, render
from ..models import GamePhase
from ..state import manager

router = Router(name="misc")


@router.message(Command("me"))
async def cmd_me(message: Message) -> None:
    game = manager.get(message.chat.id)
    if game is None or message.from_user is None:
        return
    player = game.player_by_telegram_id(message.from_user.id)
    if player is None:
        await message.reply("Вы не участвуете в этой партии.")
        return

    owned = []
    for prop in game.properties.values():
        if prop.owner != player.game_player_id:
            continue
        c = board.cell(prop.cell_index)
        extra = []
        if prop.mortgaged:
            extra.append("залог")
        if prop.development:
            extra.append("отель" if prop.development == 5 else f"домов: {prop.development}")
        suffix = f" ({', '.join(extra)})" if extra else ""
        owned.append(f"{prop.cell_index}: {c.name}{suffix}")

    text = (
        f"{player.name} ({player.game_player_id})\n"
        f"Деньги: {player.money}\n"
        f"Позиция: {board.cell(player.position).name}\n"
        f"Статус: {player.status.value}\n"
        f"Собственность:\n" + ("\n".join(owned) if owned else "—")
    )
    await message.reply(text)


@router.message(Command("board"))
async def cmd_board(message: Message) -> None:
    game = manager.get(message.chat.id)
    if game is None:
        await message.reply("Активной партии нет.")
        return
    image_bytes = await render.render_board(message.bot, game)
    await message.answer_photo(BufferedInputFile(image_bytes, filename="board.png"))


@router.message(Command("bankrupt"))
async def cmd_bankrupt(message: Message) -> None:
    game = manager.get(message.chat.id)
    if game is None or message.from_user is None:
        return
    player = game.player_by_telegram_id(message.from_user.id)
    if player is None:
        await message.reply("Вы не участвуете в этой партии.")
        return
    if not player.is_active:
        await message.reply("Вы уже банкрот.")
        return
    if game.phase is not GamePhase.IN_PROGRESS:
        await message.reply("Банкротство можно объявить только во время игры.")
        return
    blocker = flow.blocking_action_reason(game)
    if blocker is not None:
        await message.reply(f"Дождитесь завершения текущего действия ({blocker}), затем попробуйте снова.")
        return
    await message.reply(
        f"{player.name}, точно объявить банкротство? Вся собственность уйдёт банку, деньги обнулятся, "
        f"обратно в эту партию будет не вернуться.",
        reply_markup=keyboards.bankrupt_confirm_keyboard(player.game_player_id),
    )


@router.callback_query(F.data.startswith("bankrupt_confirm:"))
async def on_bankrupt_confirm(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None or callback.data is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    pid = callback.data.split(":", 1)[1]
    player = game.players.get(pid)
    if player is None or callback.from_user.id != player.telegram_id:
        await callback.answer("Это не ваша кнопка.", show_alert=True)
        return
    if not player.is_active:
        await callback.answer("Уже банкрот.", show_alert=True)
        await hygiene.delete_stray(callback.bot, game.chat_id, callback.message.message_id)
        return
    await callback.answer()
    await hygiene.delete_stray(callback.bot, game.chat_id, callback.message.message_id)
    await flow.voluntary_bankrupt(callback.bot, game, player)


@router.callback_query(F.data.startswith("bankrupt_cancel:"))
async def on_bankrupt_cancel(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    await callback.answer("Отменено.")
    await hygiene.delete_stray(callback.bot, callback.message.chat.id, callback.message.message_id)
