"""Premade / party detection.

Two independent methods, in order of trust:

1. **Confirmed** - party ids taken from the local presence feed. The client
   publishes a ``partyId`` for you and for your friends, so your own stack is
   known for certain and costs nothing. Riot's ``/parties/v1/players/{puuid}``
   endpoint refuses to answer for players outside your party, so it is only
   ever asked about yourself.
2. **Likely** - derived from data already fetched for the stats panel. Two
   sources, in order of strength:

   * a *group* that has actually been observed partied together in recent
     matches. This is what makes a genuine 4- or 5-stack detectable as one
     group, at its real size;
   * failing that, a *pair* who keep turning up in the same party even as the
     group around them changes.

   Groups larger than two are only ever reported when that exact group has been
   seen together. Chaining pairs into a bigger stack - A duos with B, B duos
   with C, therefore "3-stack" - invents premades that do not exist, so it is
   deliberately not done.

Parties never span teams, so grouping is always done within a team.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from itertools import combinations
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: Shared recent parties needed before two players are called a likely premade.
DEFAULT_MIN_SHARED = 2


@dataclass
class PartyAssignment:
    group: int          # 1-based color index, stable within a snapshot
    size: int
    confidence: str     # "confirmed" | "likely"
    evidence: str

    def to_dict(self) -> dict:
        return {
            "group": self.group,
            "size": self.size,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


def hints_from_presence(presences) -> dict[str, str]:
    """Party ids the local client already knows about, free of charge."""
    out: dict[str, str] = {}
    for presence in presences or []:
        party_id = getattr(presence, "party_id", "")
        if presence.puuid and party_id:
            out[presence.puuid] = party_id
    return out


#: Above this many players on a side, stop looking for the largest parties.
#: Only deathmatch gets near it, and premades there are not worth the work.
_MAX_PARTY_SEARCH = 10


def _inferred_parties(
    members: list[str],
    co_party: dict[tuple[str, str], int],
    claimed: set[str],
    min_shared: int,
) -> list[list[str]]:
    """Candidate parties, biggest first.

    A party is a set of players who have every one of them partied with every
    other one of them. Anything looser lets two unrelated duos who share a
    friend look like a trio.
    """
    nodes = sorted(p for p in members if p not in claimed)
    if len(nodes) < 2:
        return []

    edges = {
        frozenset(pair)
        for pair, count in co_party.items()
        if count >= min_shared and pair[0] in nodes and pair[1] in nodes
    }
    if not edges:
        return []

    largest = min(len(nodes), _MAX_PARTY_SEARCH)
    found: list[list[str]] = []
    taken: set[str] = set()
    for size in range(largest, 1, -1):
        for candidate in combinations(nodes, size):
            if any(p in taken for p in candidate):
                continue
            if all(frozenset(pair) in edges for pair in combinations(candidate, 2)):
                found.append(list(candidate))
                taken.update(candidate)
    return found


def names_from_presence(presences) -> dict[str, dict]:
    """Riot IDs the local client has already told us, for you and your friends.

    Used only for members of your own party. See ``privacy.build_display_name``
    for why that is the one place a Streamer Mode name may be shown.
    """
    out: dict[str, dict] = {}
    for presence in presences or []:
        if presence.puuid and presence.game_name:
            out[presence.puuid] = {
                "game_name": presence.game_name,
                "tag_line": presence.game_tag,
            }
    return out


def your_party(party_ids: dict[str, str], self_puuid: str) -> set[str]:
    """Everyone sharing your live party id, including you."""
    mine = party_ids.get(self_puuid)
    if not mine:
        return {self_puuid} if self_puuid else set()
    return {puuid for puuid, party in party_ids.items() if party == mine}


def detect(
    *,
    teams: dict[str, list[str]],
    party_ids: dict[str, str],
    co_party: dict[tuple[str, str], int] | None = None,
    co_parties: dict[frozenset[str], int] | None = None,
    min_shared: int = DEFAULT_MIN_SHARED,
) -> dict[str, PartyAssignment]:
    """Assign every premade player a stable color group index."""
    co_party = co_party or {}
    co_parties = co_parties or {}
    assignments: dict[str, PartyAssignment] = {}
    next_group = 1

    for _team, members in sorted(teams.items()):
        member_set = set(members)
        claimed: set[str] = set()

        # --- method 1: confirmed party ids -------------------------------
        by_party: dict[str, list[str]] = defaultdict(list)
        for puuid in members:
            party_id = party_ids.get(puuid)
            if party_id:
                by_party[party_id].append(puuid)

        for _party_id, group_members in sorted(by_party.items()):
            if len(group_members) < 2:
                continue
            for puuid in group_members:
                assignments[puuid] = PartyAssignment(
                    group=next_group,
                    size=len(group_members),
                    confidence="confirmed",
                    evidence="Shared live party id",
                )
                claimed.add(puuid)
            next_group += 1

        # --- method 2: inferred from recent matches ------------------------
        # A premade is a set of players who have all partied with each other,
        # so look for the biggest such set first. That finds a real 5-stack at
        # its true size, and it refuses to chain A-B and B-C into a three-stack
        # when A and C have never queued together.
        for group_members in _inferred_parties(members, co_party, claimed, min_shared):
            size = len(group_members)
            exact = co_parties.get(frozenset(group_members), 0)
            if exact:
                evidence = (
                    f"Seen together as a {size}-stack in "
                    f"{exact} recent match{'es' if exact != 1 else ''}"
                )
            elif size > 2:
                evidence = f"All {size} have partied with each other recently"
            else:
                shared = co_party.get(tuple(group_members), 0)
                evidence = (
                    f"Partied together in {shared} recent "
                    f"match{'es' if shared != 1 else ''}"
                )
            for puuid in group_members:
                assignments[puuid] = PartyAssignment(
                    group=next_group,
                    size=size,
                    confidence="likely",
                    evidence=evidence,
                )
                claimed.add(puuid)
            next_group += 1

    return assignments
