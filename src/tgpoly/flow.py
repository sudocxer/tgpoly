"""Оркестрация игрового цикла: ход → броски → перемещение → разбор клетки → рента/грейс-период →
следующий ход. Все хендлеры (handlers/*) — тонкие обёртки, вызывающие функции отсюда."""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

from aiogram import Bot
from aiogram.types import BufferedInputFile, Message

from . import board, hygiene, keyboards, render, rules, storage
from .logs import get_game_logger
from .models import AuctionState, DiceWait, GamePhase, GameState, OrderState, PendingDebt, Player, PropertyState, TradeOffer
from .state import manager
from .timeouts import Window

logger = logging.getLogger(__name__)

DICE_RESULT_PAUSE_SEC = 3  # дать игроку увидеть результат броска, прежде чем поле перерисуется


def log(game: GameState, text: str) -> None:
    get_game_logger(game.game_id).info(text)


def _balance_note(*players: Player) -> str:
    """Показать остаток на счету после любой денежной операции (по просьбе в чате)."""
    return " ".join(f"Баланс {p.name}: {p.money}." for p in players)


# ---------------------------------------------------------------------------
# Рендер поля (п.2)
# ---------------------------------------------------------------------------

async def render_and_send_board(bot: Bot, game: GameState) -> None:
    image_bytes = await render.render_board(bot, game)
    msg = await bot.send_photo(game.chat_id, BufferedInputFile(image_bytes, filename="board.png"))
    await hygiene.replace_transient(bot, game, "board", msg.message_id)


# ---------------------------------------------------------------------------
# Определение очерёдности хода броском кости (не порядок вступления в игру)
# ---------------------------------------------------------------------------

async def start_ordering(bot: Bot, game: GameState) -> None:
    game.phase = GamePhase.ORDERING
    active_ids = [p.game_player_id for p in game.active_players()]
    game.order = OrderState(queue=[active_ids])
    msg = await bot.send_message(game.chat_id, "🎲 Определяем очередь хода — каждый киньте одну кость.")
    game.order.message_ids.append(msg.message_id)
    manager.save(game)


async def handle_order_roll(bot: Bot, game: GameState, message: Message) -> None:
    dice = message.dice
    order = game.order
    if dice is None or dice.emoji != "🎲" or order is None or not order.queue:
        return

    current_group = order.queue[0]
    player = game.player_by_telegram_id(message.from_user.id) if message.from_user else None
    if player is None or player.game_player_id not in current_group or player.game_player_id in order.rolls:
        await hygiene.delete_stray(bot, message.chat.id, message.message_id)
        return

    order.rolls[player.game_player_id] = dice.value
    order.message_ids.append(message.message_id)
    manager.save(game)

    if len(order.rolls) < len(current_group):
        return  # ждём остальных в этой группе/раунде
    await _resolve_order_round(bot, game)


async def _resolve_order_round(bot: Bot, game: GameState) -> None:
    order = game.order
    assert order is not None
    current_group = order.queue.pop(0)
    for pid in current_group:
        order.final_rolls[pid] = order.rolls[pid]

    by_value: dict[int, list[str]] = {}
    for pid in current_group:
        by_value.setdefault(order.rolls[pid], []).append(pid)
    # разбиваем текущую группу на подгруппы по значению, от большего к меньшему,
    # и ставим их в начало очереди — сначала до конца разбираемся с этим местом в очереди
    subgroups = [by_value[v] for v in sorted(by_value, reverse=True)]
    order.queue = subgroups + order.queue
    order.rolls = {}

    while order.queue and len(order.queue[0]) == 1:
        order.resolved.append(order.queue.pop(0)[0])

    if not order.queue:
        await _finish_ordering(bot, game)
        return

    tied = order.queue[0]
    names = ", ".join(game.players[pid].name for pid in tied)
    msg = await bot.send_message(game.chat_id, f"Ничья между {names} — перебросьте кость, только вы вдвоём (или более).")
    order.message_ids.append(msg.message_id)
    manager.save(game)


async def _finish_ordering(bot: Bot, game: GameState) -> None:
    order = game.order
    assert order is not None
    for mid in order.message_ids:
        await hygiene.delete_stray(bot, game.chat_id, mid)  # чистим кости определения очереди (п.14)

    game.player_order = order.resolved
    lines = ["Порядок хода определён:"]
    for i, pid in enumerate(order.resolved, start=1):
        p = game.players[pid]
        lines.append(f"{i}. {p.name} — {order.final_rolls[pid]}")
    await bot.send_message(game.chat_id, "\n".join(lines))

    game.order = None
    game.phase = GamePhase.IN_PROGRESS
    manager.save(game)
    log(game, f"turn order: {[game.players[pid].name for pid in game.player_order]}")
    await start_turn(bot, game)


# ---------------------------------------------------------------------------
# Ход, ожидание костей (п.3, п.6)
# ---------------------------------------------------------------------------

async def start_turn(bot: Bot, game: GameState, same_player: bool = False) -> None:
    player = game.current_player()
    if player is None:
        return
    game.dice_wait = DiceWait(expected_player_id=player.game_player_id)
    manager.save(game)
    if not same_player:
        msg = await bot.send_message(game.chat_id, f"Ход игрока {player.name}. Бросьте 🎲 два раза подряд.")
        await _pin_turn_message(bot, game, msg.message_id)
    await _open_turn_window(bot, game)


async def _pin_turn_message(bot: Bot, game: GameState, message_id: int) -> None:
    """Закрепляем «чей сейчас ход», снимая предыдущий пин, чтобы они не копились."""
    old_id = game.pinned_turn_message_id
    try:
        await bot.pin_chat_message(game.chat_id, message_id, disable_notification=True)
    except Exception:
        logger.debug("failed to pin turn message %s in chat %s", message_id, game.chat_id, exc_info=True)
    game.pinned_turn_message_id = message_id
    manager.save(game)
    if old_id is not None and old_id != message_id:
        try:
            await bot.unpin_chat_message(game.chat_id, message_id=old_id)
        except Exception:
            logger.debug("failed to unpin old turn message %s in chat %s", old_id, game.chat_id, exc_info=True)


