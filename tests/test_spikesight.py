"""Unit tests for the pure logic. Run with:  python -m unittest discover tests

Nothing here touches the network or the Riot Client; the scoreboard tests use a
stub remote so the privacy rules can be asserted against fixed payloads.
"""

from __future__ import annotations

import asyncio
import copy
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spikesight import (  # noqa: E402
    career,
    content,
    parties,
    privacy,
    ranks,
    smurf,
    winprob,
)
from spikesight.config import Config, DEFAULTS  # noqa: E402
from spikesight.notes import NotesStore, _is_healthy  # noqa: E402
from spikesight.scoreboard import (  # noqa: E402
    ScoreboardBuilder,
    _match_label,
    _mode_name,
)
from spikesight.stats import PlayerStats, StatsResult, _summarise_match  # noqa: E402


def cfg() -> Config:
    return Config(DEFAULTS)


class TestRanks(unittest.TestCase):
    def test_tier_names(self):
        self.assertEqual(ranks.tier(0).name, "Unranked")
        self.assertEqual(ranks.tier(3).name, "Iron 1")
        self.assertEqual(ranks.tier(11).name, "Silver 3")
        self.assertEqual(ranks.tier(20).name, "Diamond 3")
        self.assertEqual(ranks.tier(24).name, "Immortal 1")
        self.assertEqual(ranks.tier(27).name, "Radiant")

    def test_out_of_range_is_clamped(self):
        self.assertEqual(ranks.tier(999).name, "Radiant")
        self.assertEqual(ranks.tier(-5).name, "Unranked")
        self.assertEqual(ranks.tier(None).name, "Unranked")

    def test_rating_points_are_monotonic(self):
        ladder = [(3, 0), (3, 50), (4, 0), (11, 99), (12, 0), (20, 100)]
        values = [ranks.rating_points(t, rr) for t, rr in ladder]
        self.assertEqual(values, sorted(values))

    def test_unranked_has_no_points(self):
        self.assertIsNone(ranks.rating_points(0, 0))

    def test_immortal_rr_is_clamped(self):
        # A 900 RR Immortal must not swamp a team average.
        self.assertEqual(
            ranks.rating_points(24, 900), ranks.rating_points(24, 300)
        )

    def test_points_round_trip(self):
        points = ranks.rating_points(19, 47)
        self.assertEqual(ranks.points_to_label(points), "Diamond 2 (47 RR)")


class TestPrivacy(unittest.TestCase):
    def test_incognito_name_is_never_resolved(self):
        players = [
            {"puuid": "a", "flags": privacy.PrivacyFlags(incognito=True)},
            {"puuid": "b", "flags": privacy.PrivacyFlags()},
        ]
        self.assertEqual(privacy.resolvable_puuids(players), ["b"])

    def test_incognito_display_falls_back_to_agent(self):
        name = privacy.build_display_name(
            flags=privacy.PrivacyFlags(incognito=True),
            name_record={"game_name": "Leaked", "tag_line": "NA1"},
            agent_name="Jett",
            is_self=False,
        )
        self.assertEqual(name.display, "Jett")
        self.assertTrue(name.hidden)
        self.assertIsNone(name.game_name)

    def test_hidden_players_get_a_stable_distinct_code(self):
        # The whole point: two hidden players on the same agent must not be
        # interchangeable, or a flag on one silently means "all Brimstones".
        codes = [
            privacy.build_display_name(
                flags=privacy.PrivacyFlags(incognito=True),
                name_record=None, agent_name="Brimstone", is_self=False,
                puuid=puuid,
            ).hidden_id
            for puuid in ("player-one", "player-two")
        ]
        self.assertEqual(len(set(codes)), 2)
        self.assertTrue(all(len(c) == 4 for c in codes))

        # And it is the same code every time you meet them.
        again = privacy.build_display_name(
            flags=privacy.PrivacyFlags(incognito=True),
            name_record=None, agent_name="Sova", is_self=False,
            puuid="player-one",
        )
        self.assertEqual(again.hidden_id, codes[0])

    def test_the_code_says_nothing_about_the_player(self):
        # It is derived from the account id, never from the riot ID, so a
        # hidden name cannot be read back out of it.
        with_name = privacy.build_display_name(
            flags=privacy.PrivacyFlags(incognito=True),
            name_record={"game_name": "Leaked", "tag_line": "NA1"},
            agent_name="Jett", is_self=False, puuid="p",
        )
        without = privacy.build_display_name(
            flags=privacy.PrivacyFlags(incognito=True),
            name_record=None, agent_name="Jett", is_self=False, puuid="p",
        )
        self.assertEqual(with_name.hidden_id, without.hidden_id)
        self.assertNotIn("Leaked", with_name.to_dict()["display"])

    def test_a_visible_player_gets_no_code(self):
        name = privacy.build_display_name(
            flags=privacy.PrivacyFlags(),
            name_record={"game_name": "Open", "tag_line": "NA1"},
            agent_name="Jett", is_self=False, puuid="p",
        )
        self.assertIsNone(name.hidden_id)

    def test_hidden_names_are_not_persisted(self):
        flags = privacy.PrivacyFlags(incognito=True)
        name = privacy.build_display_name(
            flags=flags, name_record={"game_name": "X", "tag_line": "1"},
            agent_name="Sage", is_self=False,
        )
        self.assertEqual(privacy.storable_alias(flags, name), (None, None))

    def test_a_party_member_is_named_from_the_local_client(self):
        # Riot's Streamer Mode hides a player from strangers. The game itself
        # shows you your own party, and this name came from the local presence
        # feed - it was never requested from Riot.
        name = privacy.build_display_name(
            flags=privacy.PrivacyFlags(incognito=True),
            name_record=None,
            agent_name="Skye",
            is_self=False,
            party_name={"game_name": "friend", "tag_line": "1234"},
        )
        self.assertEqual(name.display, "friend#1234")
        self.assertFalse(name.hidden)
        self.assertIn("your party", name.reason)

    def test_everyone_else_stays_hidden_even_with_a_name_to_hand(self):
        name = privacy.build_display_name(
            flags=privacy.PrivacyFlags(incognito=True),
            name_record={"game_name": "Stranger", "tag_line": "NA1"},
            agent_name="Jett",
            is_self=False,
            party_name=None,
        )
        self.assertTrue(name.hidden)
        self.assertNotIn("Stranger", name.display)

    def test_flag_merge(self):
        merged = privacy.PrivacyFlags(incognito=True).merge(
            privacy.PrivacyFlags(act_rank_hidden=True)
        )
        self.assertTrue(merged.incognito)
        self.assertTrue(merged.act_rank_hidden)


class TestSmurf(unittest.TestCase):
    def test_high_rank_low_level_is_flagged(self):
        verdict = smurf.evaluate(
            cfg=cfg(), account_level=22, level_hidden=False,
            current_tier=22, peak_tier=22, act_games=12, act_wins=10,
            kd=1.8, recent_matches=5, recent_winrate=0.8,
        )
        self.assertTrue(verdict.flagged)
        self.assertGreater(verdict.score, 45)

    def test_veteran_at_high_rank_is_not_flagged(self):
        verdict = smurf.evaluate(
            cfg=cfg(), account_level=430, level_hidden=False,
            current_tier=24, peak_tier=25, act_games=90, act_wins=48,
            kd=1.1, recent_matches=5, recent_winrate=0.5,
        )
        self.assertFalse(verdict.flagged)

    def test_hidden_level_is_excluded_from_scoring(self):
        with_level = smurf.evaluate(
            cfg=cfg(), account_level=15, level_hidden=False,
            current_tier=21, peak_tier=21, act_games=10, act_wins=5,
            kd=1.0, recent_matches=5, recent_winrate=0.5,
        )
        hidden = smurf.evaluate(
            cfg=cfg(), account_level=15, level_hidden=True,
            current_tier=21, peak_tier=21, act_games=10, act_wins=5,
            kd=1.0, recent_matches=5, recent_winrate=0.5,
        )
        self.assertGreater(with_level.score, hidden.score)
        self.assertTrue(hidden.level_hidden)

    def test_every_signal_is_explained(self):
        verdict = smurf.evaluate(
            cfg=cfg(), account_level=20, level_hidden=False,
            current_tier=21, peak_tier=21, act_games=12, act_wins=10,
            kd=2.0, recent_matches=5, recent_winrate=0.9,
        )
        self.assertTrue(verdict.signals)
        for signal in verdict.signals:
            self.assertTrue(signal.label and signal.detail and signal.weight > 0)


