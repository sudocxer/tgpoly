"""`/swap` — только активный игрок, выбор цели кнопкой, конструктор сделки (спец. п.7)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from .. import flow, keyboards
from ..state import manager

router = Router(name="swap")


@router.message(Command("swap"))
async def cmd_swap(message: Message) -> None:
    game = manager.get(message.chat.id)
    if game is None or message.from_user is None:
        return
    player = game.player_by_telegram_id(message.from_user.id)
    current = game.current_player()
    if player is None or current is None or player.game_player_id != current.game_player_id:
        await message.reply("Обмен может начать только активный игрок, чей сейчас ход (п.7).")
        return
    if game.trade is not None:
        await message.reply("У вас уже есть незавершённая сделка — сначала отмените или завершите её.")
        return
    await message.reply(
        "С кем меняемся?",
        reply_markup=keyboards.player_picker_keyboard(game, player.game_player_id, "swap_target"),
    )


@router.callback_query(F.data.startswith("swap_target:"))
async def on_pick_target(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    initiator = game.player_by_telegram_id(callback.from_user.id)
    current = game.current_player()
    if initiator is None or current is None or initiator.game_player_id != current.game_player_id:
        await callback.answer("Обмен может начать только активный игрок.", show_alert=True)
        return
    target_id = callback.data.split(":", 1)[1]
    target = game.players.get(target_id)
    if target is None or game.trade is not None:
        await callback.answer()
        return
    await callback.answer()
    await flow.start_trade(callback.bot, game, initiator, target)


@router.callback_query(F.data == "trade_confirm")
async def on_confirm(callback: CallbackQuery) -> None:
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
    await callback.answer()
    await flow.trade_confirm(callback.bot, game, player)


@router.callback_query(F.data == "trade_cancel")
async def on_cancel(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    await callback.answer()
    await flow.trade_cancel(callback.bot, game)


def _trade_participant(game, callback: CallbackQuery):
    trade = game.trade
    if trade is None:
        return None
    player = game.player_by_telegram_id(callback.from_user.id)
    if player is None or player.game_player_id not in (trade.initiator_id, trade.target_id):
        return None
    return player


@router.callback_query(F.data == "trade_add_money")
async def on_add_money(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    player = _trade_participant(game, callback)
    if player is None:
        await callback.answer("Это не ваша сделка.", show_alert=True)
        return
    await callback.answer()
    await flow.open_money_picker(callback.bot, game, player)


@router.callback_query(F.data.startswith("trade_money_set:"))
async def on_money_set(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    player = _trade_participant(game, callback)
    if player is None:
        await callback.answer("Это не ваша сделка.", show_alert=True)
        return
    amount = int(callback.data.split(":", 1)[1])
    ok = await flow.set_trade_money(callback.bot, game, player, amount)
    await callback.answer(f"Установлено: {amount}." if ok else "Недостаточно денег.", show_alert=not ok)


@router.callback_query(F.data == "trade_add_property")
async def on_add_property(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    player = _trade_participant(game, callback)
    if player is None:
        await callback.answer("Это не ваша сделка.", show_alert=True)
        return
    await callback.answer()
    await flow.open_property_picker(callback.bot, game, player)


@router.callback_query(F.data.startswith("trade_prop_toggle:"))
async def on_prop_toggle(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    player = _trade_participant(game, callback)
    if player is None:
        await callback.answer("Это не ваша сделка.", show_alert=True)
        return
    cell_index = int(callback.data.split(":", 1)[1])
    await flow.toggle_trade_property(callback.bot, game, player, cell_index)
    await callback.answer()


@router.callback_query(F.data == "trade_prop_confirm")
async def on_prop_confirm(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    player = _trade_participant(game, callback)
    if player is None:
        await callback.answer("Это не ваша сделка.", show_alert=True)
        return
    await callback.answer()
    await flow.confirm_property_picker(callback.bot, game, player)
