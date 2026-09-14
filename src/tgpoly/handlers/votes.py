"""Голосование за досрочный пропуск (спец. п.6)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from ..timeouts import ACTIVE_WINDOWS

router = Router(name="votes")


@router.callback_query(F.data.startswith("vote_skip:"))
async def on_vote(callback: CallbackQuery) -> None:
    chat_id = int(callback.data.split(":", 1)[1])
    window = ACTIVE_WINDOWS.get(chat_id)
    if window is None:
        await callback.answer("Голосование уже закрыто.", show_alert=True)
        return
    if callback.from_user.id not in window.eligible_voter_ids:
        await callback.answer("Вы не можете голосовать в этом окне.", show_alert=True)
        return
    if callback.from_user.id in window.voters:
        await callback.answer("Вы уже проголосовали.")
        return
    await window.register_vote(callback.from_user.id)
    await callback.answer("Голос учтён.")
