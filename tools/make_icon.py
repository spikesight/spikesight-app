"""Generate the application icon.

A scope reticle in the app's accent red on a near-black tile. Deliberately not
modeled on any Riot mark - this is SpikeSight's own icon, and it has to stay
legible when Windows renders it at 16px in the taskbar.

    python tools/make_icon.py

Writes a multi-resolution ``spikesight.ico`` used by the packaged .exe, plus a
PNG for the browser tab.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ICO_PATH = ROOT / "packaging" / "spikesight.ico"
PNG_PATH = ROOT / "web" / "assets" / "icon.png"

BG = (13, 15, 20, 255)
RING = (230, 233, 239, 255)
ACCENT = (255, 70, 85, 255)
SIZES = [256, 128, 64, 48, 32, 16]


def draw(size: int) -> Image.Image:
    # Draw at 4x and downsample: cheap antialiasing that keeps the 16px tile
    # from turning into mush.
    scale = 4
    edge = size * scale
    image = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    art = ImageDraw.Draw(image)

    radius = int(edge * 0.18)
    art.rounded_rectangle([0, 0, edge - 1, edge - 1], radius=radius, fill=BG)

    center = edge / 2
    ring_radius = edge * 0.30
    ring_width = max(scale, int(edge * 0.065))
    art.ellipse(
        [center - ring_radius, center - ring_radius,
         center + ring_radius, center + ring_radius],
        outline=ACCENT, width=ring_width,
    )

    # Reticle ticks, top/bottom/left/right.
    tick_outer = edge * 0.44
    tick_inner = edge * 0.34
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        art.line(
            [center + dx * tick_inner, center + dy * tick_inner,
             center + dx * tick_outer, center + dy * tick_outer],
            fill=RING, width=ring_width,
        )

    # A dot in the middle of the reticle. Deliberately no diagonal stroke -
    # a line through a circle reads as a "prohibited" sign, which is the wrong
    # idea entirely for a scouting tool.
    art.ellipse(
        [center - edge * 0.062, center - edge * 0.062,
         center + edge * 0.062, center + edge * 0.062],
        fill=RING,
    )

    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    ICO_PATH.parent.mkdir(parents=True, exist_ok=True)
    PNG_PATH.parent.mkdir(parents=True, exist_ok=True)

    frames = [draw(size) for size in SIZES]
    frames[0].save(ICO_PATH, format="ICO",
                   sizes=[(s, s) for s in SIZES])
    frames[0].save(PNG_PATH, format="PNG", optimize=True)

    print(f"Wrote {ICO_PATH} ({ICO_PATH.stat().st_size / 1024:.1f} KB)")
    print(f"Wrote {PNG_PATH} ({PNG_PATH.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
