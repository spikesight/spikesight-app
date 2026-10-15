using System.Text.Json;
using SpikeSight.Core.Game;
using SpikeSight.Core.Store;

namespace SpikeSight.Tests;

public class MatchSummaryTests
{
    private static JsonElement Json(string text)
    {
        using var document = JsonDocument.Parse(text);
        return document.RootElement.Clone();
    }

    /// <summary>Built rather than hand-written: nested JSON braces and raw
    /// string interpolation do not mix.</summary>
    private static object Player(
        string puuid, string team = "Blue", int kills = 1, int deaths = 1,
        int rounds = 20, int score = 4000, object? scores = null, bool observer = false)
        => new
        {
            subject = puuid,
            teamId = team,
            partyId = "",
            characterId = "c",
            competitiveTier = 18,
            isObserver = observer,
            stats = new { kills, deaths, assists = 2, roundsPlayed = rounds, score },
            scores = scores ?? new { },
        };

    private static JsonElement Match(object matchInfo, params object[] players)
        => Json(JsonSerializer.Serialize(new { matchInfo, players }));

    [Fact]
    public void Keeps_no_names()
    {
        var payload = Json(JsonSerializer.Serialize(new
        {
            matchInfo = new
            {
                matchId = "m1", queueID = "competitive", mapId = "/Game/Maps/Ascent/Ascent",
                gameStartMillis = 1L, isCompleted = true,
            },
            teams = new[] { new { teamId = "Blue", won = true, roundsWon = 13 } },
            players = new[]
            {
                new
                {
                    subject = "p1", gameName = "Leaky", tagLine = "NA1", teamId = "Blue",
                    partyId = "party-1", characterId = "c", competitiveTier = 18,
                    stats = new { kills = 20, deaths = 10, assists = 4, roundsPlayed = 20, score = 5000 },
                },
            },
        }));

        var summary = MatchSummary.From(payload)!;

        Assert.Equal("m1", summary.MatchId);
        Assert.Equal("competitive", summary.Queue);
        Assert.True(summary.WonByTeam["Blue"]);
        Assert.DoesNotContain("Leaky", JsonSerializer.Serialize(summary));
        Assert.Equal("party-1", summary.Players[0].PartyId);
    }

    [Fact]
    public void Reads_performance_score_from_riots_placeholder()
    {
        // Identified by matching the match and both team MVPs, and confirmed
        // against the number the end-of-game screen showed.
        var summary = MatchSummary.From(Match(
            new { matchId = "m1" },
            Player("p1", scores: new { TempValueF = 352.7 })))!;

        Assert.Equal(352.7, summary.Players[0].Performance!.Value, 1);
    }

    [Fact]
    public void A_real_name_would_win_over_the_placeholder()
    {
        var summary = MatchSummary.From(Match(
            new { matchId = "m1" },
            Player("p1", scores: new { performanceScore = 311.0, TempValueF = 352.7 })))!;

        Assert.Equal(311, summary.Players[0].Performance!.Value, 1);
    }

    [Theory]
    [InlineData(8123.0)]
    [InlineData(-4.0)]
    public void A_value_outside_zero_to_five_hundred_is_not_a_score(double value)
    {
        // A placeholder field can be repurposed without warning. Better to
        // show nothing than whatever landed in it instead.
        var summary = MatchSummary.From(Match(
            new { matchId = "m1" },
            Player("p1", scores: new { TempValueF = value })))!;

        Assert.Null(summary.Players[0].Performance);
    }

    [Fact]
    public void Observers_are_not_players()
    {
        var summary = MatchSummary.From(Match(
            new { matchId = "m1" },
            Player("p1", observer: true),
            Player("p2")))!;

        Assert.Equal("p2", Assert.Single(summary.Players).Puuid);
    }

    [Fact]
    public void A_payload_with_no_match_id_is_not_a_match()
    {
        Assert.Null(MatchSummary.From(Json("""{"matchInfo":{}}""")));
        Assert.Null(MatchSummary.From(Json("""{"nothing":true}""")));
    }

    [Fact]
    public void Scores_that_stop_looking_like_combat_score_are_noticed()
    {
        // A 0-500 per-match number over 20 rounds reads as an ACS of about 17.
        var summary = MatchSummary.From(Match(
            new { matchId = "m1", isCompleted = true },
            Enumerable.Range(0, 10)
                .Select(i => Player($"p{i}", kills: 15, deaths: 15, rounds: 20, score: 17 * 20))
                .ToArray()))!;

        Assert.True(summary.ScoringLooksWrong());
    }

    [Fact]
    public void An_ordinary_match_does_not_trip_the_alarm()
    {
        var summary = MatchSummary.From(Match(
            new { matchId = "m1", isCompleted = true },
            Enumerable.Range(0, 10)
                .Select(i => Player($"p{i}", kills: 15, deaths: 15, rounds: 20, score: 220 * 20))
                .ToArray()))!;

        Assert.False(summary.ScoringLooksWrong());
    }

    [Fact]
    public void A_half_empty_match_proves_nothing_either_way()
    {
        var summary = MatchSummary.From(Match(
            new { matchId = "m1", isCompleted = true },
            Enumerable.Range(0, 3)
                .Select(i => Player($"p{i}", rounds: 20, score: 17 * 20))
                .ToArray()))!;

        Assert.False(summary.ScoringLooksWrong());
    }
}

public class MatchCacheTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "sscache-" + Guid.NewGuid().ToString("N"));

    public void Dispose()
    {
        try { Directory.Delete(_dir, recursive: true); } catch (IOException) { }
    }

    private static MatchSummary Summary(string id) => new()
    {
        MatchId = id, Queue = "competitive", Completed = true,
        Players = [new MatchRow { Puuid = "p1", Rounds = 20, Score = 4000, Performance = 300 }],
    };

    [Fact]
    public void A_stored_match_comes_back_whole()
    {
        new MatchCache(_dir).Put(Summary("m1"));

        var fresh = new MatchCache(_dir).Peek("m1");

        Assert.NotNull(fresh);
        Assert.Equal("competitive", fresh!.Queue);
        Assert.Equal(300, fresh.Players[0].Performance);
    }

    [Fact]
    public void A_match_written_by_an_older_build_is_fetched_again()
    {
        new MatchCache(_dir).Put(Summary("m1"));
        var path = Path.Combine(_dir, "m1.json");
        File.WriteAllText(path, File.ReadAllText(path).Replace("\"v\":1", "\"v\":0"));

        Assert.Null(new MatchCache(_dir).Peek("m1"));
    }

    [Fact]
    public void An_unreadable_file_is_a_miss_rather_than_a_crash()
    {
        Directory.CreateDirectory(_dir);
        File.WriteAllText(Path.Combine(_dir, "m2.json"), "{ this is not json");

        Assert.Null(new MatchCache(_dir).Peek("m2"));
    }

    [Fact]
    public void A_match_id_cannot_escape_the_cache_directory()
    {
        var cache = new MatchCache(_dir);

        Assert.Null(cache.Peek("../../etc/passwd"));
        Assert.Null(cache.Peek(@"..\..\windows\system32"));
    }
}