def observed(*groups) -> tuple[dict, dict]:
    """Build (co_party, co_parties) the way StatsEngine.collect does.

    Each argument is (members, times_seen). Deriving both from one source keeps
    the tests honest: real data can never have a group without its pairs.
    """
    from itertools import combinations

    pairs: dict[tuple[str, str], int] = {}
    sets: dict[frozenset, int] = {}
    for members, seen in groups:
        members = sorted(members)
        sets[frozenset(members)] = sets.get(frozenset(members), 0) + seen
        for pair in combinations(members, 2):
            pairs[pair] = pairs.get(pair, 0) + seen
    return pairs, sets


class TestParties(unittest.TestCase):
    def test_shared_party_id_is_confirmed(self):
        result = parties.detect(
            teams={"Blue": ["a", "b", "c"]},
            party_ids={"a": "p1", "b": "p1", "c": "p2"},
        )
        self.assertEqual(result["a"].confidence, "confirmed")
        self.assertEqual(result["a"].group, result["b"].group)
        self.assertNotIn("c", result)

    def test_solo_party_id_is_not_a_group(self):
        result = parties.detect(teams={"Blue": ["a", "b"]},
                                party_ids={"a": "solo", "b": "other"})
        self.assertEqual(result, {})

    def test_co_occurrence_gives_a_likely_pair(self):
        result = parties.detect(
            teams={"Red": ["x", "y", "z"]},
            party_ids={},
            co_party={("x", "y"): 3, ("y", "z"): 1},
        )
        self.assertEqual(result["x"].confidence, "likely")
        self.assertEqual(result["x"].group, result["y"].group)
        self.assertNotIn("z", result)

    def test_a_five_stack_is_detected_at_its_real_size(self):
        five = ["a", "b", "c", "d", "e"]
        co_party, co_parties = observed((five, 3))
        result = parties.detect(
            teams={"Red": five},
            party_ids={},
            co_party=co_party,
            co_parties=co_parties,
        )
        self.assertEqual(len(result), 5)
        for puuid in five:
            self.assertEqual(result[puuid].size, 5)
            self.assertEqual(result[puuid].group, result["a"].group)
        self.assertIn("5-stack", result["a"].evidence)

    def test_pairs_are_never_chained_into_a_bigger_stack(self):
        # A duos with B and B duos with C, in different matches. That is not a
        # three-stack, and inventing one would be worse than saying nothing.
        result = parties.detect(
            teams={"Red": ["a", "b", "c"]},
            party_ids={},
            co_party={("a", "b"): 4, ("b", "c"): 4},
        )
        sizes = {p: a.size for p, a in result.items()}
        self.assertTrue(all(size == 2 for size in sizes.values()), sizes)
        self.assertEqual(len(result), 2)

    def test_a_trio_beats_the_pairs_inside_it(self):
        trio = ["a", "b", "c"]
        co_party, co_parties = observed((trio, 3))
        result = parties.detect(
            teams={"Red": trio + ["d"]},
            party_ids={},
            co_party=co_party,
            co_parties=co_parties,
        )
        self.assertEqual({result[p].size for p in trio}, {3})
        self.assertEqual(len({result[p].group for p in trio}), 1)
        self.assertIn("3-stack", result["a"].evidence)
        self.assertNotIn("d", result)

    def test_a_confirmed_party_is_not_overridden_by_inference(self):
        co_party, co_parties = observed((["a", "b", "c"], 5))
        result = parties.detect(
            teams={"Red": ["a", "b", "c"]},
            party_ids={"a": "p1", "b": "p1"},
            co_party=co_party,
            co_parties=co_parties,
        )
        self.assertEqual(result["a"].confidence, "confirmed")
        self.assertEqual(result["a"].size, 2)
        self.assertNotIn("c", result)

    def test_only_the_players_present_are_counted(self):
        # Four friends usually queue together but only three are in this lobby,
        # so it is a three-stack today - never a "4-stack".
        co_party, co_parties = observed((["a", "b", "c", "d"], 5))
        result = parties.detect(
            teams={"Red": ["a", "b", "c"]},
            party_ids={},
            co_party=co_party,
            co_parties=co_parties,
        )
        self.assertEqual({result[p].size for p in ("a", "b", "c")}, {3})

    def test_a_group_that_never_recurs_is_ignored(self):
        co_party, co_parties = observed((["a", "b", "c"], 1))
        result = parties.detect(
            teams={"Red": ["a", "b", "c"]},
            party_ids={},
            co_party=co_party,
            co_parties=co_parties,
        )
        self.assertEqual(result, {})

    def test_a_duo_inside_a_bigger_group_still_registers(self):
        # The trio only played together once, but two of them keep duoing.
        co_party, co_parties = observed(
            (["a", "b", "c"], 1), (["a", "b"], 3)
        )
        result = parties.detect(
            teams={"Red": ["a", "b", "c"]},
            party_ids={},
            co_party=co_party,
            co_parties=co_parties,
        )
        self.assertEqual({result[p].size for p in ("a", "b")}, {2})
        self.assertNotIn("c", result)

    def test_parties_never_span_teams(self):
        result = parties.detect(
            teams={"Blue": ["a"], "Red": ["b"]},
            party_ids={"a": "p1", "b": "p1"},
        )
        self.assertEqual(result, {})

    def test_your_party_is_everyone_sharing_your_id(self):
        members = parties.your_party(
            {"me": "p1", "friend": "p1", "stranger": "p2"}, "me"
        )
        self.assertEqual(members, {"me", "friend"})

    def test_your_party_is_just_you_when_solo(self):
        self.assertEqual(parties.your_party({"other": "p9"}, "me"), {"me"})

    def test_presence_hints(self):
        class P:
            def __init__(self, puuid, party_id):
                self.puuid = puuid
                self.party_id = party_id

        hints = parties.hints_from_presence([P("a", "p1"), P("b", "")])
        self.assertEqual(hints, {"a": "p1"})


class TestWinProbability(unittest.TestCase):
    def test_even_teams_are_a_coin_flip(self):
        side = [{"points": 1000, "kd": None} for _ in range(5)]
        result = winprob.estimate(cfg=cfg(), ally=list(side), enemy=list(side))
        self.assertAlmostEqual(result.probability, 0.5, places=3)

    def test_stronger_team_is_favoured(self):
        ally = [{"points": 1400, "kd": None} for _ in range(5)]
        enemy = [{"points": 1000, "kd": None} for _ in range(5)]
        result = winprob.estimate(cfg=cfg(), ally=ally, enemy=enemy)
        self.assertGreater(result.probability, 0.9)

    def test_unranked_players_are_imputed_not_zeroed(self):
        ally = [{"points": 1000, "kd": None}] * 4 + [{"points": None, "kd": None}]
        enemy = [{"points": 1000, "kd": None}] * 5
        result = winprob.estimate(cfg=cfg(), ally=ally, enemy=enemy)
        # Imputed at the lobby average, so the sides stay even rather than the
        # unranked player dragging the team down to Iron.
        self.assertAlmostEqual(result.probability, 0.5, places=2)
        self.assertEqual(result.knownRanks if hasattr(result, "knownRanks")
                         else result.known_ranks, 9)

    def test_confidence_drops_when_ranks_are_missing(self):
        ally = [{"points": None, "kd": None}] * 4 + [{"points": 1000, "kd": None}]
        enemy = [{"points": 1000, "kd": None}] * 2 + [{"points": None, "kd": None}] * 3
        result = winprob.estimate(cfg=cfg(), ally=ally, enemy=enemy)
        self.assertEqual(result.confidence, "low")

    def test_no_ranks_at_all(self):
        side = [{"points": None, "kd": None} for _ in range(5)]
        result = winprob.estimate(cfg=cfg(), ally=side, enemy=list(side))
        self.assertEqual(result.probability, 0.5)
        self.assertEqual(result.confidence, "low")


class TestContent(unittest.TestCase):
    SEASONS = [
        {"ID": "ep-2", "Name": "V26", "Type": "episode",
         "StartTime": "2026-06-23T13:15:00Z", "IsActive": True},
        {"ID": "act-b", "Name": "ACT V", "Type": "act",
         "StartTime": "2026-08-18T13:15:00Z", "IsActive": True},
        {"ID": "act-a", "Name": "ACT IV", "Type": "act",
         "StartTime": "2026-06-23T13:15:00Z", "IsActive": False},
        {"ID": "ep-1", "Name": "EPISODE 9", "Type": "episode",
         "StartTime": "2025-01-06T13:15:00Z", "IsActive": False},
        {"ID": "act-old", "Name": "ACT II", "Type": "act",
         "StartTime": "2025-03-17T13:15:00Z", "IsActive": False},
    ]

    def build(self) -> content.GameContent:
        game = content.GameContent()
        content._apply_seasons(game, self.SEASONS)
        return game

    def test_current_and_previous_act(self):
        game = self.build()
        self.assertEqual(game.current_act().name, "ACT V")
        self.assertEqual(game.previous_act().name, "ACT IV")

    def test_short_labels(self):
        game = self.build()
        self.assertEqual(game.current_act().short, "V26A5")
        self.assertEqual(game.act("act-old").short, "E9A2")

    def test_map_name_falls_back_to_the_asset_codename(self):
        game = content.GameContent()
        self.assertEqual(game.map_name("/Game/Maps/Bonsai/Bonsai"), "Bonsai")

    def test_bundled_tables_load(self):
        game = content._base_content()
        self.assertGreater(len(game.agents), 20)
        self.assertEqual(game.map_name("/Game/Maps/Bonsai/Bonsai"), "Split")


