"""Броски костей (спец. п.3)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from .. import flow, hygiene
from ..models import GamePhase
from ..state import manager

router = Router(name="dice")


@router.message(F.dice)
async def on_dice(message: Message) -> None:
    game = manager.get(message.chat.id)
    if game is None:
        return
    if game.phase is GamePhase.ORDERING:
        await flow.handle_order_roll(message.bot, game, message)
    elif game.phase is GamePhase.IN_PROGRESS:
        await flow.handle_dice_message(message.bot, game, message)
    else:
        await hygiene.delete_stray(message.bot, message.chat.id, message.message_id)
