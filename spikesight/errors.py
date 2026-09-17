"""Exception types used across SpikeSight."""

from __future__ import annotations


class SpikeSightError(Exception):
    """Base class for every error SpikeSight raises deliberately."""

    #: Short, user-facing hint rendered in the UI status bar.
    hint: str = ""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        if hint:
            self.hint = hint


class ClientNotRunning(SpikeSightError):
    """The Riot Client / VALORANT is not running, so there is no lockfile."""

    hint = "Start the Riot Client (and VALORANT) and this will connect automatically."


class AuthExpired(SpikeSightError):
    """The local access token or entitlement was rejected and must be re-read."""

    hint = "Re-reading credentials from the local Riot Client."


class RemoteError(SpikeSightError):
    """A pd/glz endpoint returned an unexpected status."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NotInMatch(SpikeSightError):
    """The player is not currently in agent select or a live match."""
