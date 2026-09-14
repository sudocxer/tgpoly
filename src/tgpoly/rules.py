"""Игровая математика: рента, ипотека, застройка, банкротство (спец. п.1, п.4, п.5, п.8)."""

from __future__ import annotations

from . import board
from .board import Cell, CellType
from .models import GameState, Player, PropertyState


def owns_full_group(game: GameState, owner_id: str, group: str) -> bool:
    cells = board.CELLS_BY_GROUP.get(group, ())
    if not cells:
        return False
    return all(
        (prop := game.properties.get(c.index)) is not None and prop.owner == owner_id
        for c in cells
    )


def count_owned_by_type(game: GameState, owner_id: str, cell_type: CellType) -> int:
    count = 0
    for prop in game.properties.values():
        if prop.owner != owner_id:
            continue
        if board.cell(prop.cell_index).type is cell_type:
            count += 1
    return count


def calculate_rent(game: GameState, prop: PropertyState) -> int:
    if prop.mortgaged or prop.owner is None:
        return 0
    c = board.cell(prop.cell_index)

    if c.type is CellType.STREET:
        if prop.development == 0:
            base = c.rent_table[0]
            return base * 2 if owns_full_group(game, prop.owner, c.group) else base
        return c.rent_table[prop.development]

    if c.type is CellType.RAILROAD:
        n = count_owned_by_type(game, prop.owner, CellType.RAILROAD)
        return board.RAILROAD_RENT[min(n, 4) - 1]

    if c.type is CellType.UTILITY:
        n = count_owned_by_type(game, prop.owner, CellType.UTILITY)
        mult = board.UTILITY_MULTIPLIER.get(n, board.UTILITY_MULTIPLIER[1])
        return board.UTILITY_AVG_DICE * mult

    return 0


def can_build(game: GameState, prop: PropertyState, c: Cell) -> bool:
    """Строить можно в любом порядке по группе, лишь бы была монополия (п.1)."""
    if prop.mortgaged or prop.development >= 5:
        return False
    return owns_full_group(game, prop.owner, c.group) if prop.owner else False


def can_mortgage(prop: PropertyState) -> bool:
    return not prop.mortgaged and prop.development == 0


def has_any_buildable(game: GameState, player_id: str) -> bool:
    """Есть ли у игрока хоть одна улица, где сейчас реально можно строить (нужна монополия, п.1)."""
    for prop in game.properties.values():
        if prop.owner != player_id:
            continue
        c = board.cell(prop.cell_index)
        if c.type is CellType.STREET and can_build(game, prop, c):
            return True
    return False


def has_any_sellable_house(game: GameState, player_id: str) -> bool:
    return any(
        prop.owner == player_id and prop.development > 0
        for prop in game.properties.values()
    )


def has_any_mortgageable(game: GameState, player_id: str) -> bool:
    return any(
        prop.owner == player_id and can_mortgage(prop)
        for prop in game.properties.values()
    )


def new_player_money_cap(game: GameState) -> int:
    """Не больше 50% от общей суммы в игре на момент добавления (п.8)."""
    return game.total_game_value() // 2


def unmortgaged_land_value(game: GameState, player_id: str) -> int:
    total = 0
    for prop in game.properties.values():
        if prop.owner == player_id and not prop.mortgaged:
            total += board.cell(prop.cell_index).price or 0
    return total


def debtor_estate_value(game: GameState, player: Player) -> int:
    """Кэш + номинал незаложенной земли — то, что арендодателю предложат принять (п.5)."""
    return player.money + unmortgaged_land_value(game, player.game_player_id)


def settle_bankruptcy_to_player(game: GameState, debtor: Player, creditor: Player) -> None:
    """Арендодатель согласился принять остаток имущества должника (п.5)."""
    for prop in game.properties.values():
        if prop.owner == debtor.game_player_id:
            prop.owner = creditor.game_player_id
    creditor.money += debtor.money
    debtor.money = 0
    from .models import PlayerStatus

    debtor.status = PlayerStatus.BANKRUPT


def settle_bankruptcy_to_bank(game: GameState, debtor: Player) -> None:
    """Банкротство в банк — арендодатель отказался, либо долг был налогом (п.5)."""
    for prop in game.properties.values():
        if prop.owner == debtor.game_player_id:
            prop.owner = None
            prop.mortgaged = False
            prop.development = 0
    debtor.money = 0
    from .models import PlayerStatus

    debtor.status = PlayerStatus.BANKRUPT
