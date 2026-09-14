"""Excel-хранилище (спец. п.10): один .xlsx на партию, шаблон default.xlsx, листы
Настройки / Игроки / Собственность. Пишем/читаем файл целиком — см. п.10 про снэпшоты."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet

from . import board
from .models import ALL_SETTINGS_KEYS, GamePhase, GameState, Player, PlayerStatus, PropertyState, Settings

DATA_DIR = Path("data")
GAMES_DIR = DATA_DIR / "games"
DEFAULT_TEMPLATE_PATH = DATA_DIR / "default.xlsx"
REGISTRY_PATH = DATA_DIR / "games_registry.xlsx"

STATUS_ACTIVE = "Активна"
STATUS_PAUSED = "Пауза"
STATUS_FINISHED = "Завершена"

_FORBIDDEN_FS_CHARS = re.compile(r'[\\/:*?"<>|]')
_MAX_NAME_LEN = 60

FONT_NAME = "Calibri"
HEADER_FONT = Font(name=FONT_NAME, size=11, bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="305496")
EDITABLE_FILL = PatternFill("solid", fgColor="FFFF00")
BORDER = Border(*(Side(style="thin", color="BFBFBF"),) * 4)

SETTINGS_HEADERS = ["Ключ", "Настройка", "Значение", "Тип / допустимые значения"]
SETTINGS_LABELS = {
    "start_money": "Стартовая сумма у каждого игрока",
    "auction_enabled": "Аукцион при отказе от покупки клетки",
    "auction_close_delay_sec": "Тишина до закрытия аукциона (сек)",
    "max_players": "Максимум игроков (минимум фиксирован — 2)",
    "debtor_timeout_min": "Таймаут окна должника (мин, или inf)",
    "debtor_vote_delay_min": "Задержка до голосования — должник (мин, или off)",
    "landlord_timeout_min": "Таймаут окна арендодателя (мин, или inf)",
    "landlord_vote_delay_min": "Задержка до голосования — арендодатель (мин, или off)",
    "turn_timeout_min": "Таймаут обычного хода / AFK (мин, или inf)",
    "turn_vote_delay_min": "Задержка до голосования — обычный ход (мин, или off)",
}

PLAYERS_HEADERS = ["game_player_id", "telegram_id", "Имя/ник", "Деньги", "Позиция", "Статус", "Админ партии"]
PROPERTIES_HEADERS = ["Клетка (id)", "Название", "Группа", "Номинал", "Владелец", "В залоге", "Застройка (0-5)"]


def sanitize_component(name: str, max_len: int = _MAX_NAME_LEN) -> str:
    cleaned = _FORBIDDEN_FS_CHARS.sub("_", name).strip()
    cleaned = cleaned or "game"
    return cleaned[:max_len]


def next_game_id() -> str:
    GAMES_DIR.mkdir(parents=True, exist_ok=True)
    existing = []
    for f in GAMES_DIR.glob("*_g*.xlsx"):
        m = re.search(r"_g(\d+)\.xlsx$", f.name)
        if m:
            existing.append(int(m.group(1)))
    n = max(existing, default=0) + 1
    return f"g{n:03d}"


def game_file_path(group_title: str, game_id: str) -> Path:
    return GAMES_DIR / f"{sanitize_component(group_title)}_{game_id}.xlsx"


def _style_header(ws: Worksheet, row: int, headers: list[str]) -> None:
    for col, text in enumerate(headers, start=1):
        c = ws.cell(row=row, column=col, value=text)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    ws.row_dimensions[row].height = 26


def _write_settings_sheet(wb: openpyxl.Workbook, settings: Settings, sheet_name: str = "Настройки") -> None:
    ws = wb.create_sheet(sheet_name) if sheet_name not in wb.sheetnames else wb[sheet_name]
    _style_header(ws, 1, SETTINGS_HEADERS)
    row = 2
    for key in ALL_SETTINGS_KEYS:
        ws.cell(row=row, column=1, value=key).font = Font(name="Consolas", size=10)
        ws.cell(row=row, column=2, value=SETTINGS_LABELS[key])
        value_cell = ws.cell(row=row, column=3, value=settings.display_value(key))
        value_cell.fill = EDITABLE_FILL
        value_cell.font = Font(name=FONT_NAME, bold=True)
        ws.cell(row=row, column=4, value="TRUE/FALSE" if key == "auction_enabled" else "число, либо inf/off")
        for col in range(1, 5):
            ws.cell(row=row, column=col).border = BORDER
        row += 1
    ws.freeze_panes = "A2"
    widths = {"A": 26, "B": 42, "C": 12, "D": 28}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


def _read_settings_sheet(ws: Worksheet) -> Settings:
    settings = Settings()
    key_col = None
    header_row = None
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        for cell in row:
            if cell.value == "Ключ":
                header_row = cell.row
                key_col = cell.column
                break
        if header_row:
            break
    if header_row is None:
        return settings

    value_col = key_col + 2
    for row in ws.iter_rows(min_row=header_row + 1, max_row=ws.max_row):
        key = row[key_col - 1].value
        if key in ALL_SETTINGS_KEYS:
            raw = row[value_col - 1].value
            settings.set_raw(key, str(raw))
    return settings


def _write_players_sheet(wb: openpyxl.Workbook, players: list[Player]) -> None:
    ws = wb.create_sheet("Игроки")
    _style_header(ws, 1, PLAYERS_HEADERS)
    for i, p in enumerate(players, start=2):
        vals = [p.game_player_id, p.telegram_id, p.name, p.money, p.position, p.status.value, p.is_admin]
        for col, v in enumerate(vals, start=1):
            c = ws.cell(row=i, column=col, value=v)
            c.border = BORDER
            if col == 2:
                c.fill = EDITABLE_FILL
    ws.freeze_panes = "A2"
    widths = {"A": 12, "B": 14, "C": 18, "D": 10, "E": 10, "F": 12, "G": 14}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


def _read_players_sheet(ws: Worksheet) -> tuple[dict[str, Player], list[str]]:
    players: dict[str, Player] = {}
    order: list[str] = []
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
        if not row or not row[0]:
            continue
        pid, tg_id, name, money, position, status, is_admin = row[:7]
        status_enum = PlayerStatus.BANKRUPT if str(status).strip() == PlayerStatus.BANKRUPT.value else PlayerStatus.ACTIVE
        players[pid] = Player(
            game_player_id=pid,
            telegram_id=int(tg_id),
            name=str(name),
            money=int(money),
            position=int(position),
            status=status_enum,
            is_admin=str(is_admin).strip().upper() in ("TRUE", "1"),
        )
        order.append(pid)
    return players, order


def _write_properties_sheet(wb: openpyxl.Workbook, properties: dict[int, PropertyState]) -> None:
    ws = wb.create_sheet("Собственность")
    _style_header(ws, 1, PROPERTIES_HEADERS)
    row = 2
    for c in board.BOARD:
        if not c.is_purchasable:
            continue
        prop = properties.get(c.index, PropertyState(cell_index=c.index))
        owner_display = prop.owner or "Банк"
        vals = [c.index, c.name, c.group, c.price, owner_display, prop.mortgaged, prop.development]
        for col, v in enumerate(vals, start=1):
            cell = ws.cell(row=row, column=col, value=v)
            cell.border = BORDER
        row += 1
    ws.freeze_panes = "A2"
    widths = {"A": 10, "B": 24, "C": 16, "D": 10, "E": 12, "F": 10, "G": 14}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


def _read_properties_sheet(ws: Worksheet) -> dict[int, PropertyState]:
    properties: dict[int, PropertyState] = {}
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
        if not row or row[0] is None:
            continue
        cell_id, _name, _group, _price, owner, mortgaged, dev = row[:7]
        owner_id = None if str(owner).strip() == "Банк" else str(owner).strip()
        properties[int(cell_id)] = PropertyState(
            cell_index=int(cell_id),
            owner=owner_id,
            mortgaged=bool(mortgaged) if not isinstance(mortgaged, str) else mortgaged.strip().upper() in ("TRUE", "1"),
            development=int(dev) if dev is not None else 0,
        )
    return properties


META_HEADERS = ["Ключ", "Значение"]


def _write_meta_sheet(wb: openpyxl.Workbook, game: GameState) -> None:
    """Точка возобновления партии (пауза/reload_state) — фаза, чей ход, что сейчас закреплено."""
    ws = wb.create_sheet("Статус")
    _style_header(ws, 1, META_HEADERS)
    rows = [
        ("phase", game.phase.name),
        ("turn_index", game.turn_index),
        ("pinned_turn_message_id", game.pinned_turn_message_id or ""),
    ]
    for i, (key, value) in enumerate(rows, start=2):
        ws.cell(row=i, column=1, value=key).font = Font(name="Consolas", size=10)
        ws.cell(row=i, column=2, value=value)
        for col in range(1, 3):
            ws.cell(row=i, column=col).border = BORDER
    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 20


def _read_meta_sheet(ws: Worksheet) -> dict[str, object]:
    meta: dict[str, object] = {}
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
        if not row or row[0] is None:
            continue
        meta[str(row[0])] = row[1]
    return meta


def _add_missing_settings_rows(path: Path) -> bool:
    """Дописывает в конец листа «Настройки» строки для ключей из ALL_SETTINGS_KEYS, которых там
    ещё нет (например, когда в коде завели новую настройку уже после того, как файл был создан
    и, возможно, вручную отредактирован админом). Существующие строки и форматирование не
    трогает — только добавляет то, чего не хватает, со значением по умолчанию из Settings()."""
    wb = openpyxl.load_workbook(path)
    if "Настройки" not in wb.sheetnames:
        return False
    ws = wb["Настройки"]

    header_row = None
    key_col = None
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        for cell in row:
            if cell.value == "Ключ":
                header_row, key_col = cell.row, cell.column
                break
        if header_row:
            break
    if header_row is None:
        return False

    existing_keys = set()
    for row in ws.iter_rows(min_row=header_row + 1, max_row=ws.max_row):
        key = row[key_col - 1].value
        if key:
            existing_keys.add(str(key))

    missing = [k for k in ALL_SETTINGS_KEYS if k not in existing_keys]
    if not missing:
        return False

    defaults = Settings()
    next_row = ws.max_row + 1
    for key in missing:
        ws.cell(row=next_row, column=key_col, value=key).font = Font(name="Consolas", size=10)
        ws.cell(row=next_row, column=key_col + 1, value=SETTINGS_LABELS[key])
        value_cell = ws.cell(row=next_row, column=key_col + 2, value=defaults.display_value(key))
        value_cell.fill = EDITABLE_FILL
        value_cell.font = Font(name=FONT_NAME, bold=True)
        ws.cell(
            row=next_row, column=key_col + 3,
            value="TRUE/FALSE" if key == "auction_enabled" else "число, либо inf/off",
        )
        for col in range(key_col, key_col + 4):
            ws.cell(row=next_row, column=col).border = BORDER
        next_row += 1

    wb.save(path)
    return True


def ensure_default_template() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DEFAULT_TEMPLATE_PATH.exists():
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        _write_settings_sheet(wb, Settings())
        wb.save(DEFAULT_TEMPLATE_PATH)
    else:
        _add_missing_settings_rows(DEFAULT_TEMPLATE_PATH)
    return DEFAULT_TEMPLATE_PATH


def load_default_settings() -> Settings:
    ensure_default_template()
    wb = openpyxl.load_workbook(DEFAULT_TEMPLATE_PATH)
    return _read_settings_sheet(wb["Настройки"])


def create_game_file(path: Path, settings: Settings) -> None:
    """Файл партии — копия default.xlsx с добавленными листами Игроки/Собственность (п.10)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _write_settings_sheet(wb, settings)
    _write_players_sheet(wb, [])
    _write_properties_sheet(wb, {})
    wb.save(path)


