"""Generate the images GitHub shows for the project.

    python tools/make_brand.py

Writes two things to ``docs/brand``:

* ``avatar.png`` - a 512px square for the account avatar, which is the icon
  GitHub puts next to the repository name. It is the same reticle as the app
  icon, drawn larger: at avatar size the mark can breathe, so the ring is a
  little finer than the 16px taskbar version.
* ``social-preview.png`` - the 1280x640 card that appears when the repository
  is linked anywhere: Discord, Slack, Twitter, a Google result.

Both are drawn here rather than exported from a design tool so they can be
regenerated after a palette change, and so nothing about the project's look
lives somewhere only one person can open.

The wordmark uses the project's own font. Barlow ships as woff2 for the web
UI, which PIL cannot read, so this converts it in memory with fontTools -
a dev-time dependency, never needed at runtime:

    pip install fonttools brotli
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "web" / "assets" / "fonts"
OUT_DIR = ROOT / "docs" / "brand"

# Straight from web/styles.css, so the images and the app cannot drift.
BG = (13, 15, 20, 255)
RAISED = (21, 25, 34, 255)
TEXT = (230, 233, 239, 255)
DIM = (152, 161, 179, 255)
ACCENT = (255, 70, 85, 255)
ALLY = (47, 179, 164, 255)

TAGLINE = "Know who you're playing with, before the match starts."
FOOTNOTE = "Read-only  ·  Runs on your PC  ·  No ads, no subscription"


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Barlow, converted from the woff2 the web UI already ships."""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:  # pragma: no cover - dev tool
        raise SystemExit(
            "This needs fontTools to read the bundled fonts:\n"
            "    pip install fonttools brotli"
        ) from None

    source = FONT_DIR / f"{name}.woff2"
    if not source.exists():
        raise SystemExit(f"Missing {source}")
    font = TTFont(source)
    font.flavor = None
    buffer = io.BytesIO()
    font.save(buffer)
    buffer.seek(0)
    return ImageFont.truetype(buffer, size)


def fit_font(art, text: str, name: str, size: int, limit: float):
    """The largest size at or below ``size`` that keeps ``text`` inside ``limit``.

    The tagline is the one piece of copy here that changes, and a social card
    with a sentence running off the edge looks worse than one set slightly
    smaller.
    """
    while size > 12:
        font = load_font(name, size)
        if art.textlength(text, font=font) <= limit:
            return font
        size -= 2
    return load_font(name, size)


def reticle(edge: int, ring_scale: float = 0.30, stroke: float = 0.055) -> Image.Image:
    """The app's mark, on a transparent square, drawn at 4x and downsampled."""
    scale = 4
    size = edge * scale
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    art = ImageDraw.Draw(image)

    center = size / 2
    radius = size * ring_scale
    width = max(scale, int(size * stroke))
    art.ellipse(
        [center - radius, center - radius, center + radius, center + radius],
        outline=ACCENT, width=width,
    )
    tick_outer, tick_inner = size * 0.44, size * 0.34
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        art.line(
            [center + dx * tick_inner, center + dy * tick_inner,
             center + dx * tick_outer, center + dy * tick_outer],
            fill=TEXT, width=width,
        )
    dot = size * 0.055
    art.ellipse([center - dot, center - dot, center + dot, center + dot], fill=TEXT)
    return image.resize((edge, edge), Image.LANCZOS)


def glow(size: tuple[int, int], color: tuple[int, int, int, int],
         center: tuple[float, float], radius: float) -> Image.Image:
    """A soft radial wash, the same one that sits behind the scoreboard."""
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    steps = 44
    art = ImageDraw.Draw(layer)
    for step in range(steps, 0, -1):
        fraction = step / steps
        alpha = int(color[3] * (1 - fraction) ** 2)
        if alpha <= 0:
            continue
        spread = radius * fraction
        art.ellipse(
            [center[0] - spread, center[1] - spread,
             center[0] + spread, center[1] + spread],
            fill=(*color[:3], alpha),
        )
    return layer


def make_avatar(edge: int = 512) -> Image.Image:
    image = Image.new("RGBA", (edge, edge), BG)
    image.alpha_composite(
        glow((edge, edge), (*ACCENT[:3], 70), (edge * 0.5, edge * 0.12), edge * 0.85)
    )
    mark = reticle(edge, ring_scale=0.31, stroke=0.05)
    image.alpha_composite(mark)

    # Round the corners: GitHub shows user avatars as circles and
    # organizations as rounded squares, and this reads well either way.
    mask = Image.new("L", (edge, edge), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, edge - 1, edge - 1], radius=int(edge * 0.18), fill=255
    )
    rounded = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    rounded.paste(image, (0, 0), mask)
    return rounded


def make_social(width: int = 1280, height: int = 640) -> Image.Image:
    image = Image.new("RGBA", (width, height), BG)
    image.alpha_composite(glow((width, height), (*ACCENT[:3], 64), (width * 0.12, -height * 0.1), width * 0.72))
    image.alpha_composite(glow((width, height), (*ALLY[:3], 40), (width * 0.95, height * 1.05), width * 0.55))

    art = ImageDraw.Draw(image)

    mark_edge = 168
    mark = reticle(mark_edge, ring_scale=0.32, stroke=0.05)
    mark_x, mark_y = 96, height // 2 - mark_edge // 2 - 40
    image.alpha_composite(mark, (mark_x, mark_y))

    display = load_font("barlow-condensed-700", 132)
    small = load_font("barlow-500", 28)

    text_x = mark_x + mark_edge + 44
    baseline = mark_y + 16
    # Whatever is left between the wordmark and the right margin.
    body = fit_font(art, TAGLINE, "barlow-500", 40, width - 96 - (text_x + 4))

    # The wordmark, two-tone exactly like the app's header.
    art.text((text_x, baseline), "SPIKE", font=display, fill=ACCENT)
    spike_width = art.textlength("SPIKE", font=display)
    art.text((text_x + spike_width, baseline), "SIGHT", font=display, fill=TEXT)

    art.text((text_x + 4, baseline + 150), TAGLINE, font=body, fill=DIM)

    # A rule and the promises, along the bottom.
    rule_y = height - 132
    art.line([(96, rule_y), (width - 96, rule_y)], fill=(*TEXT[:3], 28), width=2)
    art.text((96, rule_y + 34), FOOTNOTE, font=small, fill=DIM)

    badge = "VALORANT"
    badge_font = load_font("barlow-condensed-700", 30)
    badge_width = art.textlength(badge, font=badge_font)
    art.rounded_rectangle(
        [width - 96 - badge_width - 28, rule_y + 26,
         width - 96, rule_y + 72],
        radius=6, fill=RAISED, outline=(*ACCENT[:3], 90), width=2,
    )
    art.text((width - 96 - badge_width - 14, rule_y + 31), badge,
             font=badge_font, fill=DIM)
    return image


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    avatar = OUT_DIR / "avatar.png"
    make_avatar().save(avatar, optimize=True)

    social = OUT_DIR / "social-preview.png"
    make_social().convert("RGB").save(social, optimize=True)

    for path in (avatar, social):
        with Image.open(path) as image:
            print(f"Wrote {path.relative_to(ROOT)} "
                  f"({image.width}x{image.height}, {path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