async def _unpin_turn_message(bot: Bot, game: GameState) -> None:
    if game.pinned_turn_message_id is None:
        return
    try:
        await bot.unpin_chat_message(game.chat_id, message_id=game.pinned_turn_message_id)
    except Exception:
        logger.debug("failed to unpin turn message in chat %s", game.chat_id, exc_info=True)
    game.pinned_turn_message_id = None
    manager.save(game)


async def _open_turn_window(bot: Bot, game: GameState) -> None:
    player = game.current_player()
    if player is None:
        return
    eligible = {p.telegram_id for p in game.active_players() if p.game_player_id != player.game_player_id}
    window = Window(
        bot=bot, chat_id=game.chat_id,
        label=f"Ожидание броска от {player.name}",
        timeout_min=game.settings.turn_timeout_min,
        vote_delay_min=game.settings.turn_vote_delay_min,
        eligible_voter_ids=eligible,
        on_resolve=lambda reason: _turn_window_resolved(bot, game, reason),
    )
    game.active_window = window
    await window.start()


async def _turn_window_resolved(bot: Bot, game: GameState, reason: str) -> None:
    game.active_window = None
    game.dice_wait = None
    player = game.current_player()
    log(game, f"turn timeout ({reason}) for {player.name if player else '?'}")
    await bot.send_message(game.chat_id, "Ход пропущен — игрок не бросил кости вовремя.")
    await advance_turn(bot, game)


async def advance_turn(bot: Bot, game: GameState) -> None:
    active = game.active_players()
    if len(active) <= 1:
        game.phase = GamePhase.FINISHED
        manager.save(game)
        await _unpin_turn_message(bot, game)
        storage.set_registry_status(game.chat_id, storage.STATUS_FINISHED)
        if active:
            await bot.send_message(game.chat_id, f"🏆 {active[0].name} побеждает — все остальные банкроты!")
        return
    n = len(game.player_order)
    idx = game.turn_index
    for _ in range(n):
        idx = (idx + 1) % n
        if game.players[game.player_order[idx]].is_active:
            break
    game.turn_index = idx
    manager.save(game)
    await start_turn(bot, game)


async def handle_dice_message(bot: Bot, game: GameState, message: Message) -> None:
    dice = message.dice
    if dice is None or dice.emoji != "🎲":
        return
    if game.phase is not GamePhase.IN_PROGRESS or game.dice_wait is None:
        await hygiene.delete_stray(bot, message.chat.id, message.message_id)
        return
    expected = game.players[game.dice_wait.expected_player_id]
    if message.from_user is None or message.from_user.id != expected.telegram_id:
        await hygiene.delete_stray(bot, message.chat.id, message.message_id)
        return

    if game.active_window is not None:
        await game.active_window.cancel()
        game.active_window = None

    if game.dice_wait.first_value is None:
        game.dice_wait.first_value = dice.value
        game.dice_wait.first_message_id = message.message_id
        manager.save(game)
        await _open_turn_window(bot, game)  # окно ожидания второй кости — та же механика (п.3)
        return

    first = game.dice_wait.first_value
    second = dice.value
    game.dice_wait = None
    doubles = first == second
    total = first + second
    await asyncio.sleep(DICE_RESULT_PAUSE_SEC)  # дать доиграть нативной анимации кости в Telegram
    await bot.send_message(game.chat_id, f"{expected.name}: 🎲 {first} + {second} = {total}" + (" (дубль!)" if doubles else ""))
    await _move_player(bot, game, expected, total, doubles)


async def _move_player(bot: Bot, game: GameState, player: Player, steps: int, doubles: bool) -> None:
    old_pos = player.position
    new_pos = (old_pos + steps) % board.BOARD_SIZE
    if new_pos < old_pos:
        player.money += board.PASS_GO_BONUS
        await bot.send_message(game.chat_id, f"{player.name} проходит «Старт», +{board.PASS_GO_BONUS}. {_balance_note(player)}")
    player.position = new_pos
    game.pending_double = doubles
    manager.save(game)
    await render_and_send_board(bot, game)
    await _resolve_landing(bot, game, player)


async def _advance_after_landing(bot: Bot, game: GameState) -> None:
    if game.pending_double:
        game.pending_double = False
        manager.save(game)
        await bot.send_message(game.chat_id, "Дубль — ещё один бросок для того же игрока.")
        await start_turn(bot, game, same_player=True)
    else:
        await advance_turn(bot, game)


async def _announce_and_advance(bot: Bot, game: GameState, text: str) -> None:
    """Итоговое сообщение о действии — ход сразу передаётся дальше. /build и /mortgage остаются
    доступны в любой момент своего хода как независимые команды, без привязки к этому сообщению."""
    await bot.send_message(game.chat_id, text)
    await _advance_after_landing(bot, game)


# ---------------------------------------------------------------------------
# Разбор клетки (п.4)
# ---------------------------------------------------------------------------

