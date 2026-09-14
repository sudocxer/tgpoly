"""Static board layout (spec п.4). Classic 40-cell Monopoly positions, Russian names.

Rent tables and utility rent base are a placeholder formula, not official Hasbro numbers
(the spec never fixed exact figures) — swap BASE_RENT_MULT / UTILITY_AVG_DICE below if a
real rent table is supplied later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class CellType(Enum):
    GO = auto()
    STREET = auto()
    RAILROAD = auto()
    UTILITY = auto()
    CHANCE = auto()
    CHEST = auto()
    TAX = auto()
    JAIL = auto()
    FREE_PARKING = auto()
    GO_TO_JAIL = auto()


PASS_GO_BONUS = 200
BOARD_SIZE = 40

# base, 1 house, 2 houses, 3 houses, 4 houses, hotel
_RENT_STEPS = (1, 5, 15, 30, 45, 60)


def _rent_table(price: int) -> tuple[int, ...]:
    base = max(2, round(price / 10))
    return tuple(base * step for step in _RENT_STEPS)


@dataclass(frozen=True)
class Cell:
    index: int
    name: str
    type: CellType
    group: str | None = None
    price: int | None = None
    rent_table: tuple[int, ...] = field(default_factory=tuple)

    @property
    def is_purchasable(self) -> bool:
        return self.type in (CellType.STREET, CellType.RAILROAD, CellType.UTILITY)


def _street(index: int, name: str, group: str, price: int) -> Cell:
    return Cell(index, name, CellType.STREET, group, price, _rent_table(price))


def _railroad(index: int, name: str) -> Cell:
    return Cell(index, name, CellType.RAILROAD, "Вокзал", 200)


def _utility(index: int, name: str) -> Cell:
    return Cell(index, name, CellType.UTILITY, "Коммуналка", 150)


BOARD: tuple[Cell, ...] = (
    Cell(0, "Старт", CellType.GO),
    _street(1, "Старый Арбат", "Фиолетовая", 60),
    Cell(2, "Общественная казна", CellType.CHEST),
    _street(3, "Балтийская", "Фиолетовая", 60),
    Cell(4, "Подоходный налог", CellType.TAX, price=200),
    _railroad(5, "Вокзал Рижский"),
    _street(6, "Тверская", "Голубая", 100),
    Cell(7, "Шанс", CellType.CHANCE),
    _street(8, "Новослободская", "Голубая", 100),
    _street(9, "Сухаревская", "Голубая", 120),
    Cell(10, "Тюрьма / Просто в гостях", CellType.JAIL),
    _street(11, "Кузнецкий мост", "Розовая", 140),
    _utility(12, "Электрическая компания"),
    _street(13, "Новый Арбат", "Розовая", 140),
    _street(14, "Петровка", "Розовая", 160),
    _railroad(15, "Вокзал Курский"),
    _street(16, "Мясницкая", "Оранжевая", 180),
    Cell(17, "Общественная казна", CellType.CHEST),
    _street(18, "Покровка", "Оранжевая", 180),
    _street(19, "Маросейка", "Оранжевая", 200),
    Cell(20, "Бесплатная парковка", CellType.FREE_PARKING),
    _street(21, "Ленинский проспект", "Красная", 220),
    Cell(22, "Шанс", CellType.CHANCE),
    _street(23, "Профсоюзная", "Красная", 220),
    _street(24, "Кутузовский проспект", "Красная", 240),
    _railroad(25, "Вокзал Казанский"),
    _street(26, "Ленинградское шоссе", "Жёлтая", 260),
    _street(27, "Волгоградский проспект", "Жёлтая", 260),
    _utility(28, "Водопроводная станция"),
    _street(29, "Варшавское шоссе", "Жёлтая", 280),
    Cell(30, "Отправляйтесь в тюрьму", CellType.GO_TO_JAIL),
    _street(31, "Осенний бульвар", "Зелёная", 300),
    _street(32, "Мичуринский проспект", "Зелёная", 300),
    Cell(33, "Общественная казна", CellType.CHEST),
    _street(34, "Ленинские горы", "Зелёная", 320),
    _railroad(35, "Вокзал Белорусский"),
    Cell(36, "Шанс", CellType.CHANCE),
    _street(37, "Рублёвское шоссе", "Тёмно-синяя", 350),
    Cell(38, "Налог на роскошь", CellType.TAX, price=100),
    _street(39, "Тверская застава", "Тёмно-синяя", 400),
)

assert len(BOARD) == BOARD_SIZE

CELLS_BY_GROUP: dict[str, tuple[Cell, ...]] = {}
for _cell in BOARD:
    if _cell.type is CellType.STREET:
        CELLS_BY_GROUP.setdefault(_cell.group, ())
        CELLS_BY_GROUP[_cell.group] = CELLS_BY_GROUP[_cell.group] + (_cell,)

RAILROADS: tuple[Cell, ...] = tuple(c for c in BOARD if c.type is CellType.RAILROAD)
UTILITIES: tuple[Cell, ...] = tuple(c for c in BOARD if c.type is CellType.UTILITY)

JAIL_INDEX = 10
GO_TO_JAIL_INDEX = 30

RAILROAD_RENT = (25, 50, 100, 200)
UTILITY_MULTIPLIER = {1: 4, 2: 10}
UTILITY_AVG_DICE = 7  # spec п.1: dice-based utility rent replaced by a fixed multiplier


def cell(index: int) -> Cell:
    return BOARD[index % BOARD_SIZE]


def group_size(group: str) -> int:
    return len(CELLS_BY_GROUP.get(group, ()))


def house_cost(c: Cell) -> int:
    """Стоимость постройки одного дома. Формула-заглушка (точных цифр в спеке нет)."""
    return max(10, round(c.price / 2 / 10) * 10) if c.price else 0


def house_sell_price(c: Cell) -> int:
    """Продажа дома банку — половина стоимости постройки (п.1, п.5)."""
    return house_cost(c) // 2


def mortgage_value(c: Cell) -> int:
    """Сумма под залог = ровно номинал/2, возвращается без наценки при снятии (п.1)."""
    return (c.price or 0) // 2
