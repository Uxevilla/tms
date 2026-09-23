"""Genera los iconos PWA del TMS (PNG 192/512/180)."""
from PIL import Image, ImageDraw, ImageFont

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
EMOJI = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
OUT = "/root/tms-trimble/frontend"


def gradient_bg(size):
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    top, bottom = (37, 99, 235), (29, 78, 216)  # #2563eb -> #1d4ed8
    for y in range(size):
        t = y / (size - 1)
        d.line(
            [(0, y), (size, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )
    return img


def make_icon(size, name):
    img = gradient_bg(size)
    d = ImageDraw.Draw(img)

    # intento: emoji de camión arriba (opcional, si el color font renderiza)
    emoji_ok = False
    try:
        ef = ImageFont.truetype(EMOJI, int(size * 0.34), embedded_color=True)
        ebox = d.textbbox((0, 0), "\U0001F69B", font=ef)
        ew, eh = ebox[2] - ebox[0], ebox[3] - ebox[1]
        d.text(((size - ew) / 2 - ebox[0], size * 0.16 - ebox[1]), "\U0001F69B", font=ef, embedded_color=True)
        emoji_ok = True
    except Exception as e:
        print(f"  (emoji no disponible: {e})")

    # texto TMS
    tf = ImageFont.truetype(FONT_BOLD, int(size * (0.30 if emoji_ok else 0.42)))
    tbox = d.textbbox((0, 0), "TMS", font=tf)
    tw, th = tbox[2] - tbox[0], tbox[3] - tbox[1]
    ty = size * 0.52 if emoji_ok else (size - th) / 2
    d.text(((size - tw) / 2 - tbox[0], ty - tbox[1]), "TMS", fill=(255, 255, 255), font=tf)

    img.save(f"{OUT}/{name}")
    print(f"{name} -> {size}x{size} (emoji={'sí' if emoji_ok else 'no'})")


make_icon(512, "icon-512.png")
make_icon(192, "icon-192.png")
make_icon(180, "apple-touch-icon.png")
print("listo")
