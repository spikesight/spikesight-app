using System.Text.Json;
using SpikeSight.Core.Game;

namespace SpikeSight.Tests;

/// <summary>
/// Recent form reaching the board, and the things that depend on it.
/// </summary>
public class BoardStatsTests
{
    private static JsonElement Lobby()
    {
        var players = Enumerable.Range(0, 10).Select(i => new
        {
            Subject = $"p{i}",
            TeamID = i < 5 ? "Blue" : "Red",
            CharacterID = "c",
            PlayerIdentity = new { AccountLevel = 100, Incognito = false, HideAccountLevel = false },
        });
        using var document = JsonDocument.Parse(JsonSerializer.Serialize(new
        {
            MatchID = "m1",
            MapID = "/Game/Maps/Ascent/Ascent",
            ModeID = "/Game/GameModes/Bomb/BombGameMode.BombGameMode_C",
            MatchmakingData = new { QueueID = "competitive" },
            Players = players,
        }));
        return document.RootElement.Clone();
    }

    private static PlayerStats Form(string puuid, int kills, int deaths, int score, double? ps)
        => new(puuid)
        {
            Matches = 5, Kills = kills, Deaths = deaths, Rounds = 100, Score = score,
            Wins = 4, Losses = 1,
            PerformanceTotal = ps * 5 ?? 0, PerformanceMatches = ps is null ? 0 : 5,
        };

    /// <summary>Everyone at the same rank, so form is the only difference.</summary>
    private static Dictionary<string, JsonElement> Ranked(int tier = 15)
    {
        var mmr = new Dictionary<string, JsonElement>();
        for (var i = 0; i < 10; i++)
        {
            using var document = JsonDocument.Parse(JsonSerializer.Serialize(new
            {
                LatestCompetitiveUpdate = new { TierAfterUpdate = tier, RankedRatingAfterUpdate = 50 },
            }));
            mmr[$"p{i}"] = document.RootElement.Clone();
        }
        return mmr;
    }

    private static Board Build(
        IReadOnlyDictionary<string, PlayerStats>? stats,
        Dictionary<string, JsonElement>? mmr = null)
        => BoardBuilder.Build(Lobby(), "p0",
            mmr ?? new Dictionary<string, JsonElement>(),
            new Dictionary<string, (string, string)>(),
            currentActId: null, stats: stats);

    [Fact]
    public void Form_reaches_the_row_it_belongs_to()
    {
        var stats = new Dictionary<string, PlayerStats>
        {
            ["p0"] = Form("p0", 100, 50, 25000, 320),
        };

        var player = Build(stats).Players.Single(p => p.Puuid == "p0");

        Assert.Equal(2.0, player.Stats!.Kd);
        Assert.Equal(250, player.Stats.Acs);
        Assert.Equal(320, player.Stats.Performance);
        Assert.Equal(0.8, player.Stats.RecentWinrate);
    }

    [Fact]
    public void With_no_ranks_visible_it_says_so_rather_than_guessing()
    {
        var odds = Build(null).WinProbability!;

        Assert.Equal(50, odds.Percent);
        Assert.Equal("low", odds.Confidence);
        Assert.Contains("No ranks visible", odds.Note);
    }

    [Fact]
    public void A_player_with_no_form_simply_has_none()
    {
        var player = Build(null).Players.First();

        Assert.Null(player.Stats);
    }

    [Fact]
    public void Performance_score_averages_over_matches_not_rounds()
    {
        // ACS is score per round; Performance Score is already a whole-match
        // figure, so a 13-11 grind counts the same as a 13-3.
        var stats = new PlayerStats("p0")
        {
            Matches = 3, Rounds = 60, Score = 12000,
            PerformanceTotal = 353 + 245 + 148, PerformanceMatches = 3,
        };

        Assert.Equal(249, stats.Performance);
        Assert.Equal(200, stats.Acs);
    }

    [Fact]
    public void A_match_without_a_score_does_not_drag_the_average_down()
    {
        var stats = new PlayerStats("p0")
        {
            Matches = 5, PerformanceTotal = 300 + 400, PerformanceMatches = 2,
        };

        Assert.Equal(350, stats.Performance);
    }

    [Fact]
    public void Form_feeds_the_smurf_score()
    {
        // A high K/D is one of the signals, so it must actually arrive.
        var withForm = Build(new Dictionary<string, PlayerStats>
        {
            ["p0"] = Form("p0", 120, 50, 25000, null),
        }).Players.Single(p => p.Puuid == "p0");
        var without = Build(null).Players.Single(p => p.Puuid == "p0");

        Assert.Contains(withForm.Smurf!.Signals, s => s.Label.Contains("K/D"));
        Assert.DoesNotContain(without.Smurf!.Signals, s => s.Label.Contains("K/D"));
    }

    [Fact]
    public void Form_nudges_the_win_estimate()
    {
        var oneSided = new Dictionary<string, PlayerStats>();
        for (var i = 0; i < 5; i++) oneSided[$"p{i}"] = Form($"p{i}", 160, 100, 20000, null);
        for (var i = 5; i < 10; i++) oneSided[$"p{i}"] = Form($"p{i}", 70, 100, 20000, null);

        // Ranks are needed for the estimate to say anything at all: with none
        // visible it reports a coin flip and stops, form or no form.
        var level = Build(null, Ranked()).WinProbability!;
        var favoured = Build(oneSided, Ranked()).WinProbability!;

        Assert.Equal(50, level.Percent);
        Assert.True(favoured.Percent > level.Percent);
        Assert.Contains("Recent form favors your side", favoured.Note);
    }
}
