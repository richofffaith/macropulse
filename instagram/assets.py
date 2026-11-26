"""Generate placeholder stills for the founder-desk Reel aesthetic.

Real dual-monitor / highrise footage should replace these when available.
MacroPulse UI is mocked on the monitor 'screens' using dashboard brand colors.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from instagram.config import ACCENT, BG, HEIGHT, MUTED, TEAL, TEXT, WIDTH


def _font(size: int) -> ImageFont.ImageFont:
    for name in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def _city_skyline(width: int, height: int, seed: int = 42) -> Image.Image:
    """Night city with soft glow — base for timelapse-style pans."""
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height), (6, 10, 22))
    draw = ImageDraw.Draw(img)

    # Sky gradient
    for y in range(height):
        t = y / max(height - 1, 1)
        r = int(6 + t * 18)
        g = int(10 + t * 12)
        b = int(22 + t * 40)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    horizon = int(height * 0.42)
    # Distant haze band
    for y in range(horizon - 40, horizon + 20):
        alpha = max(0, 1 - abs(y - horizon) / 60)
        draw.line(
            [(0, y), (width, y)],
            fill=(int(40 * alpha), int(50 * alpha), int(80 * alpha)),
        )

    # Buildings
    x = 0
    while x < width:
        bw = rng.randint(28, 70)
        bh = rng.randint(int(height * 0.18), int(height * 0.48))
        top = horizon - bh
        shade = rng.randint(12, 28)
        draw.rectangle([x, top, x + bw - 2, horizon + 8], fill=(shade, shade + 4, shade + 14))
        # Windows
        for wy in range(top + 8, horizon - 4, 10):
            for wx in range(x + 4, x + bw - 6, 8):
                if rng.random() < 0.55:
                    warm = rng.choice([(255, 214, 120), (180, 210, 255), (120, 200, 180)])
                    dim = tuple(int(c * rng.uniform(0.35, 1.0)) for c in warm)
                    draw.rectangle([wx, wy, wx + 3, wy + 4], fill=dim)
        x += bw

    # Soft glow bloom
    glow = img.filter(ImageFilter.GaussianBlur(radius=6))
    img = Image.blend(img, glow, 0.35)
    return img


def _monitor_ui(w: int, h: int, label: str, stress: float = 30.5) -> Image.Image:
    """MacroPulse-ish dark UI panel for a monitor screen."""
    panel = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(panel)
    # Grid
    for gx in range(0, w, 24):
        d.line([(gx, 0), (gx, h)], fill=(18, 22, 32))
    for gy in range(0, h, 24):
        d.line([(0, gy), (w, gy)], fill=(18, 22, 32))

    d.text((16, 14), "MacroPulse", font=_font(max(18, w // 18)), fill=TEXT)
    d.text((16, 42), label, font=_font(max(12, w // 28)), fill=MUTED)

    # Stress bar
    bar_y = 80
    d.rounded_rectangle([16, bar_y, w - 16, bar_y + 18], radius=6, fill=(20, 28, 40))
    fill_w = int((w - 36) * min(max(stress / 100.0, 0.05), 1.0))
    d.rounded_rectangle([18, bar_y + 2, 18 + fill_w, bar_y + 16], radius=4, fill=TEAL)
    d.text((16, bar_y + 28), f"Stress Index  {stress:.1f}", font=_font(max(11, w // 32)), fill=MUTED)

    # Fake chart polyline
    pts = []
    base_y = h // 2 + 20
    for i in range(8):
        px = 24 + i * ((w - 48) // 7)
        py = base_y + int(28 * math.sin(i * 0.9 + stress / 20)) - i * 3
        pts.append((px, py))
    if len(pts) > 1:
        d.line(pts, fill=ACCENT, width=3)
    for px, py in pts:
        d.ellipse([px - 3, py - 3, px + 3, py + 3], fill=ACCENT)

    d.text((16, h - 36), "building quietly…", font=_font(max(11, w // 34)), fill=MUTED)
    return panel


def build_desk_frame(
    *,
    city: Image.Image,
    pan_x: float = 0.0,
    person_opacity: int = 200,
) -> Image.Image:
    """Compose one 9:16 frame: city window + dual monitors + soft silhouette."""
    frame = Image.new("RGB", (WIDTH, HEIGHT), (10, 12, 18))

    # City through floor-to-ceiling window (upper 55%)
    city_crop_w = int(WIDTH * 1.25)
    city_resized = city.resize((city_crop_w, int(HEIGHT * 0.58)), Image.Resampling.LANCZOS)
    max_pan = city_crop_w - WIDTH
    ox = int(max(0, min(max_pan, pan_x * max_pan)))
    frame.paste(city_resized.crop((ox, 0, ox + WIDTH, int(HEIGHT * 0.58))), (0, 0))

    # Window mullion
    draw = ImageDraw.Draw(frame)
    mullion = int(WIDTH * 0.52)
    draw.rectangle([mullion - 4, 0, mullion + 4, int(HEIGHT * 0.55)], fill=(30, 34, 42))
    draw.rectangle([0, int(HEIGHT * 0.55) - 6, WIDTH, int(HEIGHT * 0.55) + 2], fill=(28, 32, 40))

    # Desk surface
    desk_top = int(HEIGHT * 0.62)
    draw.rectangle([0, desk_top, WIDTH, HEIGHT], fill=(18, 16, 22))
    draw.rectangle([40, desk_top, WIDTH - 40, desk_top + 18], fill=(42, 36, 32))

    # Dual monitors
    mon_h = int(HEIGHT * 0.22)
    mon_w = int(WIDTH * 0.38)
    gap = 36
    left_x = (WIDTH - (2 * mon_w + gap)) // 2
    right_x = left_x + mon_w + gap
    mon_y = desk_top - mon_h - 10

    ui_left = _monitor_ui(mon_w - 16, mon_h - 16, "Economic Stress Radar", stress=30.5)
    ui_right = _monitor_ui(mon_w - 16, mon_h - 16, "WARN · signals", stress=22.0)

    for mx, ui in ((left_x, ui_left), (right_x, ui_right)):
        # Bezel
        draw.rounded_rectangle(
            [mx - 6, mon_y - 6, mx + mon_w + 6, mon_y + mon_h + 6],
            radius=10,
            fill=(28, 30, 36),
        )
        frame.paste(ui, (mx + 8, mon_y + 8))
        # Stand
        cx = mx + mon_w // 2
        draw.rectangle([cx - 8, mon_y + mon_h + 6, cx + 8, desk_top], fill=(50, 52, 58))
        draw.ellipse([cx - 40, desk_top - 8, cx + 40, desk_top + 10], fill=(40, 42, 48))

    # Soft person silhouette (back of head / shoulders) — placeholder for real founder footage
    sil = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sil)
    head_cx, head_cy = WIDTH // 2, int(HEIGHT * 0.78)
    sd.ellipse(
        [head_cx - 70, head_cy - 90, head_cx + 70, head_cy + 40],
        fill=(12, 12, 16, person_opacity),
    )
    sd.ellipse(
        [head_cx - 160, head_cy + 30, head_cx + 160, HEIGHT + 40],
        fill=(12, 12, 16, person_opacity),
    )
    frame = Image.alpha_composite(frame.convert("RGBA"), sil).convert("RGB")

    # Soft edge darkening
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([0, 0, WIDTH, int(HEIGHT * 0.08)], fill=(0, 0, 0, 90))
    od.rectangle([0, int(HEIGHT * 0.92), WIDTH, HEIGHT], fill=(0, 0, 0, 110))
    frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")

    # Corner watermark (soft)
    draw = ImageDraw.Draw(frame)
    draw.text((36, HEIGHT - 64), "MacroPulse · building", font=_font(22), fill=(180, 190, 210))

    return frame


def generate_placeholder_city(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "city_placeholder.png"
    if not path.exists():
        _city_skyline(int(WIDTH * 1.3), int(HEIGHT * 0.6)).save(path, "PNG")
    return path
