"""Переиспользуемые инлайн-клавиатуры (спец. п.11)."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import board
from .models import GameState, Player


def buy_decline_keyboard(cell_index: int, price: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"Купить за {price}", callback_data=f"buy:{cell_index}"),
        InlineKeyboardButton(text="Отказаться", callback_data=f"decline:{cell_index}"),
    ]])


def auction_decline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отказаться от торгов", callback_data="auction_pass"),
    ]])


def landlord_decision_keyboard(debtor_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Принять", callback_data=f"landlord_accept:{debtor_id}"),
        InlineKeyboardButton(text="Отклонить", callback_data=f"landlord_decline:{debtor_id}"),
    ]])


def newgame_continue_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Продолжить", callback_data="newgame_continue"),
    ]])


def newgame_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Start", callback_data="newgame_start"),
    ]])


def resume_or_new_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="▶️ Продолжить старую партию", callback_data="newgame_resume_old"),
        InlineKeyboardButton(text="🆕 Начать новую", callback_data="newgame_start_new"),
    ]])


def bankrupt_confirm_keyboard(player_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Подтвердить банкротство", callback_data=f"bankrupt_confirm:{player_id}"),
        InlineKeyboardButton(text="Отменить", callback_data=f"bankrupt_cancel:{player_id}"),
    ]])


def player_picker_keyboard(game: GameState, exclude_id: str, action: str) -> InlineKeyboardMarkup:
    rows = []
    for pid in game.player_order:
        p = game.players[pid]
        if pid == exclude_id or not p.is_active:
            continue
        rows.append([InlineKeyboardButton(text=p.name, callback_data=f"{action}:{pid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_keyboard(game: GameState, player: Player) -> InlineKeyboardMarkup:
    from . import rules

    rows = []
    for cell_idx, prop in sorted(game.properties.items()):
        if prop.owner != player.game_player_id:
            continue
        c = board.cell(cell_idx)
        if c.type is not board.CellType.STREET:
            continue
        can_plus = rules.can_build(game, prop, c)
        can_minus = prop.development > 0
        if not can_plus and not can_minus:
            continue
        label = f"{c.name} — застройка {prop.development}/5"
        row = [InlineKeyboardButton(text=label, callback_data="noop")]
        buttons = []
        if can_minus:
            buttons.append(InlineKeyboardButton(text="-", callback_data=f"build_minus:{cell_idx}"))
        if can_plus:
            buttons.append(InlineKeyboardButton(text="+", callback_data=f"build_plus:{cell_idx}"))
        rows.append(row)
        rows.append(buttons)
    placeholder = "Нет доступных улиц (нужна монополия на всю группу, п.1)"
    return InlineKeyboardMarkup(inline_keyboard=rows or [[InlineKeyboardButton(text=placeholder, callback_data="noop")]])


def mortgage_keyboard(game: GameState, player: Player) -> InlineKeyboardMarkup:
    from . import rules

    rows = []
    for cell_idx, prop in sorted(game.properties.items()):
        if prop.owner != player.game_player_id:
            continue
        c = board.cell(cell_idx)
        if prop.mortgaged:
            rows.append([InlineKeyboardButton(
                text=f"Снять залог: {c.name} (-{board.mortgage_value(c)})",
                callback_data=f"unmortgage:{cell_idx}",
            )])
        elif rules.can_mortgage(prop):
            rows.append([InlineKeyboardButton(
                text=f"Заложить: {c.name} (+{board.mortgage_value(c)})",
                callback_data=f"mortgage:{cell_idx}",
            )])
    return InlineKeyboardMarkup(inline_keyboard=rows or [[InlineKeyboardButton(text="Нет доступных участков", callback_data="noop")]])


def trade_keyboard(game_over: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="+ Деньги", callback_data="trade_add_money"),
         InlineKeyboardButton(text="+ Участок", callback_data="trade_add_property")],
        [InlineKeyboardButton(text="Подтвердить", callback_data="trade_confirm"),
         InlineKeyboardButton(text="Отменить", callback_data="trade_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


MONEY_PRESETS = (50, 100, 200, 500, 1000, 1500, 2000, 2500, 3000)


def trade_money_keyboard(max_amount: int) -> InlineKeyboardMarkup:
    """Каждая кнопка-сумма сама по себе — подтверждение ИМЕННО этого значения (нажатие сразу
    применяет сумму и закрывает пикер), поэтому отдельная кнопка "Подтвердить" здесь не нужна —
    а тем более такая, что на деле подтверждала бы не сумму, а всю сделку целиком."""
    presets = [a for a in MONEY_PRESETS if a <= max_amount]
    if max_amount > 0 and max_amount not in presets:
        presets.append(max_amount)
    rows: list[list[InlineKeyboardButton]] = []
    if not presets:
        rows.append([InlineKeyboardButton(text="Нет денег", callback_data="noop")])
    else:
        row: list[InlineKeyboardButton] = []
        for amount in presets:
            row.append(InlineKeyboardButton(text=str(amount), callback_data=f"trade_money_set:{amount}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def trade_property_keyboard(cell_indices: list[int], selected: set[int]) -> InlineKeyboardMarkup:
    rows = []
    for cell_idx in sorted(cell_indices):
        c = board.cell(cell_idx)
        mark = "✅ " if cell_idx in selected else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{c.name}", callback_data=f"trade_prop_toggle:{cell_idx}")])
    rows.append([InlineKeyboardButton(text="Подтвердить выборку", callback_data="trade_prop_confirm")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
