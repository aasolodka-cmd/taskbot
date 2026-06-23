from PIL import Image, ImageDraw, ImageFont
import io
import os

BURGUNDY = (94, 22, 38)
WHITE    = (255, 255, 255)
LIGHT    = (200, 160, 170)

BANNER_W = 900
BANNER_H = 280

# Шрифт лежит рядом с кодом — всегда найдётся
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(BASE_DIR, "font.ttf")

def _get_font(size: int):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()

def _make_banner(title: str, subtitle: str = "") -> io.BytesIO:
    img = Image.new("RGB", (BANNER_W, BANNER_H), BURGUNDY)
    draw = ImageDraw.Draw(img)

    # Левая полоса
    draw.rectangle([0, 0, 8, BANNER_H], fill=LIGHT)
    # Нижняя полоса
    draw.rectangle([0, BANNER_H - 6, BANNER_W, BANNER_H], fill=LIGHT)

    font_title = _get_font(80)
    font_sub   = _get_font(34)

    bbox = draw.textbbox((0, 0), title, font=font_title)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    title_y = (BANNER_H - th) // 2 - (24 if subtitle else 0)
    draw.text(((BANNER_W - tw) // 2, title_y), title, font=font_title, fill=WHITE)

    if subtitle:
        bbox2 = draw.textbbox((0, 0), subtitle, font=font_sub)
        sw = bbox2[2] - bbox2[0]
        draw.text(((BANNER_W - sw) // 2, title_y + th + 14),
                  subtitle, font=font_sub, fill=LIGHT)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def banner_morning()  -> io.BytesIO: return _make_banner("ЗАДАЧИ НА ДЕНЬ")
def banner_reminder() -> io.BytesIO: return _make_banner("НАПОМИНАНИЕ", "дедлайн через час")
def banner_report()   -> io.BytesIO: return _make_banner("ОТЧЁТ", "итоги дня")

def banner_new_task() -> io.BytesIO: return _make_banner("НОВАЯ ЗАДАЧА")

def banner_call() -> io.BytesIO: return _make_banner("ПЛАНЁРКА")
