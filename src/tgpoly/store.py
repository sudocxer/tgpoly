"""Список известных боту чатов и последние сообщения в них."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

HISTORY_SIZE = 50


@dataclass
class ChatInfo:
    id: int
    title: str
    type: str
    history: deque[str] = field(default_factory=lambda: deque(maxlen=HISTORY_SIZE))
    unread: int = 0

    @property
    def kind(self) -> str:
        return {"private": "лс", "group": "группа", "supergroup": "группа", "channel": "канал"}.get(self.type, self.type)


class ChatStore:
    """Чаты, в которых бот что-то видел. Bot API не умеет отдавать этот список, поэтому копим сами."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.chats: dict[int, ChatInfo] = {}
        # user_id -> имя, чтобы упоминать по id и находить пользователя по @username
        self.users: dict[int, str] = {}
        self.usernames: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for c in data.get("chats", []):
            self.chats[c["id"]] = ChatInfo(c["id"], c["title"], c["type"])
        self.users = {int(k): v for k, v in data.get("users", {}).items()}
        self.usernames = {k: int(v) for k, v in data.get("usernames", {}).items()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "chats": [{"id": c.id, "title": c.title, "type": c.type} for c in self.chats.values()],
            "users": {str(k): v for k, v in self.users.items()},
            "usernames": self.usernames,
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def upsert_chat(self, chat_id: int, title: str, type_: str) -> ChatInfo:
        info = self.chats.get(chat_id)
        if info is None:
            info = self.chats[chat_id] = ChatInfo(chat_id, title, type_)
            self.save()
        elif info.title != title or info.type != type_:
            info.title, info.type = title, type_
            self.save()
        return info

    def remember_user(self, user_id: int, name: str, username: str | None) -> None:
        changed = self.users.get(user_id) != name
        self.users[user_id] = name
        if username and self.usernames.get(username.lower()) != user_id:
            self.usernames[username.lower()] = user_id
            changed = True
        if changed:
            self.save()

    def ordered(self) -> list[ChatInfo]:
        return list(self.chats.values())
