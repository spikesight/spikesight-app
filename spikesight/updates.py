"""Checking whether a newer SpikeSight has been released.

This is the one thing in SpikeSight that talks to a server that is not Riot's,
so it is worth being precise about what it does:

* It asks ``api.github.com`` for the latest release of the public repository.
  That is a plain GET of a page anybody can open in a browser.
* It sends no account, no match data, no identifier of any kind. The request
  carries a ``User-Agent`` of ``SpikeSight/<version>`` because GitHub's API
  requires one, and nothing else. GitHub sees an IP address, the same as it
  would if you opened the releases page yourself.
* It is a *check*. Nothing is downloaded, and nothing on disk is modified.
  When there is a new version the app says so and offers a link; installing it
  is your decision and your click.
* It can be switched off, in which case no request is ever made.

The result is cached for a few hours so that leaving the app open does not
mean asking repeatedly.
"""

from __future__ import annotations

import logging
import re
import time

import httpx

log = logging.getLogger(__name__)

RELEASES_API = "https://api.github.com/repos/spikesight/spikesight-app/releases/latest"
RELEASES_PAGE = "https://github.com/spikesight/spikesight-app/releases/latest"

#: How long a check is good for. Releases are rare; nobody needs this hourly.
CACHE_SECONDS = 6 * 3600.0
TIMEOUT = 6.0


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """``"v2.1"`` -> ``(2, 1)``. None when it is not a version at all."""
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)*)", str(text))
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str | None, current: str | None) -> bool:
    """Is ``latest`` a higher version than ``current``?

    Compared piece by piece with the shorter one padded, so 2.1 beats 2.0.9
    and 2.1 does not beat itself.
    """
    new, old = parse_version(latest), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


class UpdateCheck:
    """Remembers the last answer, so the app asks at most once every few hours."""

    def __init__(self, current_version: str) -> None:
        self._current = current_version
        self._cached: dict | None = None
        self._checked_at = 0.0

    def _result(self, latest: str | None, url: str, error: str = "") -> dict:
        return {
            "current": self._current,
            "latest": latest,
            "updateAvailable": is_newer(latest, self._current),
            "url": url,
            "error": error,
        }

    async def check(self, force: bool = False) -> dict:
        if not force and self._cached and time.monotonic() - self._checked_at < CACHE_SECONDS:
            return self._cached

        result = self._result(None, RELEASES_PAGE, "")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(
                    RELEASES_API,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": f"SpikeSight/{self._current}",
                    },
                )
            if response.status_code == 404:
                # No releases published yet. Not an error worth showing.
                result = self._result(None, RELEASES_PAGE)
            else:
                response.raise_for_status()
                payload = response.json()
                result = self._result(
                    payload.get("tag_name") or payload.get("name"),
                    payload.get("html_url") or RELEASES_PAGE,
                )
        except (httpx.HTTPError, ValueError) as exc:
            # Offline, rate limited, blocked by a firewall: all the same to us.
            log.debug("Update check failed: %s", exc)
            result = self._result(None, RELEASES_PAGE, "could not reach GitHub")

        self._cached = result
        self._checked_at = time.monotonic()
        return result
