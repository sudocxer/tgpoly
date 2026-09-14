"""Покупка клетки — кнопки «Купить» / «Отказаться» (спец. п.4, п.11)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from .. import flow
from ..state import manager

router = Router(name="purchase")


def _is_current_player(game, callback: CallbackQuery) -> bool:
    current = game.current_player()
    player = game.player_by_telegram_id(callback.from_user.id)
    return player is not None and current is not None and player.game_player_id == current.game_player_id


@router.callback_query(F.data.startswith("buy:"))
async def on_buy(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    cell_index = int(callback.data.split(":", 1)[1])
    if not flow.is_purchase_pending(game, cell_index):
        await callback.answer("Это предложение уже неактуально.", show_alert=True)
        return
    if not _is_current_player(game, callback):
        await callback.answer("Не ваш ход.", show_alert=True)
        return
    player = game.player_by_telegram_id(callback.from_user.id)
    price = flow.board.cell(cell_index).price or 0
    if player.money < price:
        await callback.answer("Недостаточно денег. Можно сначала /mortgage заложить участок.", show_alert=True)
        return
    await callback.answer()
    await flow.buy_property(callback.bot, game, player, cell_index)


@router.callback_query(F.data.startswith("decline:"))
async def on_decline(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    cell_index = int(callback.data.split(":", 1)[1])
    if not flow.is_purchase_pending(game, cell_index):
        await callback.answer("Это предложение уже неактуально.", show_alert=True)
        return
    if not _is_current_player(game, callback):
        await callback.answer("Не ваш ход.", show_alert=True)
        return
    player = game.player_by_telegram_id(callback.from_user.id)
    await callback.answer()
    await flow.decline_purchase(callback.bot, game, player, cell_index)


@router.callback_query(F.data == "auction_pass")
async def on_auction_pass(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None or game.auction is None:
        await callback.answer("Аукцион уже закрыт.", show_alert=True)
        return
    player = game.player_by_telegram_id(callback.from_user.id)
    if player is None:
        await callback.answer()
        return
    if player.game_player_id == game.auction.best_bidder:
        await callback.answer("Вы сейчас лидируете — отказываться не от чего.", show_alert=True)
        return
    await flow.auction_pass(callback.bot, game, player)
    await callback.answer("Вы вышли из торгов.")
