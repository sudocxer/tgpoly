"""Терминальный чат от имени бота: читаем входящие сообщения и пишем в выбранный чат/группу."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatMemberUpdated, Message, User
from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from .store import ChatInfo, ChatStore

DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "chats.json"

HELP = """\
Команды:
  /chats                      список чатов и групп
  /enter <номер|id|@username> войти в чат
  /exit                       выйти из чата в список
  /quit                       закрыть программу
В чате:
  текст                       отправить сообщение
  /reply <#сообщения> текст   ответить на сообщение   (коротко: /r)
  /mention <id|@user> текст   упомянуть пользователя   (коротко: /m)"""

MEDIA = {
    "photo": "фото", "video": "видео", "voice": "голосовое", "video_note": "кружок", "audio": "аудио",
    "document": "файл", "sticker": "стикер", "animation": "gif", "location": "геопозиция",
    "contact": "контакт", "poll": "опрос", "dice": "кубик",
}


def user_name(user: User) -> str:
    return user.full_name or user.username or str(user.id)


def chat_title(chat) -> str:
    if chat.type == "private":
        return chat.full_name or chat.username or str(chat.id)
    return chat.title or str(chat.id)


class Console:
    def __init__(self, bot: Bot, store: ChatStore) -> None:
        self.bot = bot
        self.store = store
        self.me: User | None = None
        self.current: ChatInfo | None = None

    # ---------- вывод ----------

    def bot_label(self) -> str:
        return f"{user_name(self.me)} ({self.me.id})"

    def describe_actions(self, msg: Message) -> list[str]:
        actions: list[str] = []
        if msg.reply_to_message:
            r = msg.reply_to_message
            who = user_name(r.from_user) if r.from_user else chat_title(r.chat)
            actions.append(f"ответ на #{r.message_id} {who}")
        if msg.forward_origin:
            actions.append("пересылка")
        for ent in msg.entities or msg.caption_entities or []:
            if ent.type == "text_mention" and ent.user:
                actions.append(f"упоминание {user_name(ent.user)} ({ent.user.id})")
            elif ent.type == "mention":
                actions.append("упоминание " + ent.extract_from(msg.text or msg.caption or ""))
        for attr, label in MEDIA.items():
            if getattr(msg, attr, None):
                actions.append(label)
        if msg.new_chat_members:
            actions.append("добавил(а): " + ", ".join(user_name(u) for u in msg.new_chat_members))
        if msg.left_chat_member:
            actions.append("вышел(ла): " + user_name(msg.left_chat_member))
        return actions

    def format_message(self, msg: Message, extra: list[str] | None = None) -> str:
        if msg.from_user and self.me and msg.from_user.id == self.me.id:
            sender = self.bot_label()
        elif msg.from_user:
            sender = f"{user_name(msg.from_user)} ({msg.from_user.id})"
        else:
            sender = f"{chat_title(msg.sender_chat or msg.chat)} ({(msg.sender_chat or msg.chat).id})"
        actions = self.describe_actions(msg)
        if msg.edit_date:
            actions.append("изменено")
        for a in extra or []:
            if a not in actions:
                actions.append(a)
        act = f" [{'; '.join(actions)}]" if actions else ""
        text = msg.text or msg.caption or ""
        if msg.from_user and self.me and msg.from_user.id == self.me.id:
            return f"#{msg.message_id} {sender}:{act} \"{text}\""
        return f"#{msg.message_id} {sender}{act}: {text}"

    def show(self, info: ChatInfo, line: str) -> None:
        info.history.append(line)
        if self.current is info:
            print(line)
        else:
            info.unread += 1
            if self.current is None:
                print(f"  (новое в [{info.title}]) {line}")

    # ---------- входящие ----------

    async def on_message(self, msg: Message) -> None:
        info = self.store.upsert_chat(msg.chat.id, chat_title(msg.chat), msg.chat.type)
        if msg.from_user:
            self.store.remember_user(msg.from_user.id, user_name(msg.from_user), msg.from_user.username)
        self.show(info, self.format_message(msg))

    async def on_my_member(self, event: ChatMemberUpdated) -> None:
        info = self.store.upsert_chat(event.chat.id, chat_title(event.chat), event.chat.type)
        status = event.new_chat_member.status
        text = "бот добавлен в чат" if status in ("member", "administrator") else f"статус бота: {status}"
        self.show(info, f"* {text}")

    # ---------- команды ----------

    def print_chats(self) -> None:
        chats = self.store.ordered()
        if not chats:
            print("Чатов пока нет. Напишите боту в лс или добавьте его в группу — чат появится здесь.")
            print("Или войдите по id: /enter -1001234567890")
            return
        print("Чаты:")
        for i, c in enumerate(chats, 1):
            unread = f"  +{c.unread} новых" if c.unread else ""
            print(f"  {i}. [{c.kind}] {c.title} ({c.id}){unread}")
        print("Войти: /enter <номер>")

    async def resolve_chat(self, arg: str) -> ChatInfo | None:
        chats = self.store.ordered()
        if arg.isdigit() and 1 <= int(arg) <= len(chats):
            return chats[int(arg) - 1]
        target: int | str = int(arg) if arg.lstrip("-").isdigit() else arg
        if isinstance(target, int) and target in self.store.chats:
            return self.store.chats[target]
        if isinstance(target, str) and not target.startswith("@"):
            target = "@" + target
        try:
            chat = await self.bot.get_chat(target)
        except TelegramAPIError as e:
            print(f"Чат не найден: {e.message}")
            return None
        return self.store.upsert_chat(chat.id, chat_title(chat), chat.type)

    async def enter(self, arg: str) -> None:
        if not arg:
            print("Использование: /enter <номер|id|@username>")
            return
        info = await self.resolve_chat(arg)
        if info is None:
            return
        self.current = info
        info.unread = 0
        print(f"── {info.title} ({info.id}) ── /exit — назад к списку")
        for line in info.history:
            print(line)

    def resolve_user(self, arg: str) -> tuple[int, str] | None:
        if arg.lstrip("-").isdigit():
            uid = int(arg)
            return uid, self.store.users.get(uid, str(uid))
        uid = self.store.usernames.get(arg.lstrip("@").lower())
        if uid is None:
            print(f"Пользователь {arg} не встречался боту — упомяните по id.")
            return None
        return uid, self.store.users.get(uid, arg)

    async def send(self, text: str, reply_to: int | None = None, mention: tuple[int, str] | None = None) -> None:
        body = html.escape(text)
        if mention:
            uid, name = mention
            body = f'<a href="tg://user?id={uid}">{html.escape(name)}</a> {body}'.rstrip()
        try:
            sent = await self.bot.send_message(
                self.current.id, body, parse_mode="HTML", reply_to_message_id=reply_to,
            )
        except TelegramAPIError as e:
            print(f"Не отправлено: {e.message}")
            return
        line = self.format_message(sent, [f"упоминание {mention[1]} ({mention[0]})"] if mention else None)
        self.current.history.append(line)
        print(line)

    async def handle(self, line: str) -> bool:
        """Возвращает False, когда пора выходить."""
        line = line.strip()
        if not line:
            return True
        cmd, _, rest = line.partition(" ")
        rest = rest.strip()
        cmd = cmd.lower()

        if cmd == "/quit":
            return False
        if cmd == "/help":
            print(HELP)
        elif cmd == "/chats":
            self.print_chats()
        elif cmd == "/enter":
            await self.enter(rest)
        elif cmd == "/exit":
            if self.current:
                print(f"── вышли из {self.current.title} ──")
                self.current = None
            self.print_chats()
        elif self.current is None:
            print("Сначала войдите в чат: /enter <номер>. Список — /chats, справка — /help.")
        elif cmd in ("/reply", "/r"):
            target, _, text = rest.partition(" ")
            if not target.lstrip("#").isdigit() or not text.strip():
                print("Использование: /reply <#сообщения> текст")
            else:
                await self.send(text.strip(), reply_to=int(target.lstrip("#")))
        elif cmd in ("/mention", "/m"):
            target, _, text = rest.partition(" ")
            user = self.resolve_user(target) if target else None
            if not target:
                print("Использование: /mention <id|@username> текст")
            elif user:
                await self.send(text.strip(), mention=user)
        else:
            await self.send(line)
        return True

    async def repl(self) -> None:
        session: PromptSession[str] = PromptSession()
        print(f"Бот: {self.bot_label()}")
        self.print_chats()
        print("Справка — /help")
        while True:
            prompt = f"[{self.current.title}] > " if self.current else "> "
            try:
                line = await session.prompt_async(prompt)
            except (EOFError, KeyboardInterrupt):
                return
            if not await self.handle(line):
                return


async def run() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("Переменная окружения BOT_TOKEN не задана (см. .env.example).")

    bot = Bot(token=token)
    console = Console(bot, ChatStore(DATA_FILE))
    dp = Dispatcher()
    dp.message.register(console.on_message)
    dp.edited_message.register(console.on_message)
    dp.channel_post.register(console.on_message)
    dp.my_chat_member.register(console.on_my_member)

    try:
        console.me = await bot.get_me()
        with patch_stdout():
            polling = asyncio.create_task(
                dp.start_polling(bot, handle_signals=False, allowed_updates=dp.resolve_used_update_types())
            )
            try:
                await console.repl()
            finally:
                polling.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await polling
    finally:
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
