"""Client for the Riot Client's local (127.0.0.1) REST API.

This is the API the Riot Client exposes to itself on loopback. SpikeSight only
ever issues GET requests against it: credentials, chat session, and presence.

Nothing here talks to the game process, reads game memory, or writes anything.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass

import httpx

from . import paths
from .errors import ClientNotRunning, SpikeSightError


@dataclass(frozen=True)
class Lockfile:
    name: str
    pid: int
    port: int
    password: str
    protocol: str

    @property
    def base_url(self) -> str:
        return f"{self.protocol}://127.0.0.1:{self.port}"

    @property
    def auth(self) -> tuple[str, str]:
        return ("riot", self.password)


def read_lockfile() -> Lockfile:
    """Read %LOCALAPPDATA%\Riot Games\Riot Client\Config\lockfile."""
    path = paths.RIOT_LOCKFILE
    if not path.exists():
        raise ClientNotRunning("Riot Client lockfile not found.")
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as exc:  # the client rewrites this file on launch
        raise ClientNotRunning(f"Could not read lockfile: {exc}") from exc

    parts = raw.split(":")
    if len(parts) < 5:
        raise ClientNotRunning("Lockfile is malformed (client may be starting up).")
    # The product name can itself contain a colon, so parse from the right.
    protocol = parts[-1]
    password = parts[-2]
    port = parts[-3]
    pid = parts[-4]
    name = ":".join(parts[:-4])
    try:
        return Lockfile(name=name, pid=int(pid), port=int(port),
                        password=password, protocol=protocol)
    except ValueError as exc:
        raise ClientNotRunning("Lockfile is malformed.") from exc


@dataclass(frozen=True)
class Credentials:
    """Short-lived tokens minted by the local client for the logged-in user."""

    puuid: str
    access_token: str
    entitlement: str

    def masked(self) -> str:
        return f"{self.puuid[:8]}... (token {len(self.access_token)} chars)"


@dataclass(frozen=True)
class Presence:
    puuid: str
    game_name: str
    game_tag: str
    private: dict

    def _nested(self, group: str, key: str) -> str:
        """Read a field that moved into a sub-object in newer clients.

        The presence blob used to be flat (``sessionLoopState`` at the top
        level) and is now grouped (``matchPresenceData.sessionLoopState``).
        Both shapes are accepted so a client update cannot silently break
        state detection.
        """
        section = self.private.get(group)
        if isinstance(section, dict) and section.get(key) is not None:
            return str(section[key])
        value = self.private.get(key)
        return str(value) if value is not None else ""

    @property
    def session_loop_state(self) -> str:
        return self._nested("matchPresenceData", "sessionLoopState").upper()

    @property
    def party_id(self) -> str:
        return self._nested("partyPresenceData", "partyId")

    @property
    def party_size(self) -> int:
        try:
            return int(self._nested("partyPresenceData", "partySize") or 0)
        except ValueError:
            return 0

    @property
    def queue_id(self) -> str:
        return self._nested("matchPresenceData", "queueId")


def _decode_private(blob: str) -> dict:
    if not blob:
        return {}
    try:
        return json.loads(base64.b64decode(blob).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {}


class LocalClient:
    """Async GET-only wrapper around the local Riot Client API.

    TLS verification is disabled *only* for 127.0.0.1: the client serves a
    self-signed certificate that no public trust store will ever accept.
    """

    def __init__(self, timeout: float = 8.0) -> None:
        self._timeout = timeout
        self._lockfile: Lockfile | None = None
        self._client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._lockfile = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        lockfile = read_lockfile()
        if self._client is None or self._lockfile != lockfile:
            # Port/password changed (client restarted): rebuild the session.
            if self._client is not None:
                await self._client.aclose()
            self._lockfile = lockfile
            self._client = httpx.AsyncClient(
                base_url=lockfile.base_url,
                auth=lockfile.auth,
                verify=False,  # loopback only; see class docstring
                timeout=self._timeout,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def get_json(self, path: str) -> object:
        client = await self._ensure_client()
        try:
            response = await client.get(path)
        except httpx.HTTPError as exc:
            # Most commonly: the client was closed between the lockfile read
            # and the request.
            self._client = None
            raise ClientNotRunning(f"Local API unreachable: {exc}") from exc
        if response.status_code == 404:
            raise SpikeSightError(f"Local endpoint not found: {path}")
        response.raise_for_status()
        return response.json()

    async def credentials(self) -> Credentials:
        data = await self.get_json("/entitlements/v1/token")
        if not isinstance(data, dict) or not data.get("accessToken"):
            raise ClientNotRunning("Local client has no signed-in session yet.")
        return Credentials(
            puuid=str(data.get("subject", "")),
            access_token=str(data["accessToken"]),
            entitlement=str(data.get("token", "")),
        )

    async def chat_session(self) -> dict:
        data = await self.get_json("/chat/v1/session")
        return data if isinstance(data, dict) else {}

    async def region_locale(self) -> dict:
        data = await self.get_json("/riotclient/region-locale")
        return data if isinstance(data, dict) else {}

    async def presences(self) -> list[Presence]:
        data = await self.get_json("/chat/v4/presences")
        entries = data.get("presences", []) if isinstance(data, dict) else []
        out: list[Presence] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("product") not in (None, "valorant"):
                continue
            out.append(
                Presence(
                    puuid=str(entry.get("puuid", "")),
                    game_name=str(entry.get("game_name", "")),
                    game_tag=str(entry.get("game_tag", "")),
                    private=_decode_private(str(entry.get("private", ""))),
                )
            )
        return out

    async def self_presence(self, puuid: str) -> Presence | None:
        for presence in await self.presences():
            if presence.puuid == puuid:
                return presence
        return None