async def _resolve_landing(bot: Bot, game: GameState, player: Player) -> None:
    c = board.cell(player.position)

    if c.is_purchasable:
        prop = game.properties.setdefault(c.index, PropertyState(cell_index=c.index))
        if prop.owner is None:
            await _offer_purchase(bot, game, player, c)
        elif prop.owner == player.game_player_id:
            await _announce_and_advance(bot, game, f"{player.name} на своей клетке «{c.name}».")
        elif prop.mortgaged:
            owner_name = game.players[prop.owner].name if prop.owner else "?"
            await _announce_and_advance(
                bot, game, f"{player.name} на клетке «{c.name}» ({owner_name}, в залоге) — рента не взимается.",
            )
        else:
            rent = rules.calculate_rent(game, prop)
            await charge_debt(bot, game, player, rent, prop.owner, "рента", c.name)
        return

    if c.type is board.CellType.TAX:
        await charge_debt(bot, game, player, c.price or 0, None, "налог", c.name)
        return

    if c.type in (board.CellType.CHANCE, board.CellType.CHEST):
        await _draw_card(bot, game, player, c)
        return

    if c.type is board.CellType.GO_TO_JAIL:
        player.position = board.JAIL_INDEX
        manager.save(game)
        await _announce_and_advance(
            bot, game, f"{player.name} отправляется в тюрьму (только позиция — доп. правил тюрьмы нет, п.1).",
        )
        return

    # GO, JAIL (просто в гостях), FREE_PARKING — без эффекта
    await _announce_and_advance(bot, game, f"{player.name} на клетке «{c.name}» — ничего не происходит.")


# ---------------------------------------------------------------------------
# Покупка / аукцион (п.4, п.12)
# ---------------------------------------------------------------------------

async def _offer_purchase(bot: Bot, game: GameState, player: Player, c: board.Cell) -> None:
    game.pending_purchase = c.index
    price = c.price or 0
    text = f"{player.name} попал(а) на «{c.name}» (свободно). Купить за {price}? {_balance_note(player)}"
    if player.money < price and rules.has_any_mortgageable(game, player.game_player_id):
        text += " Не хватает наличных — можно сначала /mortgage заложить другой участок."
    msg = await bot.send_message(game.chat_id, text, reply_markup=keyboards.buy_decline_keyboard(c.index, price))
    game.transient_message_ids["purchase_prompt"] = msg.message_id
    manager.save(game)


def is_purchase_pending(game: GameState, cell_index: int) -> bool:
    """Кнопка «Купить/Отказаться» могла остаться от уже решённого предложения — проверяем,
    что это именно то решение, которое сейчас реально ожидается (а не устаревший клик)."""
    return game.pending_purchase == cell_index


async def _clear_purchase_prompt(bot: Bot, game: GameState) -> None:
    """Вопрос «Купить за N?» свою роль отыграл — убираем его из чата (п.14). Сам факт покупки/
    отказа фиксируется отдельным сообщением-результатом, которое остаётся навсегда."""
    prompt_id = game.transient_message_ids.pop("purchase_prompt", None)
    if prompt_id is not None:
        await hygiene.delete_stray(bot, game.chat_id, prompt_id)


async def buy_property(bot: Bot, game: GameState, player: Player, cell_index: int) -> None:
    if not is_purchase_pending(game, cell_index):
        return
    c = board.cell(cell_index)
    prop = game.properties.setdefault(cell_index, PropertyState(cell_index=cell_index))
    if prop.owner is not None or player.money < (c.price or 0):
        return
    game.pending_purchase = None
    player.money -= c.price or 0
    prop.owner = player.game_player_id
    manager.save(game)
    log(game, f"{player.name} buys {c.name} for {c.price}")
    await _clear_purchase_prompt(bot, game)
    await _announce_and_advance(bot, game, f"{player.name} купил(а) «{c.name}» за {c.price}. {_balance_note(player)}")


async def decline_purchase(bot: Bot, game: GameState, player: Player, cell_index: int) -> None:
    if not is_purchase_pending(game, cell_index):
        return
    game.pending_purchase = None
    await _clear_purchase_prompt(bot, game)
    c = board.cell(cell_index)
    if game.settings.auction_enabled:
        await _start_auction(bot, game, cell_index)
    else:
        await _announce_and_advance(bot, game, f"«{c.name}» остаётся у банка.")


async def _start_auction(bot: Bot, game: GameState, cell_index: int) -> None:
    c = board.cell(cell_index)
    delay = game.settings.auction_close_delay_sec
    text = (
        f"📢 Аукцион: «{c.name}». Стартовая цена от 1. Отвечайте (reply) числом.\n"
        f"Аукцион закрывается через {delay} сек. тишины после последней ставки."
    )
    msg = await bot.send_message(game.chat_id, text, reply_markup=keyboards.auction_decline_keyboard())
    game.auction = AuctionState(
        cell_index=cell_index, announce_message_id=msg.message_id, message_ids={msg.message_id},
        decline_prompt_message_id=msg.message_id,
    )
    manager.save(game)
    game.auction.timer_task = asyncio.create_task(_auction_timer(bot, game, game.auction))


async def _auction_timer(bot: Bot, game: GameState, auction: AuctionState) -> None:
    try:
        await asyncio.sleep(game.settings.auction_close_delay_sec)
    except asyncio.CancelledError:
        return
    if game.auction is auction:
        await _close_auction(bot, game)


