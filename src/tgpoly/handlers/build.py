"""`/build` — постройка/продажа домов, +/- по улице (спец. п.1, п.11)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from .. import flow, keyboards
from ..state import manager

router = Router(name="build")


@router.message(Command("build"))
async def cmd_build(message: Message) -> None:
    game = manager.get(message.chat.id)
    if game is None or message.from_user is None:
        return
    player = game.player_by_telegram_id(message.from_user.id)
    if player is None:
        await message.reply("Вы не участвуете в этой партии.")
        return
    await message.reply("Постройка домов:", reply_markup=keyboards.build_keyboard(game, player))


async def _refresh(callback: CallbackQuery, game, player) -> None:
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboards.build_keyboard(game, player))
    except Exception:
        pass


@router.callback_query(F.data.startswith("build_plus:"))
async def on_plus(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    player = game.player_by_telegram_id(callback.from_user.id)
    if player is None:
        await callback.answer()
        return
    cell_index = int(callback.data.split(":", 1)[1])
    await flow.build_house(callback.bot, game, player, cell_index)
    await callback.answer()
    await _refresh(callback, game, player)


@router.callback_query(F.data.startswith("build_minus:"))
async def on_minus(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    player = game.player_by_telegram_id(callback.from_user.id)
    if player is None:
        await callback.answer()
        return
    cell_index = int(callback.data.split(":", 1)[1])
    await flow.sell_house(callback.bot, game, player, cell_index)
    await callback.answer()
    await _refresh(callback, game, player)