SWIFTPLAY_MODE = (
    "/Game/GameModes/_Development/Swiftplay_EndOfRoundCredits/"
    "Swiftplay_EoRCredits_GameMode.Swiftplay_EoRCredits_GameMode_C"
)
BOMB_MODE = "/Game/GameModes/Bomb/BombGameMode.BombGameMode_C"


class TestModeLabels(unittest.TestCase):
    def test_swiftplay_variant_suffix_is_not_leaked(self):
        # The asset name carries Riot's internal "end of round credits" tag.
        self.assertEqual(_mode_name(SWIFTPLAY_MODE), "Swiftplay")
        self.assertNotIn("EoR", _match_label("swiftplay", SWIFTPLAY_MODE, "Matchmaking"))

    def test_queue_wins_over_mode(self):
        self.assertEqual(_match_label("competitive", BOMB_MODE, "Matchmaking"), "Competitive")
        self.assertEqual(_match_label("unrated", BOMB_MODE, "Matchmaking"), "Unrated")

    def test_mode_is_used_when_there_is_no_queue(self):
        self.assertEqual(_match_label("", BOMB_MODE, "Matchmaking"), "Standard")

    def test_custom_games_are_named(self):
        self.assertEqual(_match_label("", BOMB_MODE, "CustomGame"), "Custom Game (Standard)")

    def test_unknown_mode_does_not_crash(self):
        self.assertTrue(_match_label("", "/Game/GameModes/Foo/FooGameMode.FooGameMode_C",
                                     "Matchmaking"))
        self.assertEqual(_mode_name(""), "")


class TestStatsSummary(unittest.TestCase):
    def test_summarise_keeps_no_names(self):
        payload = {
            "matchInfo": {"matchId": "m1", "queueID": "competitive",
                          "mapId": "/Game/Maps/Ascent/Ascent",
                          "gameStartMillis": 1, "isCompleted": True},
            "teams": [{"teamId": "Blue", "won": True},
                      {"teamId": "Red", "won": False}],
            "players": [
                {"subject": "p1", "gameName": "Leaky", "tagLine": "NA1",
                 "teamId": "Blue", "partyId": "party-1", "characterId": "c",
                 "competitiveTier": 18,
                 "stats": {"kills": 20, "deaths": 10, "assists": 4, "roundsPlayed": 20,
                           "score": 5000}},
            ],
        }
        summary = _summarise_match(payload)
        self.assertEqual(summary["matchId"], "m1")
        self.assertNotIn("Leaky", str(summary))
        self.assertEqual(summary["players"][0]["p"], "party-1")


class _CareerRemote:
    def __init__(self, updates, history):
        self._updates = updates
        self._history = history

    async def mmr(self, puuid):
        return None

    async def competitive_updates(self, puuid, count=15):
        return {"Matches": self._updates}

    async def match_history(self, puuid, count=10, queue="competitive"):
        return {"History": self._history}


class _CareerStats:
    def __init__(self, summaries):
        self._summaries = summaries

    async def match_summary(self, match_id):
        return self._summaries.get(match_id)


def _match_summary(match_id, kills, deaths, won):
    return {
        "matchId": match_id, "queue": "competitive",
        "mapId": "/Game/Maps/Ascent/Ascent", "start": 1000, "completed": True,
        "won": {"Blue": won, "Red": not won},
        "scores": {"Blue": 13 if won else 7, "Red": 7 if won else 13},
        "players": [{"s": "me", "t": "Blue", "p": "", "c": "agent1", "ct": 17,
                     "k": kills, "d": deaths, "a": 4, "r": 20, "sc": 4400}],
    }


class TestCareer(unittest.IsolatedAsyncioTestCase):
    async def test_rr_and_stats_are_joined_per_match(self):
        updates = [
            {"MatchID": "m1", "MatchStartTime": 2000, "QueueID": "competitive",
             "MapID": "/Game/Maps/Ascent/Ascent", "RankedRatingEarned": 18,
             "RankedRatingAfterUpdate": 40, "RankedRatingBeforeUpdate": 22,
             "TierAfterUpdate": 17, "TierBeforeUpdate": 17},
            {"MatchID": "m2", "MatchStartTime": 1000, "QueueID": "competitive",
             "MapID": "/Game/Maps/Ascent/Ascent", "RankedRatingEarned": -20,
             "RankedRatingAfterUpdate": 22, "RankedRatingBeforeUpdate": 42,
             "TierAfterUpdate": 17, "TierBeforeUpdate": 17},
        ]
        summaries = {"m1": _match_summary("m1", 24, 12, True),
                     "m2": _match_summary("m2", 10, 20, False)}
        payload = await career.build(
            remote=_CareerRemote(updates, []), content=content.GameContent(),
            stats_engine=_CareerStats(summaries), puuid="me", count=10,
        )
        self.assertEqual(len(payload["matches"]), 2)
        first = payload["matches"][0]
        self.assertEqual(first["matchId"], "m1")          # newest first
        self.assertEqual(first["rrEarned"], 18)
        self.assertEqual(first["kills"], 24)
        self.assertEqual((first["roundsWon"], first["roundsLost"]), (13, 7))
        self.assertTrue(first["won"])
        self.assertEqual(payload["summary"]["wins"], 1)
        self.assertEqual(payload["summary"]["losses"], 1)
        self.assertEqual(payload["summary"]["rrNet"], -2)

    async def test_unranked_matches_appear_without_rr(self):
        history = [{"MatchID": "swift", "GameStartTime": 500}]
        summaries = {"swift": {**_match_summary("swift", 15, 15, True),
                               "queue": "swiftplay"}}
        payload = await career.build(
            remote=_CareerRemote([], history), content=content.GameContent(),
            stats_engine=_CareerStats(summaries), puuid="me", count=10,
        )
        self.assertEqual(len(payload["matches"]), 1)
        self.assertIsNone(payload["matches"][0]["rrEarned"])
        self.assertEqual(payload["summary"]["rankedMatches"], 0)

    async def test_timeline_is_oldest_first(self):
        updates = [
            {"MatchID": "new", "MatchStartTime": 3000, "RankedRatingEarned": 10,
             "RankedRatingAfterUpdate": 50, "TierAfterUpdate": 17},
            {"MatchID": "old", "MatchStartTime": 1000, "RankedRatingEarned": 5,
             "RankedRatingAfterUpdate": 40, "TierAfterUpdate": 17},
        ]
        payload = await career.build(
            remote=_CareerRemote(updates, []), content=content.GameContent(),
            stats_engine=_CareerStats({}), puuid="me", count=10,
        )
        self.assertEqual([t["matchId"] for t in payload["rrTimeline"]], ["old", "new"])

    async def test_count_is_clamped(self):
        payload = await career.build(
            remote=_CareerRemote([], []), content=content.GameContent(),
            stats_engine=_CareerStats({}), puuid="me", count=9999,
        )
        self.assertEqual(payload["matches"], [])