async def register_bid(bot: Bot, game: GameState, message: Message) -> bool:
    """True, если сообщение было обработано как попытка ставки (валидная или нет) — п.12.

    Ставкой считается: (а) reply на любое сообщение в ветке аукциона (как раньше), либо
    (б) обычное число вообще без reply — на практике люди часто забывают ответить именно
    на нужное сообщение, а само число и так однозначно читается как ставка. Reply на что-то
    ДРУГОЕ (не в ветке аукциона — например, в конструктор сделки) ставкой не считается."""
    auction = game.auction
    if auction is None:
        return False
    if message.reply_to_message is not None and message.reply_to_message.message_id not in auction.message_ids:
        return False
    if not message.text or not message.text.strip().lstrip("-").isdigit():
        return False

    amount = int(message.text.strip())
    if amount <= auction.best_bid:
        await message.reply(f"Ставка должна быть больше текущей лучшей ({auction.best_bid}).")
        return True
    bidder = game.player_by_telegram_id(message.from_user.id) if message.from_user else None
    if bidder is None or not bidder.is_active:
        return True
    if bidder.money < amount:
        await message.reply("Недостаточно денег для такой ставки.")
        return True

    auction.best_bid = amount
    auction.best_bidder = bidder.game_player_id
    auction.message_ids.add(message.message_id)
    if auction.timer_task:
        auction.timer_task.cancel()
    auction.timer_task = asyncio.create_task(_auction_timer(bot, game, auction))

    # кнопка «Отказаться» переезжает на новое сообщение о ставке — со старого снимается
    old_prompt_id = auction.decline_prompt_message_id
    if old_prompt_id is not None:
        try:
            await bot.edit_message_reply_markup(chat_id=game.chat_id, message_id=old_prompt_id, reply_markup=None)
        except Exception:
            logger.debug("failed to strip stale auction decline button %s in chat %s", old_prompt_id, game.chat_id, exc_info=True)

    reply_msg = await message.reply(f"Принято: {bidder.name} — {amount}.", reply_markup=keyboards.auction_decline_keyboard())
    auction.decline_prompt_message_id = reply_msg.message_id
    manager.save(game)
    return True


async def auction_pass(bot: Bot, game: GameState, player: Player) -> bool:
    """«Отказаться от торгов» — если так отметились все активные игроки, кроме текущего лидера,
    аукцион закрывается сразу, не дожидаясь таймера тишины (п.12)."""
    auction = game.auction
    if auction is None or player.game_player_id == auction.best_bidder:
        return False
    auction.declined.add(player.game_player_id)
    manager.save(game)

    eligible = {p.game_player_id for p in game.active_players()}
    eligible.discard(auction.best_bidder)
    if eligible and eligible <= auction.declined:
        if auction.timer_task:
            auction.timer_task.cancel()
        await _close_auction(bot, game)
    return True


async def _close_auction(bot: Bot, game: GameState) -> None:
    auction = game.auction
    game.auction = None
    if auction is None:
        return
    if auction.decline_prompt_message_id is not None:
        try:
            await bot.edit_message_reply_markup(chat_id=game.chat_id, message_id=auction.decline_prompt_message_id, reply_markup=None)
        except Exception:
            logger.debug("failed to strip auction decline button on close in chat %s", game.chat_id, exc_info=True)
    c = board.cell(auction.cell_index)
    if auction.best_bidder:
        winner = game.players[auction.best_bidder]
        winner.money -= auction.best_bid
        prop = game.properties.setdefault(auction.cell_index, PropertyState(cell_index=auction.cell_index))
        prop.owner = winner.game_player_id
        text = f"Аукцион завершён: «{c.name}» уходит {winner.name} за {auction.best_bid}. {_balance_note(winner)}"
        log(game, f"auction: {c.name} -> {winner.name} for {auction.best_bid}")
    else:
        text = f"Аукцион завершён: ставок не было, «{c.name}» остаётся у банка."
    manager.save(game)
    await _announce_and_advance(bot, game, text)


# ---------------------------------------------------------------------------
# Долги: рента/налог, грейс-период, решение арендодателя (п.5, п.6)
# ---------------------------------------------------------------------------

async def charge_debt(bot: Bot, game: GameState, debtor: Player, amount: int, creditor_id: str | None, reason: str, cell_name: str = "") -> None:
    if amount <= 0:
        await _announce_and_advance(bot, game, f"{debtor.name}: {reason} по «{cell_name}» — сумма 0.")
        return
    if debtor.money >= amount:
        debtor.money -= amount
        creditor = game.players[creditor_id] if creditor_id else None
        if creditor is not None:
            creditor.money += amount
        manager.save(game)
        log(game, f"{debtor.name} pays {amount} ({reason}: {cell_name})")
        note = _balance_note(debtor, creditor) if creditor else _balance_note(debtor)
        await _announce_and_advance(bot, game, f"{debtor.name} платит {amount} ({reason}: {cell_name}). {note}")
        return

    game.pending_debt = PendingDebt(debtor_id=debtor.game_player_id, amount=amount, creditor_id=creditor_id, reason=reason)
    manager.save(game)
    await bot.send_message(
        game.chat_id,
        f"{debtor.name} должен {amount} ({reason}: {cell_name}), но наличных не хватает. "
        f"Открыт грейс-период: /build (продать дома), /mortgage (заложить участок), /swap (обмен).",
    )
    await _open_debtor_window(bot, game)


async def _open_debtor_window(bot: Bot, game: GameState) -> None:
    debt = game.pending_debt
    if debt is None:
        return
    debtor = game.players[debt.debtor_id]
    eligible = {p.telegram_id for p in game.active_players() if p.game_player_id != debtor.game_player_id}
    window = Window(
        bot=bot, chat_id=game.chat_id,
        label=f"Грейс-период: {debtor.name} должен {debt.amount}",
        timeout_min=game.settings.debtor_timeout_min,
        vote_delay_min=game.settings.debtor_vote_delay_min,
        eligible_voter_ids=eligible,
        on_resolve=lambda reason: _debtor_window_resolved(bot, game, reason),
    )
    game.active_window = window
    await window.start()


async def _debtor_window_resolved(bot: Bot, game: GameState, reason: str) -> None:
    game.active_window = None
    debt = game.pending_debt
    if debt is None:
        return
    debtor = game.players[debt.debtor_id]
    log(game, f"debtor window resolved ({reason}) for {debtor.name}")

    if debt.creditor_id is None:
        rules.settle_bankruptcy_to_bank(game, debtor)
        game.pending_debt = None
        manager.save(game)
        await bot.send_message(
            game.chat_id,
            f"{debtor.name} не смог(ла) расплатиться по налогу — банкротство в банк. {_balance_note(debtor)}",
        )
        await _advance_after_landing(bot, game)
        return

    creditor = game.players[debt.creditor_id]
    manager.save(game)
    await bot.send_message(
        game.chat_id,
        f"{debtor.name} не смог(ла) расплатиться. {creditor.name}, принять остаток имущества должника "
        f"(≈{rules.debtor_estate_value(game, debtor)}) вместо долга?",
        reply_markup=keyboards.landlord_decision_keyboard(debtor.game_player_id),
    )
    await _open_landlord_window(bot, game)


