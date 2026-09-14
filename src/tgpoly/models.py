"""In-memory domain model for one partия (spec п.10: Настройки / Игроки / Собственность)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


# ---------------------------------------------------------------------------
# Settings (лист "Настройки", п.10) — 9 keys, matches /newgame reply shortcut (п.13)
# ---------------------------------------------------------------------------

SETTINGS_INT_FIELDS = ("start_money", "auction_close_delay_sec", "max_players")
SETTINGS_BOOL_FIELDS = ("auction_enabled",)
SETTINGS_TIMEOUT_FIELDS = ("debtor_timeout_min", "landlord_timeout_min", "turn_timeout_min")
SETTINGS_VOTE_DELAY_FIELDS = ("debtor_vote_delay_min", "landlord_vote_delay_min", "turn_vote_delay_min")

ALL_SETTINGS_KEYS = (
    SETTINGS_INT_FIELDS + SETTINGS_BOOL_FIELDS + SETTINGS_TIMEOUT_FIELDS + SETTINGS_VOTE_DELAY_FIELDS
)


@dataclass
class Settings:
    start_money: int = 1500
    auction_enabled: bool = True
    auction_close_delay_sec: int = 10
    max_players: int = 8  # минимум фиксирован — 2 (п.13), максимум настраивается
    # None == "inf" (ждать бесконечно, п.6)
    debtor_timeout_min: int | None = 5
    landlord_timeout_min: int | None = 5
    turn_timeout_min: int | None = 5
    # None == "off" (голосование выключено, п.6)
    debtor_vote_delay_min: int | None = 2
    landlord_vote_delay_min: int | None = 2
    turn_vote_delay_min: int | None = 2

    def get(self, key: str) -> Any:
        return getattr(self, key)

    def set_raw(self, key: str, raw_value: str) -> None:
        """Parse and apply one `имя_настройки значение` line (п.13 step 3)."""
        if key not in ALL_SETTINGS_KEYS:
            raise KeyError(key)
        if key in SETTINGS_BOOL_FIELDS:
            setattr(self, key, raw_value.strip().upper() in ("TRUE", "ДА", "1", "ON"))
        elif key in SETTINGS_TIMEOUT_FIELDS:
            setattr(self, key, None if raw_value.strip().lower() == "inf" else int(raw_value))
        elif key in SETTINGS_VOTE_DELAY_FIELDS:
            setattr(self, key, None if raw_value.strip().lower() == "off" else int(raw_value))
        else:
            setattr(self, key, int(raw_value))

    def display_value(self, key: str) -> str:
        value = self.get(key)
        if key in SETTINGS_TIMEOUT_FIELDS:
            return "inf" if value is None else str(value)
        if key in SETTINGS_VOTE_DELAY_FIELDS:
            return "off" if value is None else str(value)
        if key in SETTINGS_BOOL_FIELDS:
            return "TRUE" if value else "FALSE"
        return str(value)


# ---------------------------------------------------------------------------
# Игроки
# ---------------------------------------------------------------------------

class PlayerStatus(Enum):
    ACTIVE = "Активен"
    BANKRUPT = "Банкрот"


@dataclass
class Player:
    game_player_id: str  # P1..P8, устойчивый ключ (п.8, п.10)
    telegram_id: int
    name: str
    money: int
    position: int = 0
    status: PlayerStatus = PlayerStatus.ACTIVE
    is_admin: bool = False

    @property
    def is_active(self) -> bool:
        return self.status is PlayerStatus.ACTIVE


# ---------------------------------------------------------------------------
# Собственность
# ---------------------------------------------------------------------------

@dataclass
class PropertyState:
    cell_index: int
    owner: str | None = None  # game_player_id, None = Банк
    mortgaged: bool = False
    development: int = 0  # 0 пусто, 1-4 дома, 5 отель


# ---------------------------------------------------------------------------
# Runtime-only state (не сохраняется в Excel)
# ---------------------------------------------------------------------------

class GamePhase(Enum):
    SETUP = auto()       # визард настроек, п.13 шаг 2-3
    COLLECTING = auto()  # сбор игроков через Poll, п.13 шаг 4
    ORDERING = auto()    # определение очерёдности хода броском кости
    IN_PROGRESS = auto()
    FINISHED = auto()


@dataclass
class DiceWait:
    expected_player_id: str
    first_value: int | None = None
    first_message_id: int | None = None


@dataclass
class OrderState:
    """Определение очерёдности хода: все кидают по одной кости, при равенстве —
    перебрасывают локально только те, кто совпал (см. обсуждение в чате)."""
    resolved: list[str] = field(default_factory=list)  # уже определённый порядок (сверху вниз)
    queue: list[list[str]] = field(default_factory=list)  # оставшиеся группы, первая — та, что сейчас кидает
    rolls: dict[str, int] = field(default_factory=dict)  # броски текущей группы в этом раунде
    final_rolls: dict[str, int] = field(default_factory=dict)  # чем именно каждый игрок определил своё место
    message_ids: list[int] = field(default_factory=list)  # всё, что нужно подчистить в конце (п.14)


@dataclass
class AuctionState:
    cell_index: int
    best_bid: int = 0
    best_bidder: str | None = None
    message_ids: set[int] = field(default_factory=set)
    announce_message_id: int | None = None
    timer_task: Any = None
    declined: set[str] = field(default_factory=set)  # game_player_id тех, кто нажал «Отказаться»
    decline_prompt_message_id: int | None = None  # сообщение, на котором сейчас висит кнопка


@dataclass
class TradeOffer:
    initiator_id: str
    target_id: str
    initiator_money: int = 0
    target_money: int = 0
    initiator_properties: set[int] = field(default_factory=set)
    target_properties: set[int] = field(default_factory=set)
    initiator_confirmed: bool = False
    target_confirmed: bool = False
    message_id: int | None = None
    money_picker_message_ids: dict[str, int] = field(default_factory=dict)  # "initiator"/"target" -> message_id
    property_picker_message_ids: dict[str, int] = field(default_factory=dict)  # "initiator"/"target" -> message_id


@dataclass
class JoinPoll:
    poll_id: str
    message_id: int
    yes_voters: list[int] = field(default_factory=list)
    start_button_message_id: int | None = None


@dataclass
class PendingDebt:
    debtor_id: str
    amount: int
    creditor_id: str | None  # None = банк (налог)
    reason: str  # "rent" | "tax"


@dataclass
class GameState:
    chat_id: int
    game_id: str
    group_title: str
    file_path: str
    settings: Settings = field(default_factory=Settings)
    players: dict[str, Player] = field(default_factory=dict)
    player_order: list[str] = field(default_factory=list)  # порядок хода — определяется броском (см. OrderState)
    properties: dict[int, PropertyState] = field(default_factory=dict)
    phase: GamePhase = GamePhase.SETUP
    turn_index: int = 0
    admin_telegram_id: int | None = None
    order: "OrderState | None" = None

    # runtime-only, не персистится
    dice_wait: DiceWait | None = None
    pending_double: bool = False
    pending_purchase: int | None = None  # cell_index ожидающего решения "купить/отказаться"
    pinned_turn_message_id: int | None = None  # закреплённое сообщение "Ход игрока X"
    auction: AuctionState | None = None
    trade: TradeOffer | None = None
    join_poll: JoinPoll | None = None
    pending_debt: PendingDebt | None = None
    active_window: Any = None  # timeouts.Window
    last_board_render_message_id: int | None = None
    transient_message_ids: dict[str, int] = field(default_factory=dict)
    settings_wizard_message_id: int | None = None

    def active_players(self) -> list[Player]:
        return [self.players[pid] for pid in self.player_order if self.players[pid].is_active]

    def current_player(self) -> Player | None:
        """turn_index — позиция в НЕИЗМЕННОМ player_order (не в отфильтрованном active_players()),
        так что банкротство любого другого игрока не сбивает индекс текущего (см. advance_turn)."""
        if not self.player_order:
            return None
        pid = self.player_order[self.turn_index % len(self.player_order)]
        return self.players.get(pid)

    def player_by_telegram_id(self, telegram_id: int) -> Player | None:
        for p in self.players.values():
            if p.telegram_id == telegram_id:
                return p
        return None

    def next_game_player_id(self) -> str:
        n = 1
        while f"P{n}" in self.players:
            n += 1
        return f"P{n}"

    def total_game_value(self) -> int:
        """Кэш всех игроков + номинал участков + полная стоимость построек по цене постройки (п.8)."""
        from . import board as board_module

        total = sum(p.money for p in self.players.values())
        for prop in self.properties.values():
            if prop.owner is None:
                continue
            c = board_module.cell(prop.cell_index)
            total += c.price or 0
            if prop.development and c.type is board_module.CellType.STREET:
                total += board_module.house_cost(c) * min(prop.development, 4)
        return total
