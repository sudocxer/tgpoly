"""Команды и кнопки визарда `/newgame` (спец. п.13). Reply-обработка настроек — в replies.py,
чтобы не конфликтовать с другими reply-механиками (аукцион, сделка) на одном фильтре."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, PollAnswer

from .. import flow, hygiene, keyboards, storage, wizard
from ..state import manager

router = Router(name="newgame")


@router.message(Command("newgame"))
async def cmd_newgame(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup"):
        await message.reply("Партия создаётся внутри группы.")
        return
    if manager.get(message.chat.id) is not None:
        await message.reply("В этой группе уже есть активная партия.")
        return
    if message.from_user is None:
        return

    entry = storage.get_registry_entry(message.chat.id)
    if entry is not None and entry.status == storage.STATUS_PAUSED:
        await message.reply(
            "В этой группе есть партия на паузе. Продолжить её, или начать новую?",
            reply_markup=keyboards.resume_or_new_keyboard(),
        )
        return

    await wizard.start_wizard(message.bot, message.chat.id, message.chat.title or str(message.chat.id), message.from_user.id)


@router.callback_query(F.data == "newgame_resume_old")
async def on_resume_old(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        return
    chat_id = callback.message.chat.id
    if manager.get(chat_id) is not None:
        await callback.answer("В этой группе уже есть активная партия.", show_alert=True)
        return
    entry = storage.get_registry_entry(chat_id)
    if entry is None or entry.status != storage.STATUS_PAUSED:
        await callback.answer("Партия на паузе не найдена.", show_alert=True)
        return
    if callback.from_user.id != entry.admin_telegram_id:
        await callback.answer("Только админ партии может продолжить.", show_alert=True)
        return
    await callback.answer()
    await hygiene.delete_stray(callback.bot, chat_id, callback.message.message_id)
    await flow.resume_game(callback.bot, chat_id)


@router.callback_query(F.data == "newgame_start_new")
async def on_start_new(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None:
        return
    chat_id = callback.message.chat.id
    if manager.get(chat_id) is not None:
        await callback.answer("В этой группе уже есть активная партия.", show_alert=True)
        return
    await callback.answer()
    await hygiene.delete_stray(callback.bot, chat_id, callback.message.message_id)
    await wizard.start_wizard(
        callback.bot, chat_id, callback.message.chat.title or str(chat_id), callback.from_user.id,
    )


@router.callback_query(F.data == "newgame_continue")
async def on_continue(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None or callback.from_user.id != game.admin_telegram_id:
        await callback.answer("Только админ партии может продолжить.", show_alert=True)
        return
    await callback.answer()
    await wizard.continue_to_join(callback.bot, game)


@router.callback_query(F.data == "newgame_start")
async def on_start_button(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    game = manager.get(callback.message.chat.id)
    if game is None:
        await callback.answer()
        return
    started = await wizard.try_early_start(callback.bot, game, callback.from_user.id)
    if started:
        await callback.answer("Партия запущена!")
    else:
        await callback.answer("Только админ партии может запустить старт досрочно.", show_alert=True)


@router.poll_answer()
async def on_poll_answer(poll_answer: PollAnswer) -> None:
    for game in list(manager.games.values()):
        if game.join_poll and game.join_poll.poll_id == poll_answer.poll_id:
            await wizard.handle_poll_answer(
                poll_answer.bot, game, poll_answer.poll_id, poll_answer.user.id, poll_answer.option_ids,
            )
            return