class TestUserSettings(unittest.TestCase):
    """The settings layer the UI writes to, kept apart from config.toml."""

    def setUp(self):
        from spikesight import config as config_module, paths as paths_module

        self._dir = tempfile.TemporaryDirectory()
        self._settings_file = paths_module.USER_SETTINGS_FILE
        self._data_dir = paths_module.DATA_DIR
        paths_module.DATA_DIR = Path(self._dir.name)
        paths_module.USER_SETTINGS_FILE = Path(self._dir.name) / "settings.json"
        self.config_module = config_module
        self.paths_module = paths_module

    def tearDown(self):
        self.paths_module.USER_SETTINGS_FILE = self._settings_file
        self.paths_module.DATA_DIR = self._data_dir
        self._dir.cleanup()

    def test_saved_toggles_override_defaults(self):
        self.config_module.save_user_settings({"tray.close_to_tray": True})
        merged = self.config_module._deep_merge(
            self.config_module.DEFAULTS, self.config_module._load_user_settings()
        )
        self.assertTrue(merged["tray"]["close_to_tray"])
        self.assertFalse(merged["tray"]["minimize_to_tray"])

    def test_saves_are_cumulative(self):
        self.config_module.save_user_settings({"tray.close_to_tray": True})
        self.config_module.save_user_settings({"app.start_with_windows": True})
        saved = self.config_module._load_user_settings()
        self.assertTrue(saved["tray"]["close_to_tray"])
        self.assertTrue(saved["app"]["start_with_windows"])

    def test_unknown_keys_are_refused(self):
        with self.assertRaises(KeyError):
            self.config_module.save_user_settings({"app.host": "0.0.0.0"})

    def test_values_are_coerced_to_the_declared_type(self):
        self.config_module.save_user_settings({"tray.close_to_tray": "yes"})
        self.assertIs(self.config_module._load_user_settings()["tray"]["close_to_tray"], True)

    def test_a_file_with_a_byte_order_mark_still_loads(self):
        # Notepad and PowerShell both write UTF-8 with a BOM by default, and a
        # BOM used to make json.loads fail, silently reverting every setting.
        self.paths_module.USER_SETTINGS_FILE.write_text(
            '{"tray.close_to_tray": true}', encoding="utf-8-sig"
        )
        loaded = self.config_module._load_user_settings()
        self.assertTrue(loaded["tray"]["close_to_tray"])

    def test_a_corrupt_file_is_ignored_not_fatal(self):
        self.paths_module.USER_SETTINGS_FILE.write_text("{ broken", encoding="utf-8")
        self.assertEqual(self.config_module._load_user_settings(), {})

    def test_everything_defaults_to_off(self):
        for path in ("tray.minimize_to_tray", "tray.close_to_tray",
                     "app.start_with_windows"):
            section, _, key = path.partition(".")
            self.assertFalse(
                self.config_module.DEFAULTS[section][key],
                f"{path} should default to off",
            )


class TestConfigMigration(unittest.TestCase):
    """Stale generated defaults get updated without eating the user's file."""

    def setUp(self):
        from spikesight import config as config_module, paths as paths_module

        self._dir = tempfile.TemporaryDirectory()
        self._original = paths_module.CONFIG_FILE
        paths_module.CONFIG_FILE = Path(self._dir.name) / "config.toml"
        self.config_module = config_module
        self.paths_module = paths_module

    def tearDown(self):
        self.paths_module.CONFIG_FILE = self._original
        self._dir.cleanup()

    def write(self, *lines: str) -> None:
        self.paths_module.CONFIG_FILE.write_text(
            chr(10).join(lines) + chr(10), encoding="utf-8"
        )

    def read(self) -> str:
        return self.paths_module.CONFIG_FILE.read_text(encoding="utf-8")

    def test_the_old_default_is_updated(self):
        self.write("[stats]", "queue = \"competitive\"")
        self.config_module._migrate_config_file()
        self.assertIn("queue = \"auto\"", self.read())

    def test_comments_and_other_settings_survive(self):
        self.write(
            "# my notes",
            "[stats]",
            "# keep this comment",
            "queue = \"competitive\"",
            "match_history_depth = 9",
        )
        self.config_module._migrate_config_file()
        text = self.read()
        self.assertIn("# my notes", text)
        self.assertIn("# keep this comment", text)
        self.assertIn("match_history_depth = 9", text)
        self.assertIn("queue = \"auto\"", text)

    def test_a_deliberate_value_is_left_alone(self):
        self.write("[stats]", "queue = \"unrated\"")
        self.config_module._migrate_config_file()
        self.assertIn("queue = \"unrated\"", self.read())

    def test_running_twice_changes_nothing_further(self):
        self.write("[stats]", "queue = \"competitive\"")
        self.config_module._migrate_config_file()
        once = self.read()
        self.config_module._migrate_config_file()
        self.assertEqual(once, self.read())

    def test_a_missing_file_is_not_an_error(self):
        self.paths_module.CONFIG_FILE.unlink(missing_ok=True)
        self.config_module._migrate_config_file()

class TestAutostart(unittest.TestCase):
    def test_launch_command_quotes_the_interpreter(self):
        from spikesight import autostart

        command = autostart.launch_command()
        self.assertTrue(command.startswith('"'))
        # A path with spaces must survive being handed to the shell.
        self.assertGreaterEqual(command.count('"'), 2)

    def test_frozen_builds_point_at_the_exe(self):
        from spikesight import autostart

        original = getattr(sys, "frozen", None)
        sys.frozen = True
        try:
            self.assertNotIn("-m spikesight", autostart.launch_command())
        finally:
            if original is None:
                del sys.frozen
            else:
                sys.frozen = original


class TestChangelog(unittest.TestCase):
    """The changelog is rendered in-app, so parsing it must not be fragile."""

    SAMPLE = """# Changelog

Preamble that is not part of any release.

---

## 1.0.3 - Window management

An introduction that
wraps across two lines.

### Added

- **Bold thing.** With a sentence that
  continues on the next line.
- A second item.

### Fixed

- Something broken.

---

## 1.0.0 - Initial release

### Added

- The first thing.
"""

    def parsed(self):
        from spikesight import changelog

        return changelog.parse(self.SAMPLE)

    def test_releases_are_found_newest_first(self):
        releases = self.parsed()
        self.assertEqual([r.version for r in releases], ["1.0.3", "1.0.0"])
        self.assertEqual(releases[0].title, "Window management")

    def test_preamble_is_not_swallowed_into_a_release(self):
        for release in self.parsed():
            self.assertNotIn("Preamble that is not part of any release.", release.intro)

    def test_wrapped_intro_becomes_one_paragraph(self):
        self.assertEqual(
            self.parsed()[0].intro,
            ["An introduction that wraps across two lines."],
        )

    def test_hanging_indent_joins_onto_its_bullet(self):
        added = self.parsed()[0].sections[0]
        self.assertEqual(added.heading, "Added")
        self.assertEqual(
            added.items[0],
            "**Bold thing.** With a sentence that continues on the next line.",
        )
        self.assertEqual(len(added.items), 2)

    def test_sections_keep_their_order(self):
        self.assertEqual(
            [s.heading for s in self.parsed()[0].sections], ["Added", "Fixed"]
        )

    def test_the_real_changelog_parses_and_covers_this_build(self):
        from spikesight import __version__, changelog

        releases = changelog.load()
        self.assertTrue(releases, "CHANGELOG.md should ship with the app")
        versions = [r["version"] for r in releases]
        self.assertIn(__version__, versions)
        # Newest first, and every release says something.
        self.assertEqual(versions[0], __version__)
        for release in releases:
            self.assertTrue(release["sections"] or release["intro"], release["version"])


class TestMatchCacheVersion(unittest.TestCase):
    def test_older_entries_are_treated_as_misses(self):
        from spikesight import cache as cache_module

        with tempfile.TemporaryDirectory() as tmp:
            store = cache_module.MatchCache()
            store._dir = Path(tmp)  # noqa: SLF001 - test isolation
            store._memory.clear()   # noqa: SLF001

            (Path(tmp) / "old.json").write_text('{"matchId":"old","v":1}',
                                                encoding="utf-8")
            self.assertIsNone(store.peek("old"))

            store.put("new", {"matchId": "new"})
            store._memory.clear()   # noqa: SLF001 - force the disk path
            self.assertEqual(store.peek("new")["matchId"], "new")