def save_game(game: GameState) -> None:
    """Снэпшот после каждого меняющего состояние действия — файл перезаписывается целиком (п.10)."""
    path = Path(game.file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _write_settings_sheet(wb, game.settings)
    _write_players_sheet(wb, [game.players[pid] for pid in game.player_order])
    _write_properties_sheet(wb, game.properties)
    _write_meta_sheet(wb, game)
    wb.save(path)


def load_game(path: Path, chat_id: int, game_id: str, group_title: str) -> GameState:
    """Перечитать партию с диска — используется /reload_state и /resume (п.10, п.11)."""
    wb = openpyxl.load_workbook(path)
    settings = _read_settings_sheet(wb["Настройки"])
    players, order = _read_players_sheet(wb["Игроки"])
    properties = _read_properties_sheet(wb["Собственность"])

    admin_telegram_id = next((p.telegram_id for p in players.values() if p.is_admin), None)
    phase = GamePhase.IN_PROGRESS if players else GamePhase.SETUP
    turn_index = 0
    pinned_turn_message_id = None
    if "Статус" in wb.sheetnames:
        meta = _read_meta_sheet(wb["Статус"])
        phase_name = meta.get("phase")
        if phase_name:
            try:
                phase = GamePhase[str(phase_name)]
            except KeyError:
                pass
        try:
            turn_index = int(meta.get("turn_index") or 0)
        except (TypeError, ValueError):
            turn_index = 0
        raw_pin = meta.get("pinned_turn_message_id")
        pinned_turn_message_id = int(raw_pin) if raw_pin not in (None, "") else None

    return GameState(
        chat_id=chat_id,
        game_id=game_id,
        group_title=group_title,
        file_path=str(path),
        settings=settings,
        players=players,
        player_order=order,
        properties=properties,
        phase=phase,
        turn_index=turn_index,
        admin_telegram_id=admin_telegram_id,
        pinned_turn_message_id=pinned_turn_message_id,
    )


# ---------------------------------------------------------------------------
# Реестр партий (data/games_registry.xlsx): chat_id -> последняя партия этого чата,
# нужен чтобы /newgame и /resume знали, что можно продолжить (спец.: пауза/продолжение).
# ---------------------------------------------------------------------------

REGISTRY_HEADERS = ["chat_id", "game_id", "group_title", "file_path", "admin_telegram_id", "status", "updated_at"]


@dataclass
class RegistryEntry:
    chat_id: int
    game_id: str
    group_title: str
    file_path: str
    admin_telegram_id: int | None
    status: str
    updated_at: str


def _load_registry() -> dict[int, RegistryEntry]:
    if not REGISTRY_PATH.exists():
        return {}
    wb = openpyxl.load_workbook(REGISTRY_PATH)
    ws = wb["Партии"]
    entries: dict[int, RegistryEntry] = {}
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
        if not row or row[0] is None:
            continue
        chat_id, game_id, group_title, file_path, admin_id, status, updated_at = row[:7]
        entries[int(chat_id)] = RegistryEntry(
            chat_id=int(chat_id),
            game_id=str(game_id),
            group_title=str(group_title),
            file_path=str(file_path),
            admin_telegram_id=int(admin_id) if admin_id not in (None, "") else None,
            status=str(status),
            updated_at=str(updated_at),
        )
    return entries


def _save_registry(entries: dict[int, RegistryEntry]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("Партии")
    _style_header(ws, 1, REGISTRY_HEADERS)
    for i, entry in enumerate(sorted(entries.values(), key=lambda e: e.chat_id), start=2):
        vals = [
            entry.chat_id, entry.game_id, entry.group_title, entry.file_path,
            entry.admin_telegram_id, entry.status, entry.updated_at,
        ]
        for col, v in enumerate(vals, start=1):
            ws.cell(row=i, column=col, value=v).border = BORDER
    ws.freeze_panes = "A2"
    widths = {"A": 14, "B": 10, "C": 22, "D": 42, "E": 16, "F": 12, "G": 20}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    wb.save(REGISTRY_PATH)


def upsert_registry(game: GameState, status: str) -> None:
    entries = _load_registry()
    entries[game.chat_id] = RegistryEntry(
        chat_id=game.chat_id,
        game_id=game.game_id,
        group_title=game.group_title,
        file_path=game.file_path,
        admin_telegram_id=game.admin_telegram_id,
        status=status,
        updated_at=datetime.now().isoformat(timespec="seconds"),
    )
    _save_registry(entries)


def set_registry_status(chat_id: int, status: str) -> None:
    entries = _load_registry()
    entry = entries.get(chat_id)
    if entry is None:
        return
    entry.status = status
    entry.updated_at = datetime.now().isoformat(timespec="seconds")
    _save_registry(entries)


def get_registry_entry(chat_id: int) -> RegistryEntry | None:
    return _load_registry().get(chat_id)
