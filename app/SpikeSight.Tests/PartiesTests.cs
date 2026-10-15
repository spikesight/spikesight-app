using SpikeSight.Core.Game;

namespace SpikeSight.Tests;

public class PartiesTests
{
    private static Dictionary<string, List<string>> OneTeam(params string[] players)
        => new() { ["Blue"] = players.ToList() };

    private static Dictionary<(string, string), int> Pairs(params (string, string, int)[] pairs)
        => pairs.ToDictionary(p => (p.Item1, p.Item2), p => p.Item3);

    [Fact]
    public void A_shared_live_party_id_is_confirmed_not_guessed()
    {
        var assignments = Parties.Detect(
            OneTeam("a", "b", "c"),
            new Dictionary<string, string> { ["a"] = "party-1", ["b"] = "party-1" });

        Assert.Equal("confirmed", assignments["a"].Confidence);
        Assert.Equal(2, assignments["a"].Size);
        Assert.Equal(assignments["a"].Group, assignments["b"].Group);
        Assert.False(assignments.ContainsKey("c"));
    }

    [Fact]
    public void One_player_alone_with_a_party_id_is_not_a_party()
    {
        var assignments = Parties.Detect(
            OneTeam("a", "b"),
            new Dictionary<string, string> { ["a"] = "party-1" });

        Assert.Empty(assignments);
    }

    [Fact]
    public void A_pair_seen_together_often_enough_is_likely()
    {
        var assignments = Parties.Detect(
            OneTeam("a", "b", "c"),
            new Dictionary<string, string>(),
            Pairs(("a", "b", 3)));

        Assert.Equal("likely", assignments["a"].Confidence);
        Assert.Equal(2, assignments["a"].Size);
        Assert.Contains("3 recent matches", assignments["a"].Evidence);
    }

    [Fact]
    public void Once_is_not_enough()
    {
        // Two strangers get put on the same team often enough; it takes a
        // pattern before calling them a premade.
        var assignments = Parties.Detect(
            OneTeam("a", "b"), new Dictionary<string, string>(), Pairs(("a", "b", 1)));

        Assert.Empty(assignments);
    }

    [Fact]
    public void Pairs_are_never_chained_into_a_bigger_stack()
    {
        // A duos with B, B duos with C - that does not make a trio, and
        // inventing one would be worse than missing it.
        var assignments = Parties.Detect(
            OneTeam("a", "b", "c"),
            new Dictionary<string, string>(),
            Pairs(("a", "b", 3), ("b", "c", 3)));

        Assert.True(assignments.Values.All(p => p.Size == 2));
    }

    [Fact]
    public void A_genuine_trio_is_found_at_its_real_size()
    {
        // Everyone has partied with everyone, so this one is real.
        var assignments = Parties.Detect(
            OneTeam("a", "b", "c", "d"),
            new Dictionary<string, string>(),
            Pairs(("a", "b", 3), ("b", "c", 3), ("a", "c", 3)));

        Assert.Equal(3, assignments["a"].Size);
        Assert.Equal(assignments["a"].Group, assignments["c"].Group);
        Assert.False(assignments.ContainsKey("d"));
    }

    [Fact]
    public void A_group_actually_seen_together_says_so()
    {
        var assignments = Parties.Detect(
            OneTeam("a", "b", "c"),
            new Dictionary<string, string>(),
            Pairs(("a", "b", 3), ("b", "c", 3), ("a", "c", 3)),
            new Dictionary<string, int> { ["a|b|c"] = 4 });

        Assert.Contains("Seen together as a 3-stack in 4 recent matches",
                        assignments["a"].Evidence);
    }

    [Fact]
    public void A_confirmed_party_is_not_second_guessed_by_inference()
    {
        var assignments = Parties.Detect(
            OneTeam("a", "b", "c"),
            new Dictionary<string, string> { ["a"] = "p1", ["b"] = "p1" },
            Pairs(("a", "c", 5), ("b", "c", 5)));

        Assert.Equal("confirmed", assignments["a"].Confidence);
        Assert.Equal(2, assignments["a"].Size);
    }

    [Fact]
    public void Parties_never_span_teams()
    {
        var assignments = Parties.Detect(
            new Dictionary<string, List<string>>
            {
                ["Blue"] = ["a", "b"],
                ["Red"] = ["c", "d"],
            },
            new Dictionary<string, string>(),
            Pairs(("a", "c", 5)));   // impossible, but must not be believed

        Assert.Empty(assignments);
    }

    [Fact]
    public void Groups_get_distinct_colour_indices()
    {
        var assignments = Parties.Detect(
            OneTeam("a", "b", "c", "d"),
            new Dictionary<string, string>
            {
                ["a"] = "p1", ["b"] = "p1", ["c"] = "p2", ["d"] = "p2",
            });

        Assert.NotEqual(assignments["a"].Group, assignments["c"].Group);
        Assert.Equal(assignments["a"].Group, assignments["b"].Group);
    }

    [Fact]
    public void A_full_deathmatch_lobby_does_not_hang_the_search()
    {
        // Eleven players all pairwise partied is the combinatorial worst case.
        var players = Enumerable.Range(0, 11).Select(i => $"p{i}").ToList();
        var pairs = new Dictionary<(string, string), int>();
        foreach (var a in players)
            foreach (var b in players.Where(b => string.CompareOrdinal(a, b) < 0))
                pairs[(a, b)] = 5;

        var clock = System.Diagnostics.Stopwatch.StartNew();
        var assignments = Parties.Detect(
            new Dictionary<string, List<string>> { ["Blue"] = players },
            new Dictionary<string, string>(), pairs);

        Assert.True(clock.ElapsedMilliseconds < 2000, $"took {clock.ElapsedMilliseconds}ms");
        Assert.Equal(10, assignments["p0"].Size);
    }
}
