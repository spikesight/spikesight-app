"""Resolving the values Riot's edge servers require in request headers.

Three things are needed to talk to pd/glz with the local client's tokens:

* ``X-Riot-ClientPlatform``  - a fixed base64 blob describing a PC client.
* ``X-Riot-ClientVersion``   - the running shipping build, e.g.
  ``release-13.04-shipping-20-5340415``.
* the region/shard pair, which selects the ``pd.``/``glz-`` hostnames.

All of it is read from files the client already wrote to disk, or from the
local API. Nothing is guessed at, and nothing is scraped from the game process.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

from . import paths
from .local_api import LocalClient

# The client sends this verbatim; it is a constant, not a fingerprint of the
# user's machine. Built here rather than pasted so it stays readable.
_PLATFORM_JSON = (
    '{\r\n\t"platformType": "PC",\r\n\t"platformOS": "Windows",\r\n\t'
    '"platformOSVersion": "10.0.19042.1.256.64bit",\r\n\t'
    '"platformChipset": "Unknown"\r\n}'
)
CLIENT_PLATFORM = base64.b64encode(_PLATFORM_JSON.encode("utf-8")).decode("ascii")

_VERSION_RE = re.compile(rb"release-[\d.]+-shipping-\d+-\d+")
_HOST_RE = re.compile(rb"glz-([a-z0-9]+)-\d+\.([a-z0-9]+)\.a\.pvp\.net")

# Regions that do not have their own shard.
_REGION_TO_SHARD = {
    "na": "na",
    "latam": "na",
    "br": "na",
    "eu": "eu",
    "ap": "ap",
    "kr": "kr",
    "pbe": "pbe",
}


@dataclass(frozen=True)
class Endpoints:
    region: str
    shard: str
    client_version: str

    @property
    def pd(self) -> str:
        return f"https://pd.{self.shard}.a.pvp.net"

    @property
    def glz(self) -> str:
        return f"https://glz-{self.region}-1.{self.shard}.a.pvp.net"

    @property
    def shared(self) -> str:
        return f"https://shared.{self.shard}.a.pvp.net"


_HEAD_BYTES = 1_000_000
_TAIL_BYTES = 4_000_000


def _log_chunks(path) -> bytes:
    """Head + tail of a log that can be tens of megabytes and is kept open.

    The build version is written in the first hundred lines of a session, while
    the shard hostnames appear throughout, so both ends are worth reading and
    the middle never is.
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(0)
            head = handle.read(min(size, _HEAD_BYTES))
            if size <= _HEAD_BYTES:
                return head
            handle.seek(max(_HEAD_BYTES, size - _TAIL_BYTES))
            return head + b"\n" + handle.read()
    except OSError:
        return b""


def _candidate_logs() -> list:
    """The live log first, then the most recent rotated backups."""
    live = paths.RIOT_SHOOTER_LOG
    candidates = [live] if live.exists() else []
    try:
        backups = sorted(
            live.parent.glob("ShooterGame-backup-*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        candidates.extend(backups[:3])
    except OSError:
        pass
    return candidates


def version_from_log() -> str | None:
    """Most recent shipping-version string written to a ShooterGame log."""
    for path in _candidate_logs():
        matches = _VERSION_RE.findall(_log_chunks(path))
        if matches:
            return matches[-1].decode("ascii")
    return None


def region_shard_from_log() -> tuple[str, str] | None:
    """Region/shard as taken from the glz hostname the game actually used."""
    for path in _candidate_logs():
        matches = _HOST_RE.findall(_log_chunks(path))
        if matches:
            region, shard = matches[-1]
            return region.decode("ascii"), shard.decode("ascii")
    return None


async def resolve_endpoints(local: LocalClient, cfg) -> Endpoints:
    """Work out region/shard/version, preferring explicit config."""
    region = str(cfg.get("riot.region", "") or "").lower()
    shard = str(cfg.get("riot.shard", "") or "").lower()
    version = str(cfg.get("riot.client_version", "") or "")

    if not (region and shard):
        from_log = region_shard_from_log()
        if from_log:
            region = region or from_log[0]
            shard = shard or from_log[1]

    if not region:
        # Fall back to what the client reports; the shard is then derived.
        try:
            locale = await local.region_locale()
            region = str(locale.get("region", "")).lower()
        except Exception:  # noqa: BLE001 - best effort, never fatal
            region = ""
    if not region:
        raise RuntimeError(
            "Could not determine your region. Launch VALORANT once, or set "
            "riot.region / riot.shard in config.toml."
        )
    if not shard:
        shard = _REGION_TO_SHARD.get(region, region)

    if not version:
        version = version_from_log() or ""
    if not version:
        raise RuntimeError(
            "Could not read the VALORANT client version from ShooterGame.log. "
            "Launch VALORANT once, or set riot.client_version in config.toml."
        )

    return Endpoints(region=region, shard=shard, client_version=version)


def platform_payload() -> dict:
    """The decoded platform blob, for display in the diagnostics panel."""
    return json.loads(_PLATFORM_JSON)
