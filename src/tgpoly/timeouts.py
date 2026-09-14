"""Общая механика таймаутов и голосования за пропуск (спец. п.6).

Один Window обслуживает ровно одно окно ожидания: окно должника, окно арендодателя,
или ожидание броска костей на обычном ходу. `eligible_voter_ids` — активные игроки,
исключая самого бездействующего (простое большинство от них решает голосование).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# chat_id -> активное окно (в партии одновременно может быть открыто только одно)
ACTIVE_WINDOWS: dict[int, "Window"] = {}


@dataclass
class Window:
    bot: Bot
    chat_id: int
    label: str
    timeout_min: int | None  # None = "ждать бесконечно"
    vote_delay_min: int | None  # None = голосование выключено
    eligible_voter_ids: set[int]
    on_resolve: Callable[[str], Awaitable[None]]

    voters: set[int] = field(default_factory=set, init=False)
    vote_message_id: int | None = field(default=None, init=False)
    _timeout_task: asyncio.Task | None = field(default=None, init=False)
    _vote_task: asyncio.Task | None = field(default=None, init=False)
    _resolved: bool = field(default=False, init=False)

    async def start(self) -> None:
        ACTIVE_WINDOWS[self.chat_id] = self
        if self.timeout_min is not None:
            self._timeout_task = asyncio.create_task(self._run_timeout())
        if self.vote_delay_min is not None and self.eligible_voter_ids:
            self._vote_task = asyncio.create_task(self._run_vote_delay())

    async def _run_timeout(self) -> None:
        try:
            await asyncio.sleep(self.timeout_min * 60)
        except asyncio.CancelledError:
            return
        await self._resolve("timeout")

    async def _run_vote_delay(self) -> None:
        try:
            await asyncio.sleep(self.vote_delay_min * 60)
        except asyncio.CancelledError:
            return
        await self._post_vote_button()

    def _needed_votes(self) -> int:
        return len(self.eligible_voter_ids) // 2 + 1

    def _vote_keyboard(self, votes: int) -> InlineKeyboardMarkup:
        needed = self._needed_votes()
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"Пропустить ({votes}/{needed})", callback_data=f"vote_skip:{self.chat_id}")
        ]])

    async def _post_vote_button(self) -> None:
        msg = await self.bot.send_message(
            self.chat_id,
            f"{self.label}\nГолосование за досрочный пропуск открыто.",
            reply_markup=self._vote_keyboard(0),
        )
        self.vote_message_id = msg.message_id

    async def register_vote(self, telegram_id: int) -> bool:
        """True, если этот голос набрал большинство и окно только что закрылось."""
        if self._resolved or telegram_id not in self.eligible_voter_ids or telegram_id in self.voters:
            return False
        self.voters.add(telegram_id)
        if self.vote_message_id is not None:
            try:
                await self.bot.edit_message_reply_markup(
                    chat_id=self.chat_id, message_id=self.vote_message_id,
                    reply_markup=self._vote_keyboard(len(self.voters)),
                )
            except Exception:
                logger.debug("vote keyboard edit failed", exc_info=True)
        if len(self.voters) >= self._needed_votes():
            await self._resolve("vote")
            return True
        return False

    async def cancel(self) -> None:
        """Окно закрыто досрочно вне таймаута/голосования (например, игрок успел бросить кости)."""
        if self._resolved:
            return
        self._resolved = True
        self._cancel_tasks()
        ACTIVE_WINDOWS.pop(self.chat_id, None)
        await self._delete_vote_message()

    def _cancel_tasks(self) -> None:
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        if self._vote_task and not self._vote_task.done():
            self._vote_task.cancel()

    async def _delete_vote_message(self) -> None:
        """Кнопка голосования свою роль отыграла — убираем её целиком, а не оставляем мёртвой (п.14)."""
        if self.vote_message_id is None:
            return
        try:
            await self.bot.delete_message(self.chat_id, self.vote_message_id)
        except Exception:
            logger.debug("failed to delete vote message %s in chat %s", self.vote_message_id, self.chat_id, exc_info=True)
        self.vote_message_id = None

    async def _resolve(self, reason: str) -> None:
        if self._resolved:
            return
        self._resolved = True
        self._cancel_tasks()
        ACTIVE_WINDOWS.pop(self.chat_id, None)
        await self._delete_vote_message()
        await self.on_resolve(reason)
