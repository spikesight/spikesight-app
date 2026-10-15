using SpikeSight.Core.Game;

namespace SpikeSight.Tests;

public class SmurfTests
{
    private static SmurfVerdict Evaluate(
        int? level = null, bool levelHidden = false, int? tier = null, int? peak = null,
        int? actGames = null, int? actWins = null, double? kd = null,
        int recentMatches = 0, double? recentWinrate = null)
        => Smurf.Evaluate(Settings.Default, level, levelHidden, tier, peak,
                          actGames, actWins, kd, recentMatches, recentWinrate);

    [Fact]
    public void A_middling_score_says_nothing()
    {
        // Silver on level 50 with a 1.50 K/D scores 30 - which used to put a
        // "smurf 30/100" chip on the board. Not worth acting on.
        var verdict = Evaluate(level: 50, tier: 10, kd: 1.50, recentMatches: 5);

        Assert.Equal(30, verdict.Score);
        Assert.False(verdict.Flagged);
    }

    [Fact]
    public void A_strong_player_on_a_young_account_flags()
    {
        var verdict = Evaluate(level: 30, tier: 21, kd: 1.50, recentMatches: 5);

        Assert.True(verdict.Score >= 50);
        Assert.True(verdict.Flagged);
    }

    [Fact]
    public void A_high_level_account_is_just_good_at_the_game()
    {
        // No single signal is enough: a Radiant on a level-900 account is
        // simply Radiant.
        var verdict = Evaluate(level: 900, tier: 27, peak: 27);

        Assert.Equal(0, verdict.Score);
        Assert.Empty(verdict.Signals);
    }

    [Fact]
    public void A_low_level_silver_is_just_new()
    {
        var verdict = Evaluate(level: 25, tier: 10);

        Assert.False(verdict.Flagged);
        Assert.Single(verdict.Signals);  // young account, nothing more
    }

    [Fact]
    public void A_hidden_level_is_left_out_rather_than_guessed_at()
    {
        // Reconstructing it from match history would defeat the setting, so
        // the signal simply does not fire.
        var hidden = Evaluate(level: 20, levelHidden: true, tier: 24, kd: 1.9, recentMatches: 6);
        var shown = Evaluate(level: 20, levelHidden: false, tier: 24, kd: 1.9, recentMatches: 6);

        Assert.True(hidden.LevelHidden);
        Assert.DoesNotContain(hidden.Signals, s => s.Label.Contains("level"));
        Assert.True(shown.Score > hidden.Score);
    }

    [Fact]
    public void Signals_come_back_heaviest_first_so_the_reason_reads_well()
    {
        var verdict = Evaluate(level: 20, tier: 24, kd: 2.0, recentMatches: 8,
                               actGames: 10, actWins: 9);

        Assert.True(verdict.Signals.Count > 1);
        var weights = verdict.Signals.Select(s => s.Weight).ToList();
        Assert.Equal(weights.OrderByDescending(w => w).ToList(), weights);
    }

    [Fact]
    public void The_score_never_runs_past_a_hundred()
    {
        var verdict = Evaluate(level: 5, tier: 27, peak: 27, kd: 3.0, recentMatches: 10,
                               actGames: 10, actWins: 10);

        Assert.Equal(100, verdict.Score);
    }

    [Fact]
    public void Turning_it_off_scores_nothing_at_all()
    {
        var verdict = Smurf.Evaluate(
            Settings.Default with { SmurfDetectionEnabled = false },
            accountLevel: 5, levelHidden: false, currentTier: 27, peakTier: 27);

        Assert.Equal(0, verdict.Score);
        Assert.False(verdict.Flagged);
    }
}

public class WinProbabilityTests
{
    private static List<WinProbabilityEstimate.Entry> Side(params double?[] points)
        => points.Select(p => new WinProbabilityEstimate.Entry(p, null)).ToList();

    [Fact]
    public void An_even_lobby_is_a_coin_flip()
    {
        var odds = WinProbabilityEstimate.Estimate(
            Settings.Default, Side(1000, 1000), Side(1000, 1000));

        Assert.Equal(50, odds!.Percent);
        Assert.Equal("high", odds.Confidence);
    }

    [Fact]
    public void A_stronger_side_is_favoured()
    {
        var odds = WinProbabilityEstimate.Estimate(
            Settings.Default, Side(1400, 1400), Side(1000, 1000));

        Assert.True(odds!.Percent > 70);
    }

    [Fact]
    public void Unranked_players_are_imputed_not_treated_as_iron()
    {
        // Treating an unranked player as Iron 1 would wreck the estimate; the
        // average of the known ranks is a far better guess.
        var imputed = WinProbabilityEstimate.Estimate(
            Settings.Default, Side(1200, null), Side(1200, 1200));

        Assert.NotNull(imputed);
        Assert.InRange(imputed!.Percent, 45, 55);
        Assert.Equal(3, imputed.KnownRanks);
        Assert.Equal(4, imputed.TotalPlayers);
    }

    [Fact]
    public void Nobody_ranked_at_all_is_honest_about_it()
    {
        var odds = WinProbabilityEstimate.Estimate(
            Settings.Default, Side(null, null), Side(null, null));

        Assert.Equal(50, odds!.Percent);
        Assert.Equal("low", odds.Confidence);
        Assert.Contains("No ranks visible", odds.Note);
    }

    [Fact]
    public void An_empty_side_has_no_odds_to_give()
    {
        Assert.Null(WinProbabilityEstimate.Estimate(Settings.Default, Side(1000), []));
        Assert.Null(WinProbabilityEstimate.Estimate(Settings.Default, [], Side(1000)));
    }

    [Fact]
    public void The_result_never_reaches_certainty()
    {
        // A heuristic should not claim to know the outcome.
        var odds = WinProbabilityEstimate.Estimate(
            Settings.Default, Side(2700, 2700), Side(0, 0));

        Assert.InRange(odds!.Probability, 0.02, 0.98);
    }

    [Fact]
    public void Recent_form_nudges_it_but_does_not_decide_it()
    {
        var level = new List<WinProbabilityEstimate.Entry>
            { new(1000, 1.6), new(1000, 1.6) };
        var other = new List<WinProbabilityEstimate.Entry>
            { new(1000, 0.7), new(1000, 0.7) };

        var odds = WinProbabilityEstimate.Estimate(Settings.Default, level, other)!;

        Assert.InRange(odds.Percent, 55, 70);
        Assert.Contains("Recent form favors your side", odds.Note);
    }
}
