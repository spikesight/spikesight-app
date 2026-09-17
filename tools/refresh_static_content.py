"""Regenerate the bundled agent and map name tables.

SpikeSight ships ``spikesight/data/agents.json`` and ``maps.json`` so the running
app makes **zero** third-party requests: it only ever talks to 127.0.0.1 and to
Riot's own hosts. Riot's ``content-service`` used to carry these names but now
returns only seasons and events, so the tables are baked in instead.

Run this by hand after a new agent or map ships:

    python tools/refresh_static_content.py --api-key RGAPI-...   (official)
    python tools/refresh_static_content.py                       (community)

With ``--api-key`` the data comes from Riot's own VAL-CONTENT endpoint
(developer.riotgames.com). Without one it falls back to valorant-api.com, a
public community mirror of the game's static assets. Either way this is a
build-time step: nothing here runs while you are in a match.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "spikesight" / "data"
ICON_DIR = ROOT / "web" / "assets" / "agents"
FONT_DIR = ROOT / "web" / "assets" / "fonts"
ICON_SIZE = 72

# Barlow (body) and Barlow Condensed (labels and headings), both OFL. Bundled
# locally so the running page never asks Google for a font.
FONT_SPECS = [
    ("Barlow", "wght@400;500;600;700", "barlow"),
    ("Barlow+Condensed", "wght@500;600;700", "barlow-condensed"),
]
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

OFFICIAL_ROUTES = ["na", "eu", "ap", "kr", "br", "latam"]


def _norm(value: str) -> str:
    return value.replace("-", "").lower()


def from_official(api_key: str, route: str) -> tuple[dict, dict]:
    url = f"https://{route}.api.riotgames.com/val/content/v1/contents"
    response = httpx.get(
        url, params={"locale": "en-US"}, headers={"X-Riot-Token": api_key}, timeout=20
    )
    response.raise_for_status()
    payload = response.json()

    agents = {
        _norm(entry["id"]): entry["name"]
        for entry in payload.get("characters", [])
        if entry.get("id") and entry.get("name")
    }
    maps = {
        "uuid:" + _norm(entry["id"]): entry["name"]
        for entry in payload.get("maps", [])
        if entry.get("id") and entry.get("name")
    }
    return agents, maps


def from_community() -> tuple[dict, dict]:
    with httpx.Client(timeout=30) as client:
        agents_payload = client.get(
            "https://valorant-api.com/v1/agents",
            params={"isPlayableCharacter": "true", "language": "en-US"},
        ).raise_for_status().json()
        maps_payload = client.get(
            "https://valorant-api.com/v1/maps", params={"language": "en-US"}
        ).raise_for_status().json()

    agents = {
        _norm(entry["uuid"]): entry["displayName"]
        for entry in agents_payload["data"]
        if entry.get("uuid") and entry.get("displayName")
    }

    maps: dict[str, str] = {}
    for entry in maps_payload["data"]:
        name = entry.get("displayName")
        if not name:
            continue
        # The live match payload identifies maps by asset path, so that is the
        # important key; the uuid form is kept for match-details payloads.
        if entry.get("mapUrl"):
            maps[entry["mapUrl"]] = name
        if entry.get("uuid"):
            maps["uuid:" + _norm(entry["uuid"])] = name
    return agents, maps


def download_icons() -> int:
    """Fetch agent portraits and shrink them to scoreboard size.

    They are stored under web/assets so the page loads them from SpikeSight's own
    origin - the running app never talks to a third-party host.
    """
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is needed to resize icons:  pip install Pillow", file=sys.stderr)
        return 0

    import io

    with httpx.Client(timeout=60) as client:
        payload = client.get(
            "https://valorant-api.com/v1/agents",
            params={"isPlayableCharacter": "true", "language": "en-US"},
        ).raise_for_status().json()

        ICON_DIR.mkdir(parents=True, exist_ok=True)
        written = 0
        for entry in payload["data"]:
            uuid = _norm(entry.get("uuid") or "")
            url = entry.get("displayIconSmall") or entry.get("displayIcon")
            if not uuid or not url:
                continue
            blob = client.get(url).raise_for_status().content
            image = Image.open(io.BytesIO(blob)).convert("RGBA")
            # The source art is ~500 KB each; the scoreboard shows it at 30px.
            image.thumbnail((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
            image.save(ICON_DIR / f"{uuid}.png", "PNG", optimize=True)
            written += 1
    return written


def download_fonts() -> int:
    """Pull the woff2 files Google Fonts serves and store them beside the app.

    The css2 endpoint returns different formats per user agent, so a modern
    browser UA is required to get woff2 rather than ttf.
    """
    import re

    FONT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    with httpx.Client(timeout=60, headers={"User-Agent": BROWSER_UA}) as client:
        for family, axis, slug in FONT_SPECS:
            url = f"https://fonts.googleapis.com/css2?family={family}:{axis}&display=swap"
            css = client.get(url).raise_for_status().text
            blocks = re.findall(
                r"/\*\s*([\w-]+)\s*\*/\s*@font-face\s*\{(.*?)\}", css, re.S
            )
            for subset, body in blocks:
                # latin only; latin-ext and vietnamese are dead weight here.
                if subset != "latin":
                    continue
                weight = re.search(r"font-weight:\s*(\d+)", body).group(1)
                src = re.search(r"url\((https://[^)]+\.woff2)\)", body).group(1)
                target = FONT_DIR / f"{slug}-{weight}.woff2"
                target.write_bytes(client.get(src).raise_for_status().content)
                written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key", default="", help="Riot developer API key")
    parser.add_argument("--route", default="na", choices=OFFICIAL_ROUTES)
    parser.add_argument("--skip-icons", action="store_true",
                        help="Do not refresh the agent portraits")
    parser.add_argument("--skip-fonts", action="store_true",
                        help="Do not refresh the bundled fonts")
    args = parser.parse_args()

    if args.api_key:
        print(f"Fetching official VAL-CONTENT from {args.route}...")
        agents, maps = from_official(args.api_key, args.route)
        # The official payload has no asset paths, so keep any we already have.
        existing = json.loads((DATA_DIR / "maps.json").read_text(encoding="utf-8"))
        maps = {**existing, **maps}
    else:
        print("No API key given; using the community mirror (valorant-api.com)...")
        agents, maps = from_community()

    if len(agents) < 15:
        print(f"Refusing to write: only {len(agents)} agents came back.", file=sys.stderr)
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "agents.json").write_text(
        json.dumps(agents, indent=1, sort_keys=True), encoding="utf-8"
    )
    (DATA_DIR / "maps.json").write_text(
        json.dumps(maps, indent=1, sort_keys=True), encoding="utf-8"
    )
    print(f"Wrote {len(agents)} agents and {len(maps)} map keys to {DATA_DIR}")

    if not args.skip_icons:
        # Portraits only exist on the community mirror; the official content
        # API returns names and ids, not art.
        count = download_icons()
        if count:
            total = sum(p.stat().st_size for p in ICON_DIR.glob("*.png"))
            print(f"Wrote {count} agent portraits ({total / 1024:.0f} KB) to {ICON_DIR}")

    if not args.skip_fonts:
        faces = download_fonts()
        total = sum(p.stat().st_size for p in FONT_DIR.glob("*.woff2"))
        print(f"Wrote {faces} font faces ({total / 1024:.0f} KB) to {FONT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
