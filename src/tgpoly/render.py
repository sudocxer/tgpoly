"""Рендер поля (спец. п.2). Статичный фон — assets/board_template.png (2048×2048, сетка 11×11),
поверх него на лету рисуются дома/отели и фишки-аватарки игроков. Фон можно свободно заменить
на художественный (тот же файл, тот же размер/сетка) — код от этого не зависит; если файла нет,
генерируется схематичная заглушка (см. generate_placeholder_template)."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from aiogram import Bot
from PIL import Image, ImageDraw, ImageFont

from . import board
from .models import GameState, Player

logger = logging.getLogger(__name__)

IMG_SIZE = 2048
GRID = 11

# Абсолютный путь от расположения пакета, а не от текущей рабочей директории процесса —
# иначе бот, запущенный не из корня проекта (systemd/Docker/cron), не найдёт assets/ вообще:
# TEMPLATE_PATH.exists() всегда False, он молча сгенерирует заглушку не в том месте, а если
# при этом не найдётся и шрифт — заглушка выйдет с "квадратиками" вместо кириллицы.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = PROJECT_ROOT / "assets" / "board_template.png"
FONT_REGULAR_PATH = PROJECT_ROOT / "assets" / "fonts" / "DejaVuSans.ttf"
FONT_BOLD_PATH = PROJECT_ROOT / "assets" / "fonts" / "DejaVuSans-Bold.ttf"

AVATAR_SIZE = 96  # диаметр фишки-аватарки на поле
HOUSE_COLOR = (30, 90, 220)  # обычные дома — синие
HOTEL_COLOR = (200, 30, 30)  # отель — красный

GROUP_COLORS: dict[str, tuple[int, int, int]] = {
    "Фиолетовая": (148, 0, 211),
    "Голубая": (135, 206, 235),
    "Розовая": (255, 105, 180),
    "Оранжевая": (255, 140, 0),
    "Красная": (220, 20, 60),
    "Жёлтая": (255, 215, 0),
    "Зелёная": (34, 139, 34),
    "Тёмно-синяя": (25, 25, 112),
    "Вокзал": (105, 105, 105),
    "Коммуналка": (176, 196, 222),
}
DEFAULT_BG = (235, 235, 235)
PLAYER_COLORS = [
    (231, 76, 60), (52, 152, 219), (46, 204, 113), (241, 196, 15),
    (155, 89, 182), (26, 188, 156), (230, 126, 34), (149, 165, 166),
]

_avatar_cache: dict[int, Image.Image] = {}


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """load_default() не умеет в кириллицу — используем шрифт из assets/fonts (см. LICENSE-DejaVu.txt)."""
    path = FONT_BOLD_PATH if bold else FONT_REGULAR_PATH
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        logger.warning("font file not found at %s, falling back to the built-in font", path)
        return ImageFont.load_default(size=size)


# ---------------------------------------------------------------------------
# Геометрия сетки 11x11 (не зависит от того, чем именно залит фон)
# ---------------------------------------------------------------------------

def _grid_bounds() -> list[int]:
    """12 границ по каждой оси — считаем явно, чтобы не накапливалась ошибка округления."""
    return [round(i * IMG_SIZE / GRID) for i in range(GRID + 1)]


def _index_to_grid(i: int) -> tuple[int, int]:
    i %= board.BOARD_SIZE
    if i <= 10:
        return GRID - 1, GRID - 1 - i
    if i <= 20:
        return GRID - 1 - (i - 10), 0
    if i <= 30:
        return 0, i - 20
    return i - 30, GRID - 1


def _cell_rect(index: int) -> tuple[int, int, int, int]:
    bounds = _grid_bounds()
    row, col = _index_to_grid(index)
    return bounds[col], bounds[row], bounds[col + 1], bounds[row + 1]


# ---------------------------------------------------------------------------
# Фон: assets/board_template.png, либо схематичная заглушка (см. докстринг модуля)
# ---------------------------------------------------------------------------

def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if not cur or draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def generate_placeholder_template() -> Image.Image:
    """Схематичная заглушка фона, пока assets/board_template.png не заменят на художественный
    вариант. Раскладка (сетка 11x11, порядок клеток) должна сохраниться и в новом шаблоне —
    иначе разъедутся координаты домов/фишек, которые считаются по этой же сетке."""
    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), "white")
    draw = ImageDraw.Draw(img)
    name_font = _font(15)
    price_font = _font(13, bold=True)
    title_font = _font(52, bold=True)

    bounds = _grid_bounds()
    for c in board.BOARD:
        row, col = _index_to_grid(c.index)
        x0, y0, x1, y1 = bounds[col], bounds[row], bounds[col + 1], bounds[row + 1]
        color = GROUP_COLORS.get(c.group or "", DEFAULT_BG)
        draw.rectangle([x0, y0, x1, y1], fill=color, outline=(60, 60, 60), width=2)
        text_color = (0, 0, 0) if sum(color) > 380 else (255, 255, 255)
        pad = 8
        for i, line in enumerate(_wrap_text(draw, c.name, name_font, (x1 - x0) - pad * 2)[:3]):
            draw.text((x0 + pad, y0 + pad + i * 18), line, fill=text_color, font=name_font)
        if c.price:
            draw.text((x0 + pad, y1 - pad - 14), str(c.price), fill=text_color, font=price_font)

    c0, c1 = bounds[1], bounds[GRID - 1]
    draw.rectangle([c0, c0, c1, c1], fill=(245, 245, 240), outline=(60, 60, 60), width=2)
    title = "МОНОПОЛИЯ"
    tw = draw.textlength(title, font=title_font)
    draw.text(((c0 + c1 - tw) / 2, (c0 + c1) / 2 - 26), title, fill=(120, 120, 120), font=title_font)

    return img


def ensure_board_template() -> Path:
    TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not TEMPLATE_PATH.exists():
        generate_placeholder_template().save(TEMPLATE_PATH)
    return TEMPLATE_PATH


def _load_background() -> Image.Image:
    if TEMPLATE_PATH.exists():
        img = Image.open(TEMPLATE_PATH).convert("RGB")
        if img.size != (IMG_SIZE, IMG_SIZE):
            img = img.resize((IMG_SIZE, IMG_SIZE))
        return img
    return generate_placeholder_template()


# ---------------------------------------------------------------------------
# Дома / отель — пятиугольник домика (боковые стороны вертикальны, значит параллельны друг
# другу), крыша — два ската к вершине. Обычные дома синие, отель — один, крупный, красный.
# ---------------------------------------------------------------------------

def _draw_house(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, color: tuple[int, int, int]) -> None:
    roof_y = y0 + (y1 - y0) * 0.42
    points = [
        (x0, y1), (x1, y1),                          # низ — параллельные боковые стороны идут вверх отсюда
        (x1, roof_y), ((x0 + x1) / 2, y0), (x0, roof_y),  # два ската крыши к вершине
    ]
    draw.polygon(points, fill=color, outline=(0, 0, 0))


def house_zone(rect: tuple[int, int, int, int]) -> tuple[float, float]:
    """Полоса по высоте клетки под дома/отель — ниже зоны аватарок, выше подписи цены."""
    x0, y0, x1, y1 = rect
    cell_h = y1 - y0
    return y0 + cell_h * 0.70, y0 + cell_h * 0.88


def _draw_development(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], development: int) -> None:
    if development <= 0:
        return
    x0, y0, x1, y1 = rect
    cell_w = x1 - x0
    zone_top, zone_bottom = house_zone(rect)
    icon = zone_bottom - zone_top  # квадратная подложка под пиктограмму — одного размера что домик, что отель

    if development >= 5:
        cx = (x0 + x1) / 2
        _draw_house(draw, cx - icon / 2, zone_top, cx + icon / 2, zone_bottom, HOTEL_COLOR)
        return

    n = development
    gap = cell_w * 0.03
    total_w = icon * n + gap * (n - 1)
    start_x = x0 + (cell_w - total_w) / 2
    for i in range(n):
        hx0 = start_x + i * (icon + gap)
        _draw_house(draw, hx0, zone_top, hx0 + icon, zone_bottom, HOUSE_COLOR)


# ---------------------------------------------------------------------------
# Аватарки игроков — реальное фото профиля Telegram (круглая фишка), с фолбэком на цветной
# бейдж game_player_id, если фото нет/не получилось скачать. Кэшируется в памяти процесса
# по telegram_id, чтобы не дёргать Telegram API на каждый рендер поля.
# ---------------------------------------------------------------------------

def _circular_avatar(src: Image.Image, size: int) -> Image.Image:
    src = src.convert("RGB")
    side = min(src.size)
    src = src.crop(((src.width - side) // 2, (src.height - side) // 2, (src.width + side) // 2, (src.height + side) // 2))
    src = src.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size))
    out.paste(src, (0, 0), mask)
    ImageDraw.Draw(out).ellipse((1, 1, size - 2, size - 2), outline=(0, 0, 0), width=3)
    return out


def _fallback_avatar(player_id: str, size: int) -> Image.Image:
    color_idx = int(player_id[1:]) - 1 if player_id[1:].isdigit() else 0
    color = PLAYER_COLORS[color_idx % len(PLAYER_COLORS)]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((1, 1, size - 2, size - 2), fill=color, outline=(0, 0, 0), width=3)
    font = _font(max(10, int(size * 0.45)), bold=True)
    text = player_id[1:] or "?"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), text, fill=(255, 255, 255), font=font)
    return img


async def _get_avatar(bot: Bot, player: Player, size: int) -> Image.Image:
    cached = _avatar_cache.get(player.telegram_id)
    if cached is not None and cached.size == (size, size):
        return cached

    avatar: Image.Image | None = None
    try:
        photos = await bot.get_user_profile_photos(player.telegram_id, limit=1)
        if photos.total_count > 0:
            largest = photos.photos[0][-1]
            buf = await bot.download(largest)
            if buf is not None:
                avatar = _circular_avatar(Image.open(buf), size)
    except Exception:
        logger.debug("failed to fetch avatar for telegram_id=%s", player.telegram_id, exc_info=True)

    if avatar is None:
        avatar = _fallback_avatar(player.game_player_id, size)
    _avatar_cache[player.telegram_id] = avatar
    return avatar


# ---------------------------------------------------------------------------
# Сборка кадра
# ---------------------------------------------------------------------------

async def render_board(bot: Bot, game: GameState) -> bytes:
    img = _load_background()
    draw = ImageDraw.Draw(img)

    for cell_index, prop in game.properties.items():
        if prop.development:
            _draw_development(draw, _cell_rect(cell_index), prop.development)

    occupants: dict[int, list[str]] = {}
    for pid in game.player_order:
        p = game.players[pid]
        if p.is_active:
            occupants.setdefault(p.position, []).append(pid)

    for pos, pids in occupants.items():
        rect = _cell_rect(pos)
        x0, y0, x1, y1 = rect
        cell_w = x1 - x0
        avatar_top = y0 + (y1 - y0) * 0.36  # ниже названия клетки (до 3 строк), см. generate_placeholder_template
        house_top, _ = house_zone(rect)
        size = max(18, min(AVATAR_SIZE, int(cell_w * 0.30)))
        gap = 4
        for i, pid in enumerate(pids):
            col_i, row_i = i % 2, i // 2
            ax = x0 + gap + col_i * (size + gap)
            ay = avatar_top + row_i * (size + gap)
            if ay + size > house_top:  # много игроков на одной клетке — подвинуть повыше, чем толкаться с домами
                ay = max(y0 + gap, house_top - size)
            avatar = await _get_avatar(bot, game.players[pid], size)
            img.paste(avatar, (int(ax), int(ay)), avatar)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf.read()