async def _open_landlord_window(bot: Bot, game: GameState) -> None:
    debt = game.pending_debt
    if debt is None or debt.creditor_id is None:
        return
    creditor = game.players[debt.creditor_id]
    eligible = {p.telegram_id for p in game.active_players() if p.game_player_id != creditor.game_player_id}
    window = Window(
        bot=bot, chat_id=game.chat_id,
        label=f"Ожидание решения {creditor.name}",
        timeout_min=game.settings.landlord_timeout_min,
        vote_delay_min=game.settings.landlord_vote_delay_min,
        eligible_voter_ids=eligible,
        on_resolve=lambda reason: _landlord_window_resolved(bot, game, reason),
    )
    game.active_window = window
    await window.start()


async def _landlord_window_resolved(bot: Bot, game: GameState, reason: str) -> None:
    """Таймаут/голосование по умолчанию = отказ (банкротство в банк) — п.5, п.6."""
    game.active_window = None
    debt = game.pending_debt
    if debt is None:
        return
    debtor = game.players[debt.debtor_id]
    rules.settle_bankruptcy_to_bank(game, debtor)
    game.pending_debt = None
    manager.save(game)
    log(game, f"landlord window resolved ({reason}): bankruptcy to bank for {debtor.name}")
    await bot.send_message(
        game.chat_id, f"Решение не поступило — банкротство {debtor.name} в банк. {_balance_note(debtor)}",
    )
    await _advance_after_landing(bot, game)


async def landlord_accept(bot: Bot, game: GameState, creditor: Player) -> None:
    debt = game.pending_debt
    if debt is None or debt.creditor_id != creditor.game_player_id:
        return
    if game.active_window is not None:
        await game.active_window.cancel()
        game.active_window = None
    debtor = game.players[debt.debtor_id]
    rules.settle_bankruptcy_to_player(game, debtor, creditor)
    game.pending_debt = None
    manager.save(game)
    log(game, f"{creditor.name} accepts {debtor.name}'s estate")
    await bot.send_message(
        game.chat_id,
        f"{creditor.name} принимает остаток имущества {debtor.name}. {_balance_note(creditor, debtor)}",
    )
    await _advance_after_landing(bot, game)


async def landlord_decline(bot: Bot, game: GameState, creditor: Player) -> None:
    debt = game.pending_debt
    if debt is None or debt.creditor_id != creditor.game_player_id:
        return
    if game.active_window is not None:
        await game.active_window.cancel()
        game.active_window = None
    debtor = game.players[debt.debtor_id]
    rules.settle_bankruptcy_to_bank(game, debtor)
    game.pending_debt = None
    manager.save(game)
    log(game, f"{creditor.name} declines; bankruptcy to bank for {debtor.name}")
    await bot.send_message(
        game.chat_id,
        f"{creditor.name} отказался(ась) — банкротство {debtor.name} в банк. {_balance_note(debtor)}",
    )
    await _advance_after_landing(bot, game)


async def maybe_settle_debt_early(bot: Bot, game: GameState) -> None:
    """После продажи домов / ипотеки во время грейс-периода — проверить, хватает ли теперь денег (п.5)."""
    debt = game.pending_debt
    if debt is None:
        return
    debtor = game.players[debt.debtor_id]
    if debtor.money < debt.amount:
        return
    if game.active_window is not None:
        await game.active_window.cancel()
        game.active_window = None
    debtor.money -= debt.amount
    creditor = game.players[debt.creditor_id] if debt.creditor_id else None
    if creditor is not None:
        creditor.money += debt.amount
    game.pending_debt = None
    manager.save(game)
    note = _balance_note(debtor, creditor) if creditor else _balance_note(debtor)
    await _announce_and_advance(bot, game, f"{debtor.name} расплатился(ась) — долг закрыт. {note}")


# ---------------------------------------------------------------------------
# Постройка / ипотека (п.1, п.5, п.11) — доступны в любой момент своего хода независимо,
# сами по себе ход не завершают (см. maybe_settle_debt_early про досрочное закрытие долга)
# ---------------------------------------------------------------------------

async def build_house(bot: Bot, game: GameState, player: Player, cell_index: int) -> None:
    c = board.cell(cell_index)
    prop = game.properties.get(cell_index)
    if prop is None or prop.owner != player.game_player_id:
        return
    cost = board.house_cost(c)
    if not rules.can_build(game, prop, c) or player.money < cost:
        return
    player.money -= cost
    prop.development += 1
    manager.save(game)
    log(game, f"{player.name} builds on {c.name} -> {prop.development}")
    label = "отель" if prop.development == 5 else f"дом ({prop.development}/4)"
    await bot.send_message(game.chat_id, f"{player.name} строит на «{c.name}»: {label}, -{cost}. {_balance_note(player)}")
    await maybe_settle_debt_early(bot, game)


async def sell_house(bot: Bot, game: GameState, player: Player, cell_index: int) -> None:
    c = board.cell(cell_index)
    prop = game.properties.get(cell_index)
    if prop is None or prop.owner != player.game_player_id or prop.development <= 0:
        return
    price = board.house_sell_price(c)
    player.money += price
    prop.development -= 1
    manager.save(game)
    log(game, f"{player.name} sells a house on {c.name} -> {prop.development}")
    await bot.send_message(
        game.chat_id,
        f"{player.name} продаёт дом банку на «{c.name}» (осталось {prop.development}/4), +{price}. {_balance_note(player)}",
    )
    await maybe_settle_debt_early(bot, game)


