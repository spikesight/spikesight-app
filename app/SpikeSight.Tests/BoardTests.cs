using System.Text.Json;
using SpikeSight.Core.Game;

namespace SpikeSight.Tests;

/// <summary>
/// Built from a real Deathmatch, captured live and scrubbed. Eleven players,
/// two of them incognito, seven hiding their account level - the privacy rules
/// get tested against something that actually happened rather than something
/// invented to pass.
/// </summary>
public class BoardFromRealDeathmatch
{
    private static readonly JsonElement Fixture = Load();
    private static JsonElement Match => Fixture.GetProperty("match");
    private static string Self => Fixture.GetProperty("self").GetString()!;

    private static JsonElement Load()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "Fixtures", "deathmatch-ingame.json");
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        return document.RootElement.Clone();
    }

    private static Dictionary<string, JsonElement> Mmr()
        => Fixture.GetProperty("mmr").EnumerateObject()
            .ToDictionary(p => p.Name, p => p.Value.Clone());

    /// <summary>Names for everyone we are allowed to ask about, as the real
    /// lookup would return them - incognito players are simply absent.</summary>
    private static Dictionary<string, (string, string)> NamesForResolvable()
    {
        var names = new Dictionary<string, (string, string)>();
        foreach (var player in Match.GetProperty("Players").EnumerateArray())
        {
            var puuid = player.GetProperty("Subject").GetString()!;
            var identity = player.GetProperty("PlayerIdentity");
            var incognito = identity.TryGetProperty("Incognito", out var i)
                            && i.ValueKind == JsonValueKind.True;
            if (!incognito) names[puuid] = ($"Player{puuid[1..]}", "NA1");
        }
        return names;
    }

    private static Board Build() => BoardBuilder.Build(
        Match, Self, Mmr(), NamesForResolvable(), currentActId: null);

    [Fact]
    public void Reads_the_whole_lobby()
    {
        var board = Build();

        Assert.Equal(11, board.Players.Count());
        Assert.Equal("Deathmatch", board.Label);
        Assert.False(board.IsRanked);
    }

    [Fact]
    public void Resolves_the_map_code_name()
    {
        // The payload says /Game/Maps/Duality/Duality; Duality is Bind.
        Assert.Equal("Bind", Build().MapName);
    }

    [Fact]
    public void A_free_for_all_lobby_is_not_called_your_team()
    {
        // Every player in Deathmatch carries TeamID "Blue", so the whole lobby
        // lands on our side with nobody opposite. They are not team mates.
        var board = Build();
        var ally = board.Sides[0];
        var enemy = board.Sides[1];

        Assert.Equal(11, ally.Players.Count);
        Assert.Empty(enemy.Players);
        Assert.Equal("Everyone in this match", ally.Label);
    }

    [Fact]
    public void The_two_incognito_players_are_never_named()
    {
        var hidden = Build().Players.Where(p => p.Flags.Incognito).ToList();

        Assert.Equal(2, hidden.Count);
        foreach (var player in hidden)
        {
            Assert.True(player.Name.Hidden);
            Assert.Null(player.Name.GameName);
            Assert.Null(player.Name.TagLine);
            Assert.DoesNotContain("Player", player.Name.Display, StringComparison.Ordinal);
            // Shown by agent, with a short code so two of them stay apart.
            Assert.NotNull(player.Name.HiddenId);
            Assert.Equal(4, player.Name.HiddenId!.Length);
        }

        // And the two codes differ, which is the entire point of having them.
        Assert.Equal(2, hidden.Select(p => p.Name.HiddenId).Distinct().Count());
    }

    [Fact]
    public void The_seven_who_hid_their_level_have_none_shown()
    {
        var board = Build();

        Assert.Equal(7, board.Players.Count(p => p.LevelHidden));
        Assert.All(board.Players.Where(p => p.LevelHidden), p => Assert.Null(p.Level));
        // And the four who did not are shown, so this is suppression and not
        // the field simply being absent.
        Assert.Equal(4, board.Players.Count(p => p.Level is > 0));
    }

    [Fact]
    public void A_hidden_act_rank_suppresses_the_peak()
    {
        var board = Build();

        Assert.All(board.Players.Where(p => p.Flags.ActRankHidden), p => Assert.Null(p.Peak));
    }

    [Fact]
    public void Ranked_players_sort_above_unranked()
    {
        var points = Build().Sides[0].Players.Select(p => p.RatingPoints ?? -1).ToList();

        Assert.Equal(points.OrderByDescending(p => p).ToList(), points);
    }

    [Fact]
    public void Eleven_players_is_a_lobby_worth_reading()
        => Assert.True(BoardBuilder.IsLobby(Match));
}

public class LobbyDetectionTests
{
    private static JsonElement Players(int count)
    {
        var subjects = string.Join(",", Enumerable.Range(0, count).Select(i => $$"""{"Subject":"p{{i}}"}"""));
        using var document = JsonDocument.Parse($$"""{"Players":[{{subjects}}]}""");
        return document.RootElement.Clone();
    }

    [Fact]
    public void Sitting_in_the_range_alone_is_not_a_lobby()
        => Assert.False(BoardBuilder.IsLobby(Players(1)));

    [Fact]
    public void Neither_is_a_session_with_nobody_in_it()
        => Assert.False(BoardBuilder.IsLobby(Players(0)));

    [Fact]
    public void Two_players_is()
        => Assert.True(BoardBuilder.IsLobby(Players(2)));
}
