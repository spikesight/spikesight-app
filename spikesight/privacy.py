"""Privacy enforcement - the single place player data is allowed to be hidden.

Riot exposes several per-player privacy flags. SpikeSight honors all of them,
and does so *before* data leaves the backend, so a redacted field is never sent
to the browser at all rather than merely hidden with CSS.

=========================  ==================================================
Flag                       Effect in SpikeSight
=========================  ==================================================
``Incognito``              Streamer Mode. The riot ID is never resolved, never
                           displayed and never written to the notes database.
                           The player is shown by agent instead. The single
                           exception is somebody in your own party, whose name
                           the local client has already handed you - the game
                           shows you your own party too.
``HideAccountLevel``       Account level is not displayed and is excluded from
                           smurf scoring. It is *not* recovered from match
                           history, which would defeat the setting.
``IsActRankBadgeHidden``   Peak rank and previous-act rank (both derived from
                           the act rank badge) are suppressed.
``IsLeaderboardAnonymized``Leaderboard position is suppressed.
=========================  ==================================================
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

HIDDEN_NAME = "Hidden Player"

# Crockford base32: no I, L, O or U, so a code is never misread or
# accidentally spelled into a word.
_TAG_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TAG_LENGTH = 4


@dataclass
class PrivacyFlags:
    """Per-player privacy state, collected from the match and MMR payloads."""

    incognito: bool = False
    hide_account_level: bool = False
    act_rank_hidden: bool = False
    leaderboard_anonymized: bool = False

    def merge(self, other: "PrivacyFlags") -> "PrivacyFlags":
        return PrivacyFlags(
            incognito=self.incognito or other.incognito,
            hide_account_level=self.hide_account_level or other.hide_account_level,
            act_rank_hidden=self.act_rank_hidden or other.act_rank_hidden,
            leaderboard_anonymized=self.leaderboard_anonymized
            or other.leaderboard_anonymized,
        )

    def reasons(self) -> list[str]:
        out = []
        if self.incognito:
            out.append("Streamer Mode is on for this player")
        if self.hide_account_level:
            out.append("Account level hidden by player")
        if self.act_rank_hidden:
            out.append("Act rank badge hidden by player")
        if self.leaderboard_anonymized:
            out.append("Leaderboard placement anonymized")
        return out


def hidden_tag(puuid: str) -> str:
    """A short, stable label for a player we are not allowed to name.

    Streamer Mode hides the riot ID, which used to leave every hidden
    player looking identical - a wall of "Unknown player" rows in
    Encounters, and an agent name in the lobby that says nothing about
    *which* Brimstone this is. A note is keyed on puuid and has always
    followed the right person; there was simply no way to see that.

    This is that missing handle. It is derived from the puuid alone, so it
    is the same code every time you meet them and carries nothing about
    who they are. It is a label, not a name, and it is not reversible.
    """
    digest = hashlib.blake2s(puuid.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    out = []
    for _ in range(_TAG_LENGTH):
        value, index = divmod(value, len(_TAG_ALPHABET))
        out.append(_TAG_ALPHABET[index])
    return "".join(out)


def flags_from_identity(identity: dict | None) -> PrivacyFlags:
    identity = identity or {}
    return PrivacyFlags(
        incognito=bool(identity.get("Incognito")),
        hide_account_level=bool(identity.get("HideAccountLevel")),
    )


def flags_from_mmr(mmr: dict | None) -> PrivacyFlags:
    mmr = mmr or {}
    return PrivacyFlags(
        act_rank_hidden=bool(mmr.get("IsActRankBadgeHidden")),
        leaderboard_anonymized=bool(mmr.get("IsLeaderboardAnonymized")),
    )


@dataclass
class DisplayName:
    """What the UI is permitted to render for a player."""

    display: str
    game_name: str | None = None
    tag_line: str | None = None
    hidden: bool = False
    reason: str = ""
    # Set only when the riot ID is withheld. See hidden_tag().
    hidden_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "display": self.display,
            "gameName": self.game_name,
            "tagLine": self.tag_line,
            "hidden": self.hidden,
            "reason": self.reason,
            "hiddenId": self.hidden_id,
        }


def resolvable_puuids(players: list[dict]) -> list[str]:
    """The subset of a roster whose names we are allowed to look up.

    Incognito players are dropped here, which means their riot ID is never
    even requested from Riot - not requested, not cached, not stored.
    """
    return [
        p["puuid"]
        for p in players
        if p.get("puuid") and not p.get("flags", PrivacyFlags()).incognito
    ]


def build_display_name(
    *,
    flags: PrivacyFlags,
    name_record: dict | None,
    agent_name: str | None,
    is_self: bool,
    party_name: dict | None = None,
    puuid: str | None = None,
) -> DisplayName:
    if flags.incognito:
        # One exception, and only one: somebody in your own party, named from
        # the local client's own presence feed. Riot's Streamer Mode hides a
        # player from strangers - the game itself shows you your party members,
        # and this name was never requested from Riot, it was already sitting
        # in the client. Nobody outside your party is ever named this way.
        if party_name and party_name.get("game_name"):
            game_name = str(party_name["game_name"])
            tag_line = str(party_name.get("tag_line", ""))
            return DisplayName(
                display=f"{game_name}#{tag_line}" if tag_line else game_name,
                game_name=game_name,
                tag_line=tag_line,
                hidden=False,
                reason="In your party. Hidden from everyone else by Streamer Mode.",
            )
        code = hidden_tag(puuid) if puuid else None
        label = f"{agent_name}" if agent_name else HIDDEN_NAME
        return DisplayName(
            display=label,
            hidden=True,
            hidden_id=code,
            reason="Player has Streamer Mode enabled"
            + (f". Tracked locally as {code}." if code else ""),
        )

    if not name_record or not name_record.get("game_name"):
        return DisplayName(display=agent_name or "Unknown", hidden=False, reason="")

    game_name = str(name_record["game_name"])
    tag_line = str(name_record.get("tag_line", ""))
    display = f"{game_name}#{tag_line}" if tag_line else game_name
    return DisplayName(display=display, game_name=game_name, tag_line=tag_line)


def storable_alias(flags: PrivacyFlags, name: DisplayName) -> tuple[str | None, str | None]:
    """Name/tag safe to persist in the local notes database.

    Nothing is stored for an incognito player: their note is keyed on puuid
    alone, so it still resurfaces next time without ever recording who they
    are.
    """
    if flags.incognito or name.hidden:
        return (None, None)
    return (name.game_name, name.tag_line)