async def mortgage_property(bot: Bot, game: GameState, player: Player, cell_index: int) -> None:
    c = board.cell(cell_index)
    prop = game.properties.get(cell_index)
    if prop is None or prop.owner != player.game_player_id or not rules.can_mortgage(prop):
        return
    prop.mortgaged = True
    value = board.mortgage_value(c)
    player.money += value
    manager.save(game)
    log(game, f"{player.name} mortgages {c.name}")
    await bot.send_message(game.chat_id, f"{player.name} закладывает «{c.name}», +{value}. {_balance_note(player)}")
    await maybe_settle_debt_early(bot, game)


async def unmortgage_property(bot: Bot, game: GameState, player: Player, cell_index: int) -> None:
    c = board.cell(cell_index)
    prop = game.properties.get(cell_index)
    if prop is None or prop.owner != player.game_player_id or not prop.mortgaged:
        return
    cost = board.mortgage_value(c)
    if player.money < cost:
        return
    prop.mortgaged = False
    player.money -= cost
    manager.save(game)
    log(game, f"{player.name} unmortgages {c.name}")
    await bot.send_message(game.chat_id, f"{player.name} снимает залог с «{c.name}», -{cost}. {_balance_note(player)}")


# ---------------------------------------------------------------------------
# Карты Шанс/Казна — минимальный плейсхолдер-набор (в спеке содержимое карт не определено)
# ---------------------------------------------------------------------------

def _card_collect(player: Player, amount: int) -> tuple[str, bool]:
    player.money += amount
    return f"Банк начисляет {amount} (деньги автоматически зачислены). {_balance_note(player)}", False


def _card_pay(player: Player, amount: int) -> tuple[str, bool]:
    player.money = max(0, player.money - amount)
    return f"Вы платите {amount} в банк (деньги автоматически списаны). {_balance_note(player)}", False


def _card_move_to(player: Player, index: int, label: str, grant_go_bonus: bool = False) -> tuple[str, bool]:
    player.position = index
    if grant_go_bonus:
        player.money += board.PASS_GO_BONUS
        return f"Переместитесь на «{label}», +{board.PASS_GO_BONUS}. {_balance_note(player)}", True
    return f"Переместитесь на «{label}».", True


def _card_collect_from_all(game: GameState, player: Player, amount: int) -> tuple[str, bool]:
    for other in game.active_players():
        if other.game_player_id != player.game_player_id:
            other.money = max(0, other.money - amount)
            player.money += amount
    return f"Каждый игрок платит вам {amount} (деньги автоматически спишутся). {_balance_note(player)}", False


async def _draw_card(bot: Bot, game: GameState, player: Player, c: board.Cell) -> None:
    effects = [
        lambda: _card_collect(player, 200),
        lambda: _card_pay(player, 100),
        lambda: _card_move_to(player, 0, "Старт", grant_go_bonus=True),
        lambda: _card_move_to(player, board.JAIL_INDEX, "Тюрьма"),
        lambda: _card_collect_from_all(game, player, 50),
    ]
    result_text, moved = random.choice(effects)()
    manager.save(game)
    log(game, f"{player.name} draws card on {c.name}: {result_text}")
    text = f"{c.name}: {player.name} тянет карту — {result_text}"

    if moved:
        # карта передвинула игрока — поле перерисовывается, а новая клетка разбирается заново,
        # как при обычном ходе (если это снова Шанс/Казна — колода тянется ещё раз)
        await bot.send_message(game.chat_id, text)
        await render_and_send_board(bot, game)
        await _resolve_landing(bot, game, player)
    else:
        await _announce_and_advance(bot, game, text)


# ---------------------------------------------------------------------------
# Обмен /swap (п.7)
# ---------------------------------------------------------------------------

def format_trade(game: GameState) -> str:
    trade = game.trade
    assert trade is not None
    initiator = game.players[trade.initiator_id]
    target = game.players[trade.target_id]

    def side(p: Player, money: int, props: set[int]) -> str:
        names = ", ".join(board.cell(i).name for i in sorted(props)) or "—"
        return f"{p.name}: {money} монет, участки: {names}"

    return (
        f"Сделка {initiator.name} ↔ {target.name}\n"
        f"{side(initiator, trade.initiator_money, trade.initiator_properties)}\n"
        f"{side(target, trade.target_money, trade.target_properties)}\n"
        f"Подтверждено: {initiator.name} {'✅' if trade.initiator_confirmed else '❌'}, "
        f"{target.name} {'✅' if trade.target_confirmed else '❌'}"
    )


async def start_trade(bot: Bot, game: GameState, initiator: Player, target: Player) -> None:
    game.trade = TradeOffer(initiator_id=initiator.game_player_id, target_id=target.game_player_id)
    msg = await bot.send_message(game.chat_id, format_trade(game), reply_markup=keyboards.trade_keyboard())
    game.trade.message_id = msg.message_id
    manager.save(game)


def _trade_side(trade: TradeOffer, player: Player) -> str | None:
    if player.game_player_id == trade.initiator_id:
        return "initiator"
    if player.game_player_id == trade.target_id:
        return "target"
    return None


async def _refresh_trade_message(bot: Bot, game: GameState) -> None:
    trade = game.trade
    if trade is None or trade.message_id is None:
        return
    await bot.edit_message_text(
        text=format_trade(game), chat_id=game.chat_id, message_id=trade.message_id,
        reply_markup=keyboards.trade_keyboard(),
    )


# --- выбор суммы: кнопки с готовыми числами + reply своим числом на то же сообщение --------

