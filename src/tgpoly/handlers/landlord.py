"""Решение арендодателя принять/отклонить имущество должника (спец. п.5)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from .. import flow
from ..state import manager

router = Router(name="landlord")


def _creditor_or_none(game, callback: CallbackQuery):
    creditor = game.player_by_telegram_id(callback.from_user.id)
    if creditor is None or game.pending_debt is None or game.pending_debt.creditor_id != creditor.game_player_id:
        return None
    return creditor


@router.callback_query(F.data.startswith("landlord_accept:"))
async def on_accept(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    creditor = _creditor_or_none(game, callback)
    if creditor is None:
        await callback.answer("Это решение не для вас.", show_alert=True)
        return
    await callback.answer()
    await flow.landlord_accept(callback.bot, game, creditor)


@router.callback_query(F.data.startswith("landlord_decline:"))
async def on_decline(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    creditor = _creditor_or_none(game, callback)
    if creditor is None:
        await callback.answer("Это решение не для вас.", show_alert=True)
        return
    await callback.answer()
    await flow.landlord_decline(callback.bot, game, creditor)