class TestDamagedNotesDatabase(unittest.IsolatedAsyncioTestCase):
    """The notes file is the one thing here that cannot be re-derived."""

    async def _seed(self, folder: Path) -> Path:
        path = folder / "notes.sqlite3"
        store = NotesStore(path)
        for index in range(40):
            await store.touch_player(f"p{index}", f"Name{index}", "TAG")
            await store.record_encounter(f"p{index}", f"m{index}", "enemy")
        await store.add_note("p7", 3, ["thrower"], "threw round nine")
        store.close()
        return path

    @staticmethod
    def _damage(path: Path) -> None:
        """Scribble over a page in the middle, the way real corruption looks."""
        raw = bytearray(path.read_bytes())
        page = 4096
        start = page * 3
        raw[start:start + page] = b"\x00" * page
        path.write_bytes(bytes(raw))

    async def test_a_damaged_file_is_rebuilt_instead_of_written_into(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = await self._seed(folder)
            self._damage(path)

            store = NotesStore(path)
            try:
                # It opened, it is sound, and the damaged original was kept.
                self.assertTrue(_is_healthy(store._connection))  # noqa: SLF001
                self.assertTrue(list(folder.glob("notes.sqlite3.corrupt-*")))
                # Enough survived to be worth keeping.
                rows = await store.encounter_index(limit=100)
                self.assertGreater(len(rows), 0)
                # And it is usable again, not read-only wreckage.
                await store.add_note("p1", 2, ["toxic"], "after the rebuild")
                self.assertEqual(len((await store.notes_for(["p1"]))["p1"]), 1)
            finally:
                store.close()

    async def test_recovery_keeps_the_wal_instead_of_deleting_it(self):
        # In WAL mode the newest commits sit in the -wal sidecar until a
        # checkpoint folds them in. Recovery used to delete it before moving
        # the database aside, which threw away the most recent notes - exactly
        # the ones worth saving. It has to travel with its database.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = await self._seed(folder)
            self._damage(path)
            # SQLite checkpoints on the last close, so stand one in. A sidecar
            # with no valid header is ignored on open, which keeps this test
            # about where the file ends up.
            (folder / "notes.sqlite3-wal").write_bytes(b"not a real wal")

            store = NotesStore(path)
            try:
                quarantined = list(folder.glob("notes.sqlite3.corrupt-*"))
                kept = [q for q in quarantined if q.name.endswith("-wal")]
                # Moved next to the database it belongs to, rather than
                # deleted. (A -wal at the live path afterwards is the new
                # database's own, which is why that is not what we check.)
                self.assertEqual(len(kept), 1, quarantined)
            finally:
                store.close()

    async def test_the_backup_is_a_database_and_not_half_of_one(self):
        # The old backup copied the file off disk, which in WAL mode leaves
        # the recent writes behind in the sidecar.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = await self._seed(folder)

            store = NotesStore(path)
            await store.add_note("p3", 1, ["afk"], "written after the backup")
            store._make_backup()  # noqa: SLF001 - normally runs at startup
            store.close()

            backup = folder / "notes.sqlite3.backup"
            self.assertTrue(backup.exists())
            copy = sqlite3.connect(str(backup))
            try:
                self.assertEqual(
                    copy.execute("PRAGMA quick_check").fetchone()[0], "ok"
                )
                bodies = [
                    r[0] for r in copy.execute("SELECT body FROM notes")
                ]
                self.assertIn("written after the backup", bodies)
            finally:
                copy.close()


class TestNotesConcurrency(unittest.IsolatedAsyncioTestCase):
    """A cancelled query must not overlap the next one on the connection."""

    async def test_cancelling_a_query_does_not_let_the_next_one_overlap(self):
        import threading
        import time as _time

        with tempfile.TemporaryDirectory() as tmp:
            store = NotesStore(Path(tmp) / "notes.sqlite3")
            active = 0
            overlapped = False
            gate = threading.Lock()

            def slow():
                nonlocal active, overlapped
                with gate:
                    active += 1
                    overlapped = overlapped or active > 1
                _time.sleep(0.3)
                with gate:
                    active -= 1

            first = asyncio.create_task(store._run(slow))  # noqa: SLF001
            await asyncio.sleep(0.05)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            # The old asyncio.Lock released here while slow() was still running.
            await store._run(slow)  # noqa: SLF001
            store.close()
            self.assertFalse(overlapped)

    async def test_close_waits_for_work_already_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.sqlite3"
            store = NotesStore(path)
            task = asyncio.create_task(store.add_note("p1", 3, [], "mid-shutdown"))
            await asyncio.sleep(0)
            task.cancel()
            store.close()
            reopened = NotesStore(path)
            try:
                self.assertTrue(_is_healthy(reopened._connection))  # noqa: SLF001
            finally:
                reopened.close()


class TestSettingsValidation(unittest.TestCase):
    def test_choice_settings_reject_unknown_values(self):
        from spikesight.config import WRITABLE_SETTINGS

        self.assertEqual(WRITABLE_SETTINGS["ui.theme"]("Light"), "light")
        self.assertEqual(WRITABLE_SETTINGS["overlay.side"]("right"), "right")
        with self.assertRaises(ValueError):
            WRITABLE_SETTINGS["ui.theme"]("neon")
        with self.assertRaises(ValueError):
            WRITABLE_SETTINGS["overlay.side"]("top")
        self.assertIsNone(WRITABLE_SETTINGS["overlay.x"](None))
        self.assertEqual(WRITABLE_SETTINGS["overlay.x"]("0.25"), 0.25)
        with self.assertRaises(ValueError):
            WRITABLE_SETTINGS["overlay.y"](1.5)


class TestOverlayGeometry(unittest.TestCase):
    def test_frame_is_clipped_and_content_lands_on_the_margin(self):
        from spikesight import overlay

        frame = overlay.Frame.from_metrics({
            "outerWidth": 354, "innerWidth": 340,
            "outerHeight": 609, "innerHeight": 572, "contentHeight": 572,
        })
        self.assertEqual((frame.side, frame.top), (7, 30))

        spot = overlay.placement("left", 500, frame, 1.0, (0, 0, 1920, 1080))
        left, top, right, bottom = spot.region
        # Content sits exactly at the margin, the frame is outside the region.
        self.assertEqual(spot.x + left, overlay.EDGE_MARGIN)
        self.assertEqual(right - left, overlay.WIDTH)
        self.assertEqual(bottom - top, 500)

        right_side = overlay.placement("right", 500, frame, 1.5, (0, 0, 2560, 1440))
        r_left, _, r_right, _ = right_side.region
        self.assertEqual(right_side.x + r_right, 2560 - round(overlay.EDGE_MARGIN * 1.5))

    def test_a_dragged_spot_is_used_and_kept_on_screen(self):
        from spikesight import overlay

        screen = (0, 0, 1920, 1080)
        spot = overlay.placement("left", 400, overlay.Frame(), 1.0, screen, (0.5, 0.25))
        self.assertEqual(spot.x + spot.region[0], 960)
        self.assertEqual(spot.y + spot.region[1], 270)
        # Round trip: the spot saves back to the same fractions.
        self.assertEqual(
            overlay.position_from(spot.x + spot.region[0], spot.y + spot.region[1], screen),
            (0.5, 0.25),
        )
        # Dropped hanging off the right edge: pulled fully back on.
        edge = overlay.placement("left", 400, overlay.Frame(), 1.0, screen, (0.99, 0.99))
        self.assertLessEqual(edge.x + edge.region[2], 1920)
        self.assertLess(edge.y + edge.region[1], 1080)

    def test_a_tall_panel_never_runs_off_the_screen(self):
        from spikesight import overlay

        spot = overlay.placement("left", 5000, overlay.Frame(), 1.0, (0, 0, 1920, 1080))
        self.assertLessEqual(spot.y + spot.region[3], 1080)

    def test_nonsense_metrics_fall_back_to_the_measured_frame(self):
        from spikesight import overlay

        frame = overlay.Frame.from_metrics({
            "outerWidth": 900, "innerWidth": 340,
            "outerHeight": 609, "innerHeight": 572, "contentHeight": 572,
        })
        self.assertEqual(frame, overlay.Frame())


class TestMatchWindowRule(unittest.TestCase):
    """When the window should get out of the game's way, and come back."""

    def rule(self, state, enabled=True, already=False):
        from spikesight.desktop import match_window_action

        return match_window_action(state, enabled, already)

    def test_minimizes_once_a_match_starts(self):
        self.assertEqual(self.rule("INGAME"), "minimize")
        # Already done: leave it alone, so a window the user restored by hand
        # is not taken away again.
        self.assertIsNone(self.rule("INGAME", already=True))

    def test_comes_back_when_the_match_ends(self):
        self.assertEqual(self.rule("MENUS", already=True), "restore")
        self.assertEqual(self.rule("PREGAME", already=True), "restore")
        self.assertIsNone(self.rule("MENUS"))

    def test_switching_it_off_mid_match_puts_the_window_back(self):
        self.assertEqual(self.rule("INGAME", enabled=False, already=True), "restore")
        self.assertIsNone(self.rule("INGAME", enabled=False))


class TestLegacyDataMigration(unittest.TestCase):
    """Upgrading from ValScout must not cost anyone their flags."""

    def _run_migration(self, tmp):
        from spikesight import paths

        legacy = Path(tmp) / "ValScout"
        (legacy / "matchcache").mkdir(parents=True)
        (legacy / "notes.sqlite3").write_bytes(b"pretend database")
        (legacy / "notes.sqlite3-wal").write_bytes(b"recent writes")
        (legacy / "settings.json").write_text('{"overlay.enabled": true}')
        (legacy / "matchcache" / "a.json").write_text("{}")
        # The part that must NOT come across: a browser profile is a cache,
        # and on a real install it is most of a gigabyte.
        (legacy / "window" / "Cache").mkdir(parents=True)
        (legacy / "window" / "Cache" / "big.bin").write_bytes(b"x" * 4096)

        new = Path(tmp) / "SpikeSight"
        with mock.patch.object(paths, "LEGACY_DATA_DIR", legacy),              mock.patch.object(paths, "DATA_DIR", new),              mock.patch.object(paths, "MATCH_CACHE_DIR", new / "matchcache"):
            paths.ensure_data_dirs()
        return legacy, new

    def test_notes_and_settings_come_across_but_the_browser_cache_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy, new = self._run_migration(tmp)
            self.assertEqual((new / "notes.sqlite3").read_bytes(), b"pretend database")
            self.assertEqual((new / "notes.sqlite3-wal").read_bytes(), b"recent writes")
            self.assertIn("overlay.enabled", (new / "settings.json").read_text())
            self.assertTrue((new / "matchcache" / "a.json").exists())
            self.assertFalse((new / "window").exists())
            # And the old folder is untouched, so nothing is destroyed.
            self.assertTrue((legacy / "notes.sqlite3").exists())

    def test_it_does_not_run_twice(self):
        from spikesight import paths

        with tempfile.TemporaryDirectory() as tmp:
            legacy, new = self._run_migration(tmp)
            (new / "notes.sqlite3").write_bytes(b"newer, mine")
            with mock.patch.object(paths, "LEGACY_DATA_DIR", legacy),                  mock.patch.object(paths, "DATA_DIR", new),                  mock.patch.object(paths, "MATCH_CACHE_DIR", new / "matchcache"):
                self.assertFalse(paths.migrate_legacy_data())
            self.assertEqual((new / "notes.sqlite3").read_bytes(), b"newer, mine")


class TestOverlayTopmost(unittest.TestCase):
    """Always-on-top is the whole feature: without it the panel is invisible."""

    def test_a_window_without_always_on_top_is_rebuilt_not_shown(self):
        from unittest import mock

        from spikesight import overlay

        window = overlay.OverlayWindow("http://127.0.0.1/overlay")
        window._hwnd = 1234           # noqa: SLF001 - stand-in for a real window
        window._process = mock.Mock(**{"poll.return_value": None})  # noqa: SLF001

        with mock.patch.object(overlay, "is_topmost", return_value=False),              mock.patch.object(window, "_adopt"),              mock.patch.object(window, "_place") as place,              mock.patch.object(window, "stop") as stop,              mock.patch.object(window, "ensure_started") as started:
            window.update(True, "left")
            self.assertTrue(stop.called and started.called)
            self.assertFalse(place.called, "must not show a window nobody can see")

            # Cooling off: it does not thrash the window every tick.
            stop.reset_mock()
            window.update(True, "left")
            self.assertFalse(stop.called)

            # Still no luck after a couple of goes: show it anyway.
            window._topmost_failures = 2   # noqa: SLF001
            window._rebuilt_at = 0.0       # noqa: SLF001
            window.update(True, "left")
            self.assertTrue(place.called)

    def test_a_topmost_window_is_shown_straight_away(self):
        from unittest import mock

        from spikesight import overlay

        window = overlay.OverlayWindow("http://127.0.0.1/overlay")
        window._hwnd = 1234  # noqa: SLF001
        window._process = mock.Mock(**{"poll.return_value": None})  # noqa: SLF001
        with mock.patch.object(overlay, "is_topmost", return_value=True),              mock.patch.object(window, "_adopt"),              mock.patch.object(window, "_place") as place,              mock.patch.object(window, "stop") as stop:
            window.update(True, "left")
            self.assertTrue(place.called)
            self.assertFalse(stop.called)


class TestOverlayAppearance(unittest.TestCase):
    """The panel must never be seen half-dressed."""

    def _window(self):
        from unittest import mock

        from spikesight import overlay

        window = overlay.OverlayWindow("http://127.0.0.1/overlay")
        window._hwnd = 1234  # noqa: SLF001
        window._process = mock.Mock(**{"poll.return_value": None})  # noqa: SLF001
        return window, overlay

    def test_it_paints_off_screen_before_it_is_shown(self):
        from unittest import mock

        window, overlay = self._window()
        with mock.patch.object(overlay, "is_topmost", return_value=True),              mock.patch.object(window, "_adopt"),              mock.patch.object(window, "_place") as place:
            window.update(True, "right")
            place.assert_called_once_with(onscreen=False)
            self.assertFalse(window._visible)  # noqa: SLF001

            # Too soon: still painting where nobody can see it.
            place.reset_mock()
            window.update(True, "right")
            self.assertFalse(place.called)

            # Waited long enough, but the page has not said how big it is
            # yet: showing now would mean resizing in front of the user.
            window._warming_since -= overlay.WARMUP_SECONDS + 0.1  # noqa: SLF001
            window.update(True, "right")
            self.assertFalse(place.called)

            # Size reported: in it goes.
            window._sized = True  # noqa: SLF001
            window.update(True, "right")
            place.assert_called_once_with(onscreen=True)
            self.assertTrue(window._visible)  # noqa: SLF001

    def test_a_page_that_never_reports_its_size_is_shown_anyway(self):
        from unittest import mock

        window, overlay = self._window()
        with mock.patch.object(overlay, "is_topmost", return_value=True),              mock.patch.object(window, "_adopt"),              mock.patch.object(window, "_place") as place:
            window.update(True, "right")
            window._warming_since -= overlay.WARMUP_LIMIT + 0.1  # noqa: SLF001
            place.reset_mock()
            window.update(True, "right")
            place.assert_called_once_with(onscreen=True)

    def test_hiding_mid_warmup_leaves_nothing_behind(self):
        from unittest import mock

        window, overlay = self._window()
        with mock.patch.object(overlay, "is_topmost", return_value=True),              mock.patch.object(window, "_adopt"),              mock.patch.object(window, "_place"),              mock.patch.object(overlay, "_user32") as user32:
            window.update(True, "right")
            self.assertIsNotNone(window._warming_since)  # noqa: SLF001
            window.update(False, "right")
            user32.ShowWindow.assert_called_with(1234, overlay.SW_HIDE)
            self.assertIsNone(window._warming_since)  # noqa: SLF001


class TestAgentPool(unittest.TestCase):
    @staticmethod
    def _match(map_id, agent, won, kills=20, deaths=10):
        return {
            "mapId": map_id,
            "won": {"Blue": won, "Red": not won},
            "players": [
                {"s": "me", "t": "Blue", "c": agent, "k": kills, "d": deaths,
                 "r": 20, "sc": 4000},
                {"s": "other", "t": "Red", "c": "x", "k": 1, "d": 1, "r": 20, "sc": 1},
            ],
        }

    def test_uses_the_map_when_there_is_enough_of_it(self):
        from spikesight import agentpool

        matches = [self._match("ascent", "jett", True)] * 3 + [
            self._match("bind", "sage", False)] * 5
        pool = agentpool.aggregate(matches, "me", "ascent")
        self.assertEqual(pool["scope"], "map")
        self.assertEqual(pool["matches"], 3)
        self.assertEqual([a["id"] for a in pool["agents"]], ["jett"])
        self.assertEqual(pool["agents"][0]["winrate"], 1.0)
        self.assertEqual(pool["agents"][0]["kd"], 2.0)
        self.assertEqual(pool["agents"][0]["acs"], 200)

    def test_one_game_on_the_map_beats_a_pile_from_elsewhere(self):
        # Maps suit different agents, so the map you are about to play wins.
        from spikesight import agentpool

        matches = [self._match("ascent", "jett", True)] + [
            self._match("bind", "sage", False)] * 8
        pool = agentpool.aggregate(matches, "me", "ascent")
        self.assertEqual(pool["scope"], "map")
        self.assertEqual([a["id"] for a in pool["agents"]], ["jett"])
        self.assertEqual(pool["matches"], 1)

    def test_falls_back_to_all_maps_on_a_map_you_have_never_played(self):
        from spikesight import agentpool

        matches = [self._match("bind", "sage", False)] * 4
        pool = agentpool.aggregate(matches, "me", "abyss")
        self.assertEqual(pool["scope"], "all")
        self.assertEqual(pool["agents"][0]["id"], "sage")
        self.assertEqual(pool["mapMatches"], 0)

    def test_ignores_matches_you_were_not_in(self):
        from spikesight import agentpool

        pool = agentpool.aggregate([self._match("ascent", "jett", True), None],
                                   "someone-else", "ascent")
        self.assertEqual(pool["agents"], [])


class TestOverlayMoveEndpoints(unittest.TestCase):
    """Move mode is refused unless the overlay is on and actually running."""

    def _client(self, enabled):
        from fastapi.testclient import TestClient

        from spikesight.server import create_app

        cfg = Config(copy.deepcopy(DEFAULTS))
        cfg.set("overlay.enabled", enabled)
        return TestClient(create_app(cfg, demo=True))

    def test_refused_while_the_overlay_is_off(self):
        with self._client(False) as client:
            response = client.post("/api/overlay/edit", json={"on": True})
            self.assertEqual(response.status_code, 409)
            self.assertIn("Turn the overlay on", response.json()["detail"])

    def test_refused_without_an_overlay_window(self):
        with self._client(True) as client:
            self.assertEqual(
                client.post("/api/overlay/edit", json={"on": True}).status_code, 409
            )
            self.assertEqual(
                client.post("/api/overlay/drag", json={"phase": "start"}).status_code, 409
            )
            self.assertFalse(client.get("/api/overlay/edit").json()["on"])

    def test_picking_a_side_forgets_the_dragged_spot(self):
        from unittest import mock

        with self._client(True) as client, mock.patch(
            "spikesight.server.save_user_settings"
        ) as saved:
            client.app.state.cfg.set("overlay.x", 0.4)
            client.app.state.cfg.set("overlay.y", 0.4)
            self.assertTrue(client.get("/api/settings").json()["settings"]["overlayMoved"])
            result = client.post("/api/settings", json={"overlaySide": "right"}).json()
            self.assertFalse(result["settings"]["overlayMoved"])
            written = saved.call_args[0][0]
            self.assertIsNone(written["overlay.x"])
            self.assertEqual(written["overlay.side"], "right")


class TestOverlayDoesNotKeepTheAppAlive(unittest.IsolatedAsyncioTestCase):
    async def test_overlay_sockets_are_not_counted_as_the_window(self):
        from spikesight.server import Hub

        hub = Hub()
        window, panel = object(), object()
        await hub.add(panel, passive=True)
        self.assertEqual(hub.count, 0)
        await hub.add(window)
        self.assertEqual(hub.count, 1)
        await hub.discard(window)
        self.assertEqual(hub.count, 0)


class TestNotesStore(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.store = NotesStore(Path(self._dir.name) / "notes.sqlite3")

    async def asyncTearDown(self):
        self.store.close()
        self._dir.cleanup()

    async def test_note_round_trip(self):
        note = await self.store.add_note("p1", 3, ["thrower"], "threw round 9")
        found = await self.store.notes_for(["p1", "p2"])
        self.assertEqual(len(found["p1"]), 1)
        self.assertEqual(found["p1"][0]["severityKey"], "dodge")
        self.assertTrue(await self.store.delete_note(note["id"]))
        self.assertEqual(await self.store.notes_for(["p1"]), {})

    async def test_a_flag_on_one_hidden_player_stays_on_that_player(self):
        # Both were in Streamer Mode, so neither has a stored name. Only one
        # is flagged, and the two must still be told apart in the index.
        for puuid, match in (("hidden-a", "m1"), ("hidden-b", "m1")):
            await self.store.touch_player(puuid, None, None)
            await self.store.record_encounter(puuid, match, "enemy",
                                              agent="Brimstone")
        await self.store.add_note("hidden-a", 3, ["thrower"], "threw the match")

        rows = {r["puuid"]: r for r in await self.store.encounter_index()}
        self.assertEqual(rows["hidden-a"]["severity"], 3)
        self.assertEqual(rows["hidden-b"]["severity"], 0)

        codes = {rows["hidden-a"]["hiddenId"], rows["hidden-b"]["hiddenId"]}
        self.assertEqual(len(codes), 2)
        self.assertNotIn(None, codes)

    async def test_a_hidden_player_can_be_searched_by_their_code(self):
        await self.store.touch_player("hidden-a", None, None)
        await self.store.record_encounter("hidden-a", "m1", "enemy")
        code = privacy.hidden_tag("hidden-a")

        found = await self.store.encounter_index(query=code)
        self.assertEqual([r["puuid"] for r in found], ["hidden-a"])
        # Lowercase and a stray leading # both still find them.
        self.assertTrue(await self.store.encounter_index(query=code.lower()))
        self.assertTrue(await self.store.encounter_index(query="#" + code))

    async def test_a_named_player_gets_no_code(self):
        await self.store.touch_player("p1", "Name", "TAG")
        await self.store.record_encounter("p1", "m1", "ally")
        row = (await self.store.encounter_index())[0]
        self.assertIsNone(row["hiddenId"])
        self.assertIsNone((await self.store.player_profile("p1"))["hiddenId"])

    async def test_encounters_are_deduplicated_per_match(self):
        await self.store.touch_player("p1", "Name", "TAG")
        self.assertTrue(await self.store.record_encounter("p1", "m1", "enemy"))
        self.assertFalse(await self.store.record_encounter("p1", "m1", "enemy"))
        profile = await self.store.player_profile("p1")
        self.assertEqual(profile["encounterCount"], 1)

    async def test_alias_is_not_overwritten_with_nothing(self):
        await self.store.touch_player("p1", "Known", "TAG")
        await self.store.touch_player("p1", None, None)  # seen while incognito
        profile = await self.store.player_profile("p1")
        self.assertEqual(profile["lastName"], "Known")

    async def test_encounter_summary_counts_by_side(self):
        await self.store.touch_player("p1", "Name", "TAG")
        await self.store.record_encounter("p1", "m1", "enemy")
        await self.store.record_encounter("p1", "m2", "enemy")
        await self.store.record_encounter("p1", "m3", "ally")
        summary = (await self.store.encounter_summary(["p1", "p2"]))["p1"]
        self.assertEqual((summary["count"], summary["enemy"], summary["ally"]), (3, 2, 1))
        self.assertNotIn("p2", await self.store.encounter_summary(["p2"]))

    async def test_encounter_index_sorts_and_filters(self):
        await self.store.touch_player("often", "Often", "T")
        await self.store.touch_player("once", "Once", "T")
        for i in range(4):
            await self.store.record_encounter("often", f"m{i}", "enemy")
        await self.store.record_encounter("once", "m9", "ally")
        await self.store.add_note("once", 3, ["thrower"], "threw")

        by_count = await self.store.encounter_index(sort="count")
        self.assertEqual(by_count[0]["puuid"], "often")
        self.assertEqual(by_count[0]["count"], 4)

        flagged = await self.store.encounter_index(flagged_only=True)
        self.assertEqual([r["puuid"] for r in flagged], ["once"])
        self.assertEqual(flagged[0]["severity"], 3)

    async def test_notes_survive_reopening_the_database(self):
        await self.store.add_note("p1", 3, ["thrower"], "threw round 9")
        await self.store.record_encounter("p1", "m1", "enemy")
        self.store.close()
        # Same file, brand new process-equivalent connection.
        self.store = NotesStore(Path(self._dir.name) / "notes.sqlite3")
        found = await self.store.notes_for(["p1"])
        self.assertEqual(len(found["p1"]), 1)
        self.assertEqual((await self.store.encounter_summary(["p1"]))["p1"]["count"], 1)

    async def test_export_import(self):
        await self.store.add_note("p1", 2, ["toxic"], "flamed everyone")
        payload = await self.store.export()
        counts = await self.store.import_payload(payload)
        self.assertEqual(counts["notes"], 1)


class _StubRemote:
    """Stands in for RemoteClient with fixed responses."""

    def __init__(self, mmr_by_puuid: dict, names: dict) -> None:
        self._mmr = mmr_by_puuid
        self._names = names
        self.name_requests: list[list[str]] = []

    async def mmr(self, puuid):
        return self._mmr.get(puuid)

    async def names(self, puuids):
        self.name_requests.append(list(puuids))
        return {p: self._names[p] for p in puuids if p in self._names}

    async def party_of(self, puuid):
        return None


class _StubStats:
    cached_matches = 0

    async def collect(self, puuids, progress=None):
        return StatsResult(
            players={p: PlayerStats(puuid=p) for p in puuids},
            co_party={}, matches_examined=0, matches_fetched=0,
        )


class _StubNotes:
    def __init__(self):
        self.recorded = []

    async def notes_for(self, puuids):
        return {}

    async def encounter_summary(self, puuids):
        return {}

    async def touch_player(self, *args, **kwargs):
        return None

    async def record_encounter(self, puuid, match_id, relation, **kwargs):
        self.recorded.append((puuid, match_id, relation))
        return True


def _mmr_payload(act_id: str, tier: int, rr: int, *, act_hidden=False) -> dict:
    return {
        "QueueSkills": {
            "competitive": {
                "SeasonalInfoBySeasonID": {
                    act_id: {
                        "SeasonID": act_id,
                        "CompetitiveTier": tier,
                        "RankedRating": rr,
                        "NumberOfGames": 20,
                        "NumberOfWins": 12,
                        "LeaderboardRank": 0,
                        "WinsByTier": {str(tier): 12, str(tier + 1): 3},
                    }
                }
            }
        },
        "LatestCompetitiveUpdate": {},
        "IsActRankBadgeHidden": act_hidden,
        "IsLeaderboardAnonymized": False,
    }


class TestScoreboard(unittest.IsolatedAsyncioTestCase):
    ACT_ID = "aaaaaaaa1111bbbb2222cccc3333dddd"

    def _content(self) -> content.GameContent:
        game = content.GameContent()
        game.agents = {"agent1": "Jett", "agent2": "Omen"}
        game.maps_by_path = {"/Game/Maps/Ascent/Ascent": "Ascent"}
        game.acts[self.ACT_ID] = content.Act(
            id=self.ACT_ID, name="ACT V", episode_name="V26", is_active=True,
            start=datetime(2026, 8, 18, tzinfo=timezone.utc),
        )
        return game

    def _payload(self) -> dict:
        def player(puuid, team, agent, level, incognito=False, hide_level=False):
            return {
                "Subject": puuid,
                "TeamID": team,
                "CharacterID": agent,
                "PlayerIdentity": {
                    "Subject": puuid, "AccountLevel": level,
                    "Incognito": incognito, "HideAccountLevel": hide_level,
                },
                "SeasonalBadgeInfo": {"Rank": 0},
                "IsCoach": False,
            }

        return {
            "MatchID": "match-1",
            "MapID": "/Game/Maps/Ascent/Ascent",
            "ModeID": "/Game/GameModes/Bomb/BombGameMode.BombGameMode_C",
            "MatchmakingData": {"QueueID": "competitive", "IsRanked": True},
            "Players": [
                player("me", "Blue", "agent1", 266),
                player("ally", "Blue", "agent2", 40),
                player("shy", "Red", "agent1", 90, incognito=True),
                player("private", "Red", "agent2", 55, hide_level=True),
            ],
        }

    def _pregame_payload(self) -> dict:
        return {
            "ID": "pregame-1",
            "MapID": "/Game/Maps/Ascent/Ascent",
            "Mode": "/Game/GameModes/Bomb/BombGameMode.BombGameMode_C",
            "QueueID": "competitive",
            "IsRanked": True,
            "EnemyTeamSize": 5,
            "AllyTeam": {
                "TeamID": "Blue",
                "Players": [
                    {"Subject": "me", "CharacterID": "agent1",
                     "CharacterSelectionState": "locked",
                     "PlayerIdentity": {"AccountLevel": 266, "Incognito": False,
                                        "HideAccountLevel": False}},
                    {"Subject": "ally", "CharacterID": "agent2",
                     "CharacterSelectionState": "selected",
                     "PlayerIdentity": {"AccountLevel": 40, "Incognito": False,
                                        "HideAccountLevel": False}},
                ],
            },
            "EnemyTeam": None,
        }

    def _builder(self, remote) -> ScoreboardBuilder:
        return ScoreboardBuilder(remote, self._content(), _StubNotes(),
                                 _StubStats(), cfg())

    async def _build(self):
        remote = _StubRemote(
            {
                "me": _mmr_payload(self.ACT_ID, 17, 40),
                "ally": _mmr_payload(self.ACT_ID, 12, 90),
                "shy": _mmr_payload(self.ACT_ID, 20, 10),
                "private": _mmr_payload(self.ACT_ID, 15, 5, act_hidden=True),
            },
            {"me": {"game_name": "Me", "tag_line": "NA1"},
             "ally": {"game_name": "Ally", "tag_line": "NA1"},
             "shy": {"game_name": "SHOULD_NOT_APPEAR", "tag_line": "X"},
             "private": {"game_name": "Private", "tag_line": "NA1"}},
        )
        builder = self._builder(remote)
        snapshot = await builder.build(
            state="INGAME", match_payload=self._payload(), self_puuid="me", deep=False,
        )
        return remote, snapshot

    def _find(self, snapshot, puuid):
        for team in snapshot["teams"]:
            for player in team["players"]:
                if player["puuid"] == puuid:
                    return player
        raise AssertionError(f"{puuid} missing from snapshot")

    async def test_teams_are_split_and_sorted(self):
        _, snapshot = await self._build()
        ally = snapshot["teams"][0]
        enemy = snapshot["teams"][1]
        self.assertEqual({p["puuid"] for p in ally["players"]}, {"me", "ally"})
        self.assertEqual({p["puuid"] for p in enemy["players"]}, {"shy", "private"})
        points = [p["ratingPoints"] for p in enemy["players"]]
        self.assertEqual(points, sorted(points, reverse=True))

    async def test_incognito_name_is_never_requested(self):
        remote, snapshot = await self._build()
        for batch in remote.name_requests:
            self.assertNotIn("shy", batch)
        shy = self._find(snapshot, "shy")
        self.assertEqual(shy["name"]["display"], "Jett")
        self.assertTrue(shy["name"]["hidden"])
        self.assertNotIn("SHOULD_NOT_APPEAR", str(snapshot))

    async def test_hidden_level_is_stripped(self):
        _, snapshot = await self._build()
        player = self._find(snapshot, "private")
        self.assertIsNone(player["level"])
        self.assertTrue(player["levelHidden"])

    async def test_hidden_act_badge_suppresses_peak_and_record(self):
        _, snapshot = await self._build()
        player = self._find(snapshot, "private")
        self.assertIsNone(player["peak"])
        self.assertIsNone(player["previousAct"])
        self.assertTrue(player["act"]["hidden"])
        self.assertEqual(player["act"]["games"], 0)

    async def test_peak_uses_the_best_wins_by_tier(self):
        _, snapshot = await self._build()
        me = self._find(snapshot, "me")
        self.assertEqual(me["peak"]["rank"]["tier"], 18)
        self.assertEqual(me["peak"]["act"]["short"], "V26A5")

    async def test_map_and_mode_are_resolved(self):
        _, snapshot = await self._build()
        self.assertEqual(snapshot["match"]["map"], "Ascent")
        self.assertEqual(snapshot["match"]["modeName"], "Standard")
        self.assertTrue(snapshot["match"]["isRanked"])

    async def test_win_probability_is_present(self):
        _, snapshot = await self._build()
        self.assertIsNotNone(snapshot["winProbability"])
        self.assertIn("percent", snapshot["winProbability"])

    async def test_tracker_link_only_for_visible_names(self):
        _, snapshot = await self._build()
        self.assertIn("tracker.gg", self._find(snapshot, "ally")["trackerUrl"])
        # A Streamer Mode player must not get a link that reveals who they are.
        self.assertIsNone(self._find(snapshot, "shy")["trackerUrl"])

    async def test_tracker_link_escapes_the_hash(self):
        _, snapshot = await self._build()
        url = self._find(snapshot, "me")["trackerUrl"]
        self.assertIn("%23", url)
        self.assertNotIn("#", url)

    async def test_encounter_block_is_always_present(self):
        _, snapshot = await self._build()
        block = self._find(snapshot, "ally")["encounters"]
        self.assertEqual(block["count"], 0)
        self.assertIn("ally", block)

    async def test_building_a_lobby_records_nothing(self):
        # Agent select is dodgeable, so simply seeing a roster must not write
        # anything to the encounter log.
        notes = _StubNotes()
        remote = _StubRemote({"me": _mmr_payload(self.ACT_ID, 17, 40)}, {})
        builder = ScoreboardBuilder(remote, self._content(), notes, _StubStats(), cfg())
        await builder.build(state="PREGAME", match_payload=self._pregame_payload(),
                            self_puuid="me", deep=False)
        await builder.build(state="INGAME", match_payload=self._payload(),
                            self_puuid="me", deep=False)
        self.assertEqual(notes.recorded, [])

    async def test_encounters_are_recorded_once_when_the_match_ends(self):
        notes = _StubNotes()
        remote = _StubRemote({"me": _mmr_payload(self.ACT_ID, 17, 40)}, {})
        builder = ScoreboardBuilder(remote, self._content(), notes, _StubStats(), cfg())
        snapshot = await builder.build(state="INGAME", match_payload=self._payload(),
                                       self_puuid="me", deep=False)

        self.assertTrue(await builder.record_encounters(snapshot))
        # Everyone but you, exactly once.
        self.assertEqual(sorted(p for p, _, _ in notes.recorded),
                         ["ally", "private", "shy"])
        # A repeat call for the same match is a no-op.
        self.assertFalse(await builder.record_encounters(snapshot))
        self.assertEqual(len(notes.recorded), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