async def open_money_picker(bot: Bot, game: GameState, player: Player) -> None:
    trade = game.trade
    side = _trade_side(trade, player) if trade else None
    if trade is None or side is None:
        return
    old_id = trade.money_picker_message_ids.get(side)
    if old_id is not None:
        await hygiene.delete_stray(bot, game.chat_id, old_id)
    text = (
        f"{player.name}, сколько монет предложить (максимум {player.money})? "
        f"Можно нажать кнопку либо ответить (reply) на это сообщение своим числом."
    )
    msg = await bot.send_message(game.chat_id, text, reply_markup=keyboards.trade_money_keyboard(player.money))
    trade.money_picker_message_ids[side] = msg.message_id
    manager.save(game)


async def set_trade_money(bot: Bot, game: GameState, player: Player, amount: int) -> bool:
    trade = game.trade
    side = _trade_side(trade, player) if trade else None
    if trade is None or side is None or amount < 0 or amount > player.money:
        return False
    if side == "initiator":
        trade.initiator_money = amount
    else:
        trade.target_money = amount
    trade.initiator_confirmed = False
    trade.target_confirmed = False
    picker_id = trade.money_picker_message_ids.pop(side, None)
    manager.save(game)
    await _refresh_trade_message(bot, game)
    if picker_id is not None:
        # сумма уже видна на главном сообщении сделки — пикер своё отыграл (по просьбе в чате)
        await hygiene.delete_stray(bot, game.chat_id, picker_id)
    return True


async def handle_money_picker_reply(bot: Bot, game: GameState, message: Message) -> bool:
    trade = game.trade
    if trade is None or message.reply_to_message is None:
        return False
    if message.reply_to_message.message_id not in trade.money_picker_message_ids.values():
        return False
    if message.from_user is None or not message.text or not message.text.strip().isdigit():
        return False
    player = game.player_by_telegram_id(message.from_user.id)
    if player is None:
        return False
    amount = int(message.text.strip())
    if amount > player.money:
        await message.reply(f"У вас только {player.money}.")
        return True
    await set_trade_money(bot, game, player, amount)
    await hygiene.delete_stray(bot, game.chat_id, message.message_id)  # своё числовое сообщение тоже убираем
    return True


# --- выбор участков: тоггл-кнопки с ✅, подтверждение выборки (не всей сделки) --------------

def _owned_cell_indices(game: GameState, player_id: str) -> list[int]:
    return [cell_idx for cell_idx, prop in game.properties.items() if prop.owner == player_id]


async def open_property_picker(bot: Bot, game: GameState, player: Player) -> None:
    trade = game.trade
    side = _trade_side(trade, player) if trade else None
    if trade is None or side is None:
        return
    owned = _owned_cell_indices(game, player.game_player_id)
    if not owned:
        await bot.send_message(game.chat_id, f"{player.name}, у вас нет участков для обмена.")
        return
    old_id = trade.property_picker_message_ids.get(side)
    if old_id is not None:
        await hygiene.delete_stray(bot, game.chat_id, old_id)
    selected = trade.initiator_properties if side == "initiator" else trade.target_properties
    msg = await bot.send_message(
        game.chat_id, f"{player.name}, выберите участки для сделки:",
        reply_markup=keyboards.trade_property_keyboard(owned, selected),
    )
    trade.property_picker_message_ids[side] = msg.message_id
    manager.save(game)


async def toggle_trade_property(bot: Bot, game: GameState, player: Player, cell_index: int) -> bool:
    trade = game.trade
    side = _trade_side(trade, player) if trade else None
    if trade is None or side is None:
        return False
    prop = game.properties.get(cell_index)
    if prop is None or prop.owner != player.game_player_id:
        return False

    target_set = trade.initiator_properties if side == "initiator" else trade.target_properties
    if cell_index in target_set:
        target_set.discard(cell_index)
    else:
        target_set.add(cell_index)
    trade.initiator_confirmed = False
    trade.target_confirmed = False
    manager.save(game)

    picker_msg_id = trade.property_picker_message_ids.get(side)
    if picker_msg_id is not None:
        owned = _owned_cell_indices(game, player.game_player_id)
        try:
            await bot.edit_message_reply_markup(
                chat_id=game.chat_id, message_id=picker_msg_id,
                reply_markup=keyboards.trade_property_keyboard(owned, target_set),
            )
        except Exception:
            logger.debug("failed to refresh property picker %s in chat %s", picker_msg_id, game.chat_id, exc_info=True)
    return True


async def confirm_property_picker(bot: Bot, game: GameState, player: Player) -> None:
    trade = game.trade
    side = _trade_side(trade, player) if trade else None
    if trade is None or side is None:
        return
    picker_msg_id = trade.property_picker_message_ids.pop(side, None)
    manager.save(game)
    await _refresh_trade_message(bot, game)
    if picker_msg_id is not None:
        # выборка уже видна на главном сообщении сделки — пикер своё отыграл (по просьбе в чате)
        await hygiene.delete_stray(bot, game.chat_id, picker_msg_id)


async def _cleanup_trade_pickers(bot: Bot, game: GameState, trade: TradeOffer) -> None:
    """Сделка завершается (исполнена или отменена) — любые ещё открытые пикеры сумм/участков
    больше не нужны ни с чьей стороны, подчищаем все разом."""
    for msg_id in list(trade.money_picker_message_ids.values()):
        await hygiene.delete_stray(bot, game.chat_id, msg_id)
    trade.money_picker_message_ids.clear()
    for msg_id in list(trade.property_picker_message_ids.values()):
        await hygiene.delete_stray(bot, game.chat_id, msg_id)
    trade.property_picker_message_ids.clear()


