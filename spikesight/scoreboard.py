"""Builds the scoreboard snapshot the UI renders.

The snapshot is produced in two phases so the lobby is on screen immediately:

* **quick** - roster, names, ranks, peaks, parties, notes. A handful of small
  requests; typically under two seconds.
* **enriched** - recent-form stats, refined smurf scores and the win
  probability's form adjustment. Slower, and streamed in behind the quick pass.

All privacy redaction happens here, before anything is serialized.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

from . import mmrview, parties, privacy, ranks, smurf, winprob
from .content import GameContent
from .errors import RemoteError, SpikeSightError
from .mmrview import RankView
from .privacy import PrivacyFlags

log = logging.getLogger(__name__)

STATE_MENUS = "MENUS"
STATE_PREGAME = "PREGAME"
STATE_INGAME = "INGAME"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Slot:
    """One player in the current lobby, before enrichment."""

    puuid: str
    team: str
    side: str                      # "ally" | "enemy"
    character_id: str = ""
    flags: PrivacyFlags = field(default_factory=PrivacyFlags)
    account_level: int | None = None
    is_self: bool = False
    is_coach: bool = False
    selection_state: str = ""
    badge: dict | None = None

    mmr: dict | None = None
    name_record: dict | None = None


class ScoreboardBuilder:
    def __init__(self, remote, content: GameContent, notes_store, stats_engine, cfg) -> None:
        self._remote = remote
        self._content = content
        self._notes = notes_store
        self._stats = stats_engine
        #: (match id, result) from the last deep build, reused by the quick
        #: rebuilds that happen while the same lobby is still on screen.
        self._last_stats: tuple[str, object] | None = None
        self._cfg = cfg
        self._last_recorded_match: str | None = None

    def set_content(self, content: GameContent) -> None:
        self._content = content

    @property
    def cached_matches(self) -> int:
        return self._stats.cached_matches

    # -- roster discovery ---------------------------------------------------

    async def detect_match(self, puuid: str) -> tuple[str, str | None]:
        """Return ``(state, match_id)`` by asking the game servers directly."""
        core = await self._remote.coregame_player(puuid)
        if isinstance(core, dict) and core.get("MatchID"):
            return STATE_INGAME, str(core["MatchID"])
        pre = await self._remote.pregame_player(puuid)
        if isinstance(pre, dict) and pre.get("MatchID"):
            return STATE_PREGAME, str(pre["MatchID"])
        return STATE_MENUS, None

    def _slots_from_pregame(self, payload: dict, self_puuid: str) -> tuple[list[Slot], dict]:
        ally = (payload.get("AllyTeam") or {})
        slots: list[Slot] = []
        for entry in ally.get("Players") or []:
            if not isinstance(entry, dict):
                continue
            identity = entry.get("PlayerIdentity") or {}
            slots.append(
                Slot(
                    puuid=str(entry.get("Subject") or ""),
                    team=str(ally.get("TeamID") or "Blue"),
                    side="ally",
                    character_id=str(entry.get("CharacterID") or ""),
                    flags=privacy.flags_from_identity(identity),
                    account_level=identity.get("AccountLevel"),
                    is_self=str(entry.get("Subject") or "") == self_puuid,
                    selection_state=str(entry.get("CharacterSelectionState") or ""),
                    badge=entry.get("SeasonalBadgeInfo"),
                )
            )
        meta = {
            "id": str(payload.get("ID") or ""),
            "mapId": str(payload.get("MapID") or ""),
            "mode": str(payload.get("Mode") or ""),
            "queue": str(payload.get("QueueID") or ""),
            "provisioning": str(payload.get("ProvisioningFlowID") or ""),
            "isRanked": bool(payload.get("IsRanked")),
            "pregameState": str(payload.get("PregameState") or ""),
            "enemyTeamSize": int(payload.get("EnemyTeamSize") or 0),
            "enemyLockCount": int(payload.get("EnemyTeamLockCount") or 0),
            "phaseRemainingSeconds": int(
                (payload.get("PhaseTimeRemainingNS") or 0) / 1_000_000_000
            ),
        }
        return slots, meta

    def _slots_from_coregame(self, payload: dict, self_puuid: str) -> tuple[list[Slot], dict]:
        players = [p for p in payload.get("Players") or [] if isinstance(p, dict)]
        own_team = ""
        for entry in players:
            if str(entry.get("Subject") or "") == self_puuid:
                own_team = str(entry.get("TeamID") or "")
                break

        slots: list[Slot] = []
        for entry in players:
            identity = entry.get("PlayerIdentity") or {}
            team = str(entry.get("TeamID") or "")
            puuid = str(entry.get("Subject") or "")
            slots.append(
                Slot(
                    puuid=puuid,
                    team=team,
                    side="ally" if (own_team and team == own_team) else "enemy",
                    character_id=str(entry.get("CharacterID") or ""),
                    flags=privacy.flags_from_identity(identity),
                    account_level=identity.get("AccountLevel"),
                    is_self=puuid == self_puuid,
                    is_coach=bool(entry.get("IsCoach")),
                    badge=entry.get("SeasonalBadgeInfo"),
                )
            )
        matchmaking = payload.get("MatchmakingData")
        queue = ""
        if isinstance(matchmaking, dict):
            queue = str(matchmaking.get("QueueID") or "")
        meta = {
            "id": str(payload.get("MatchID") or ""),
            "mapId": str(payload.get("MapID") or ""),
            "mode": str(payload.get("ModeID") or ""),
            "queue": queue,
            "provisioning": str(payload.get("ProvisioningFlow") or ""),
            "isRanked": queue == "competitive",
            "state": str(payload.get("State") or ""),
        }
        return slots, meta

    # -- MMR interpretation -------------------------------------------------

    def _badge_tier(self, badge: dict | None) -> int:
        """Act rank from the badge the game itself puts in the match payload."""
        if not isinstance(badge, dict):
            return 0
        try:
            return int(badge.get("Rank") or 0)
        except (TypeError, ValueError):
            return 0

    # -- snapshot assembly --------------------------------------------------

    async def build(
        self,
        *,
        state: str,
        match_payload: dict,
        self_puuid: str,
        deep: bool,
        party_hints: dict[str, str] | None = None,
        presence_names: dict[str, dict] | None = None,
        progress=None,
    ) -> dict:
        if state == STATE_PREGAME:
            slots, meta = self._slots_from_pregame(match_payload, self_puuid)
        else:
            slots, meta = self._slots_from_coregame(match_payload, self_puuid)

        slots = [s for s in slots if s.puuid]
        if not slots:
            raise SpikeSightError("Match payload contained no players.")

        puuids = [s.puuid for s in slots]

        # MMR and names in parallel. Incognito players are excluded from the
        # name lookup entirely - see privacy.resolvable_puuids.
        name_targets = [s.puuid for s in slots if not s.flags.incognito]
        mmr_task = asyncio.gather(
            *(self._remote.mmr(s.puuid) for s in slots), return_exceptions=True
        )
        names_task = asyncio.create_task(self._safe_names(name_targets))

        mmr_results, name_records = await asyncio.gather(mmr_task, names_task)

        # Party ids come from the local presence feed; Riot will only answer
        # the parties endpoint about ourselves, so that is all we ask it.
        party_ids = dict(party_hints or {})
        if self_puuid not in party_ids:
            own_party = await self._remote.party_of(self_puuid)
            if own_party:
                party_ids[self_puuid] = own_party

        # Names for your own party members only, and only ones the local client
        # already published. Never used for anyone else.
        own_party_members = parties.your_party(party_ids, self_puuid)
        party_names = {
            puuid: record
            for puuid, record in (presence_names or {}).items()
            if puuid in own_party_members
        }

        for slot, result in zip(slots, mmr_results):
            slot.mmr = result if isinstance(result, dict) else None
            slot.flags = slot.flags.merge(privacy.flags_from_mmr(slot.mmr))
            slot.name_record = name_records.get(slot.puuid)

        # Agent select refreshes the roster every few seconds as people lock
        # in, and each refresh is a *quick* build. Without this, the recent
        # form fetched once at the start would be thrown away seconds later
        # and the K/D column, the ACS, the form pips and the inferred parties
        # would all blink out for the rest of the lobby.
        match_id = meta.get("id", "")
        stats_result = None
        if deep and self._cfg.get("stats.enable_deep_stats", True):
            stats_result = await self._stats.collect(
                puuids, progress=progress, lobby_queue=meta.get("queue", "")
            )
            self._last_stats = (match_id, stats_result)
        elif self._last_stats and match_id and self._last_stats[0] == match_id:
            stats_result = self._last_stats[1]

        teams: dict[str, list[str]] = {}
        for slot in slots:
            teams.setdefault(slot.team or slot.side, []).append(slot.puuid)

        assignments = parties.detect(
            teams=teams,
            party_ids=party_ids,
            co_party=stats_result.co_party if stats_result else None,
            co_parties=stats_result.co_parties if stats_result else None,
        )

        notes_by_puuid = await self._notes.notes_for(puuids)
        # Encounter counts are read *before* this lobby is recorded, so "3rd
        # time" means three previous games, not counting this one.
        encounters_by_puuid = await self._notes.encounter_summary(puuids)

        players = [
            self._render_player(
                slot,
                assignments.get(slot.puuid),
                stats_result.players.get(slot.puuid) if stats_result else None,
                notes_by_puuid.get(slot.puuid, []),
                encounters_by_puuid.get(slot.puuid),
                party_names.get(slot.puuid),
            )
            for slot in slots
        ]

        players.sort(key=_sort_key)

        ally = [p for p in players if p["side"] == "ally"]
        enemy = [p for p in players if p["side"] == "enemy"]

        probability = None
        if enemy:
            probability = winprob.estimate(
                cfg=self._cfg,
                ally=[{"points": p["ratingPoints"], "kd": (p["stats"] or {}).get("kd")}
                      for p in ally],
                enemy=[{"points": p["ratingPoints"], "kd": (p["stats"] or {}).get("kd")}
                       for p in enemy],
            )

        map_name = self._content.map_name(meta.get("mapId")) or "Unknown map"
        snapshot = {
            "state": state,
            "phase": "enriched" if stats_result else "quick",
            "generatedAt": _iso_now(),
            "self": self_puuid,
            "match": {
                **meta,
                "map": map_name,
                "modeName": _mode_name(meta.get("mode", "")),
                "label": _match_label(
                    meta.get("queue", ""),
                    meta.get("mode", ""),
                    meta.get("provisioning", ""),
                ),
            },
            "winProbability": probability.to_dict() if probability else None,
            "teams": [
                {"side": "ally", "label": "Your team", "players": ally},
                {"side": "enemy", "label": "Enemy team", "players": enemy},
            ],
            "alerts": _build_alerts(players, state),
            "stats": {
                "matchesExamined": stats_result.matches_examined if stats_result else 0,
                "matchesFetched": stats_result.matches_fetched if stats_result else 0,
            },
        }
        return snapshot

    async def _safe_names(self, puuids: list[str]) -> dict[str, dict]:
        try:
            return await self._remote.names(puuids)
        except (RemoteError, SpikeSightError) as exc:
            log.warning("Name lookup failed: %s", exc)
            return {}

    def _render_player(
        self, slot: Slot, party, stats, notes: list[dict], encounters: dict | None,
        party_name: dict | None = None,
    ) -> dict:
        seasonal = mmrview.seasonal(slot.mmr)
        current = mmrview.current_rank(slot.mmr, seasonal, self._content)
        if not current.tier:
            # No MMR (request failed, or an account with no ranked history):
            # the match payload carries the act rank badge, which is enough to
            # place them on the board.
            badge_tier = self._badge_tier(slot.badge)
            if badge_tier:
                current = RankView(tier=badge_tier)

        agent_name = self._content.agent_name(slot.character_id)
        display = privacy.build_display_name(
            flags=slot.flags,
            name_record=slot.name_record,
            agent_name=agent_name,
            is_self=slot.is_self,
            party_name=party_name,
            puuid=slot.puuid,
        )

        peak_block = None
        previous_block = None
        if not slot.flags.act_rank_hidden:
            peak = mmrview.peak(seasonal)
            if peak:
                act = self._content.act(peak[1])
                peak_block = {
                    "rank": ranks.tier_dict(peak[0]),
                    "act": act.to_dict() if act else None,
                }
            previous_act = self._content.previous_act()
            if previous_act:
                info = seasonal.get(previous_act.id)
                if info and int(info.get("CompetitiveTier") or 0) >= 3:
                    previous_block = {
                        "rank": ranks.tier_dict(int(info["CompetitiveTier"])),
                        "act": previous_act.to_dict(),
                        "games": int(info.get("NumberOfGames") or 0),
                        "wins": int(info.get("NumberOfWins") or 0),
                    }

        current_act = self._content.current_act()
        # The act record is part of the same act rank badge as the peak/previous
        # tiers, so it is suppressed by the same player setting.
        act_info = None
        if current_act and not slot.flags.act_rank_hidden:
            act_info = seasonal.get(current_act.id)
        act_games = int((act_info or {}).get("NumberOfGames") or 0)
        act_wins = int((act_info or {}).get("NumberOfWins") or 0)

        verdict = smurf.evaluate(
            cfg=self._cfg,
            account_level=None if slot.flags.hide_account_level else slot.account_level,
            level_hidden=slot.flags.hide_account_level,
            current_tier=current.tier,
            peak_tier=peak_block["rank"]["tier"] if peak_block else None,
            act_games=act_games,
            act_wins=act_wins,
            kd=stats.kd if stats else None,
            recent_matches=stats.matches if stats else 0,
            recent_winrate=stats.recent_winrate if stats else None,
        )

        note_items = notes or []
        max_severity = max((n["severity"] for n in note_items), default=0)

        leaderboard = None
        if current.leaderboard and not slot.flags.leaderboard_anonymized:
            leaderboard = current.leaderboard

        return {
            "puuid": slot.puuid,
            "team": slot.team,
            "side": slot.side,
            "isSelf": slot.is_self,
            "isCoach": slot.is_coach,
            "selectionState": slot.selection_state,
            "agent": {
                "id": slot.character_id,
                "name": agent_name,
                "icon": self._content.agent_icon(slot.character_id),
            },
            "name": display.to_dict(),
            "trackerUrl": tracker_url(display),
            "rank": ranks.tier_dict(current.tier, current.rr),
            "ratingPoints": ranks.rating_points(current.tier, current.rr),
            "leaderboardRank": leaderboard,
            "peak": peak_block,
            "previousAct": previous_block,
            "act": {
                "hidden": slot.flags.act_rank_hidden,
                "games": act_games,
                "wins": act_wins,
                "losses": max(0, act_games - act_wins),
                "winrate": round(act_wins / act_games, 3) if act_games else None,
                "act": current_act.to_dict() if current_act else None,
            },
            "level": None if slot.flags.hide_account_level else slot.account_level,
            "levelHidden": slot.flags.hide_account_level,
            "party": party.to_dict() if party else None,
            "smurf": verdict.to_dict(),
            "stats": stats.to_dict() if stats else None,
            "notes": {
                "count": len(note_items),
                "maxSeverity": max_severity,
                "items": note_items,
            },
            "encounters": encounters or {"count": 0, "ally": 0, "enemy": 0,
                                         "firstSeen": None, "lastSeen": None},
            "privacy": {
                "incognito": slot.flags.incognito,
                "hideAccountLevel": slot.flags.hide_account_level,
                "actRankHidden": slot.flags.act_rank_hidden,
                "leaderboardAnonymized": slot.flags.leaderboard_anonymized,
                "reasons": slot.flags.reasons(),
            },
        }

    async def record_encounters(self, snapshot: dict) -> bool:
        """Write a finished match's roster into the encounter log.

        Deliberately *not* called from :meth:`build`. Agent select is a lobby
        you can still dodge, and a dodged lobby is not an encounter - so the
        poller calls this only once the match has actually ended.
        """
        match = snapshot.get("match") or {}
        match_id = match.get("id")
        if not match_id or match_id == self._last_recorded_match:
            return False
        self._last_recorded_match = match_id

        queue = match.get("queue") or match.get("mode") or ""
        map_name = match.get("map") or ""
        recorded = 0
        for team in snapshot.get("teams") or []:
            for player in team.get("players") or []:
                if player.get("isSelf"):
                    continue
                name = player.get("name") or {}
                alias, tag = ((name.get("gameName"), name.get("tagLine"))
                              if not name.get("hidden") else (None, None))
                await self._notes.touch_player(player["puuid"], alias, tag)
                if await self._notes.record_encounter(
                    player["puuid"],
                    match_id,
                    player.get("side", "unknown"),
                    agent=(player.get("agent") or {}).get("name"),
                    map_name=map_name,
                    queue=queue,
                ):
                    recorded += 1
        log.info("Recorded %d encounters for match %s", recorded, match_id[:8])
        return recorded > 0


def tracker_url(display) -> str | None:
    """One-click deep link to the player's tracker.gg profile.

    Returns None whenever the riot ID is hidden, so a Streamer Mode player
    cannot be looked up through a link SpikeSight generated.
    """
    if display.hidden or not display.game_name:
        return None
    handle = f"{display.game_name}#{display.tag_line or ''}".rstrip("#")
    return (
        "https://tracker.gg/valorant/profile/riot/"
        f"{quote(handle, safe='')}/overview"
    )


def _sort_key(player: dict):
    points = player.get("ratingPoints")
    return (0 if points is not None else 1, -(points or 0), player["name"]["display"].lower())


# Queue ids are stable and human-meaningful, so they are the preferred label.
_QUEUE_LABELS = {
    "competitive": "Competitive",
    "unrated": "Unrated",
    "swiftplay": "Swiftplay",
    "spikerush": "Spike Rush",
    "deathmatch": "Deathmatch",
    "ggteam": "Escalation",
    "onefa": "Replication",
    "hurm": "Team Deathmatch",
    "snowball": "Snowball Fight",
    "newmap": "New Map",
    "premier": "Premier",
    "premiermode": "Premier",
}

# Mode asset names are internal and messy - Swiftplay's is
# "Swiftplay_EoRCredits_GameMode" (end-of-round credits, a dev folder name).
# Matching on a substring survives that kind of variant suffix.
_MODE_MATCHES = [
    ("swiftplay", "Swiftplay"),
    ("quickbomb", "Spike Rush"),
    ("deathmatch", "Deathmatch"),
    ("gungameteams", "Escalation"),
    ("gungame", "Escalation"),
    ("oneforall", "Replication"),
    ("snowball", "Snowball Fight"),
    ("hurm", "Team Deathmatch"),
    ("newmap", "New Map"),
    ("bomb", "Standard"),
]

_NOISE_TOKENS = ("gamemode", "eorcredits", "endofroundcredits", "development")


def _mode_name(mode: str) -> str:
    """Turn a ModeID asset path into something a human would say.

    ``/Game/GameModes/Bomb/BombGameMode.BombGameMode_C``            -> Standard
    ``.../Swiftplay_EoRCredits_GameMode.Swiftplay_EoRCredits_..._C`` -> Swiftplay
    """
    if not mode:
        return ""
    tail = mode.rstrip("/").split("/")[-1].split(".")[0]
    key = tail.lower().removesuffix("_c").replace("_", "")
    for token, label in _MODE_MATCHES:
        if token in key:
            return label
    for noise in _NOISE_TOKENS:
        key = key.replace(noise, "")
    return key.title() or tail


def _match_label(queue: str, mode: str, provisioning: str) -> str:
    """One short phrase for the header: what kind of game is this."""
    if provisioning and provisioning.lower() == "customgame":
        mode_label = _mode_name(mode)
        return f"Custom Game ({mode_label})" if mode_label else "Custom Game"
    queue_key = (queue or "").lower().replace("_", "")
    if queue_key in _QUEUE_LABELS:
        return _QUEUE_LABELS[queue_key]
    return _mode_name(mode) or (queue or "Unknown")


def _build_alerts(players: list[dict], state: str) -> list[dict]:
    alerts: list[dict] = []

    flagged = [p for p in players if p["notes"]["count"] > 0 and not p["isSelf"]]
    if flagged:
        worst = max(p["notes"]["maxSeverity"] for p in flagged)
        names = ", ".join(p["name"]["display"] for p in flagged[:4])
        more = f" +{len(flagged) - 4} more" if len(flagged) > 4 else ""
        alerts.append(
            {
                "level": "danger" if worst >= 3 else "warning",
                "title": f"{len(flagged)} flagged player{'s' if len(flagged) > 1 else ''} in this lobby",
                "text": f"{names}{more}",
                "dodgeable": state == "PREGAME",
            }
        )

    smurfs = [p for p in players if p["smurf"]["flagged"] and not p["isSelf"]]
    if smurfs:
        enemy_smurfs = [p for p in smurfs if p["side"] == "enemy"]
        alerts.append(
            {
                "level": "warning" if enemy_smurfs else "info",
                "title": f"{len(smurfs)} possible smurf{'s' if len(smurfs) > 1 else ''}",
                "text": ", ".join(p["name"]["display"] for p in smurfs[:5]),
                "dodgeable": False,
            }
        )

    stacks = {}
    for player in players:
        party = player.get("party")
        if party:
            stacks.setdefault(party["group"], []).append(player)
    big = [group for group in stacks.values() if len(group) >= 3]
    for group in big:
        side = group[0]["side"]
        alerts.append(
            {
                "level": "info",
                "title": f"{len(group)}-stack on the {'enemy' if side == 'enemy' else 'ally'} team",
                "text": ", ".join(p["name"]["display"] for p in group),
                "dodgeable": False,
            }
        )
    return alerts