def _describe_trade_side(p: Player, money: int, props: set[int]) -> str:
    parts = []
    if money:
        parts.append(f"{money} монет")
    if props:
        parts.append(", ".join(board.cell(i).name for i in sorted(props)))
    return " + ".join(parts) if parts else "ничего"


async def trade_confirm(bot: Bot, game: GameState, player: Player) -> None:
    trade = game.trade
    if trade is None:
        return
    if player.game_player_id == trade.initiator_id:
        trade.initiator_confirmed = True
    elif player.game_player_id == trade.target_id:
        trade.target_confirmed = True
    else:
        return
    if trade.initiator_confirmed and trade.target_confirmed:
        await _execute_trade(bot, game)
    else:
        manager.save(game)
        await _refresh_trade_message(bot, game)


async def _execute_trade(bot: Bot, game: GameState) -> None:
    trade = game.trade
    assert trade is not None
    initiator = game.players[trade.initiator_id]
    target = game.players[trade.target_id]

    if initiator.money < trade.initiator_money or target.money < trade.target_money:
        await _cleanup_trade_pickers(bot, game, trade)
        await bot.send_message(game.chat_id, "Сделка отменена: недостаточно денег у одной из сторон.")
        game.trade = None
        manager.save(game)
        return

    initiator_gave = _describe_trade_side(initiator, trade.initiator_money, trade.initiator_properties)
    target_gave = _describe_trade_side(target, trade.target_money, trade.target_properties)

    initiator.money -= trade.initiator_money
    target.money += trade.initiator_money
    target.money -= trade.target_money
    initiator.money += trade.target_money
    for cell_idx in trade.initiator_properties:
        game.properties[cell_idx].owner = target.game_player_id
    for cell_idx in trade.target_properties:
        game.properties[cell_idx].owner = initiator.game_player_id

    await _cleanup_trade_pickers(bot, game, trade)
    game.trade = None
    manager.save(game)
    log(game, f"trade executed: {initiator.name} <-> {target.name}")
    await bot.send_message(
        game.chat_id,
        f"✅ Сделка {initiator.name} ↔ {target.name} завершена.\n"
        f"{initiator.name} отдаёт: {initiator_gave}\n"
        f"{target.name} отдаёт: {target_gave}",
    )
    await maybe_settle_debt_early(bot, game)


async def trade_cancel(bot: Bot, game: GameState) -> None:
    trade = game.trade
    if trade is not None:
        await _cleanup_trade_pickers(bot, game, trade)
    game.trade = None
    manager.save(game)
    await bot.send_message(game.chat_id, "Сделка отменена.")


# ---------------------------------------------------------------------------
# Пауза / продолжение партии, добровольное банкротство
# ---------------------------------------------------------------------------

def blocking_action_reason(game: GameState) -> str | None:
    """Есть ли сейчас незавершённое взаимодействие, из-за которого паузу/банкротство лучше
    отложить (их состояние держится на конкретных живых сообщениях/таймерах, не сериализуется)."""
    if game.auction is not None:
        return "идёт аукцион"
    if game.trade is not None:
        return "не завершена сделка"
    if game.pending_debt is not None:
        return "не закрыт грейс-период по долгу"
    if game.pending_purchase is not None:
        return "не решена покупка участка"
    return None


async def pause_game(bot: Bot, game: GameState) -> None:
    """Партия ставится на паузу: снимаем активный таймер хода, сохраняем точку возврата
    (фаза/чей ход) в файл партии и выгружаем партию из памяти — продолжить можно /resume."""
    if game.active_window is not None:
        await game.active_window.cancel()
        game.active_window = None
    game.dice_wait = None
    await _unpin_turn_message(bot, game)
    manager.save(game)
    storage.set_registry_status(game.chat_id, storage.STATUS_PAUSED)
    manager.remove(game.chat_id)
    log(game, "game paused")
    await bot.send_message(game.chat_id, "⏸ Партия поставлена на паузу. Чтобы продолжить — команда /resume.")


async def resume_game(bot: Bot, chat_id: int) -> GameState | None:
    """Поднимаем партию, поставленную на паузу, обратно с диска и продолжаем с той же точки хода."""
    entry = storage.get_registry_entry(chat_id)
    if entry is None or entry.status != storage.STATUS_PAUSED:
        return None
    game = storage.load_game(Path(entry.file_path), chat_id, entry.game_id, entry.group_title)
    manager.attach(game)
    storage.set_registry_status(chat_id, storage.STATUS_ACTIVE)
    log(game, "game resumed")
    await bot.send_message(chat_id, "▶️ Партия возобновлена.")
    if game.phase is GamePhase.IN_PROGRESS:
        await start_turn(bot, game)
    return game


async def voluntary_bankrupt(bot: Bot, game: GameState, player: Player) -> None:
    """Игрок сам объявляет банкротство и досрочно выходит из игры — вся его собственность
    уходит банку (по аналогии с settle_bankruptcy_to_bank, спец. п.5)."""
    was_current = game.current_player() is player
    if was_current:
        if game.active_window is not None:
            await game.active_window.cancel()
            game.active_window = None
        game.dice_wait = None

    rules.settle_bankruptcy_to_bank(game, player)
    manager.save(game)
    log(game, f"{player.name} declares voluntary bankruptcy")
    await bot.send_message(
        game.chat_id,
        f"💀 {player.name} объявляет банкротство и выходит из игры. Собственность переходит банку. "
        f"{_balance_note(player)}",
    )

    active = game.active_players()
    if len(active) <= 1:
        game.phase = GamePhase.FINISHED
        manager.save(game)
        await _unpin_turn_message(bot, game)
        storage.set_registry_status(game.chat_id, storage.STATUS_FINISHED)
        if active:
            await bot.send_message(game.chat_id, f"🏆 {active[0].name} побеждает — все остальные банкроты!")
        return

    if was_current:
        await advance_turn(bot, game)
