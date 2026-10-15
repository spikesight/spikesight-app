using System.Text.Json;
using SpikeSight.Core.Game;
using SpikeSight.Core.Store;

namespace SpikeSight.Tests;

/// <summary>
/// Notes reaching the board. The whole point of the app is that a flag you
/// left comes back next time, while the dodge window is still open.
/// </summary>
public class BoardNotesTests
{
    private static JsonElement Lobby()
    {
        using var document = JsonDocument.Parse(JsonSerializer.Serialize(new
        {
            MatchID = "m1",
            MapID = "/Game/Maps/Ascent/Ascent",
            MatchmakingData = new { QueueID = "competitive" },
            Players = Enumerable.Range(0, 4).Select(i => new
            {
                Subject = $"p{i}",
                TeamID = i < 2 ? "Blue" : "Red",
                CharacterID = "c",
                PlayerIdentity = new { AccountLevel = 100 },
            }),
        }));
        return document.RootElement.Clone();
    }

    private static PlayerNote Note(string puuid, Severity severity)
        => new(1, puuid, severity, "", [], DateTimeOffset.UtcNow, DateTimeOffset.UtcNow);

    private static Board Build(
        Dictionary<string, List<PlayerNote>>? notes = null,
        Dictionary<string, EncounterSummary>? encounters = null)
        => BoardBuilder.Build(Lobby(), "p0",
            new Dictionary<string, JsonElement>(),
            new Dictionary<string, (string, string)>(),
            currentActId: null, notes: notes, encounters: encounters);

    [Fact]
    public void A_flag_comes_back_on_the_player_it_was_left_on()
    {
        var board = Build(new Dictionary<string, List<PlayerNote>>
        {
            ["p2"] = [Note("p2", Severity.Dodge)],
        });

        Assert.Equal(Severity.Dodge, board.Players.Single(p => p.Puuid == "p2").WorstNote);
        Assert.Null(board.Players.Single(p => p.Puuid == "p1").WorstNote);
    }

    [Fact]
    public void The_worst_flag_is_the_one_shown()
    {
        var board = Build(new Dictionary<string, List<PlayerNote>>
        {
            ["p1"] = [Note("p1", Severity.Watch), Note("p1", Severity.Dodge), Note("p1", Severity.Avoid)],
        });

        Assert.Equal(Severity.Dodge, board.Players.Single(p => p.Puuid == "p1").WorstNote);
    }

    [Fact]
    public void Encounter_counts_reach_the_row()
    {
        var board = Build(encounters: new Dictionary<string, EncounterSummary>
        {
            ["p3"] = new(3, 1, 2, DateTimeOffset.UtcNow),
        });

        var player = board.Players.Single(p => p.Puuid == "p3");
        Assert.Equal(3, player.Encounters!.Count);
        Assert.Equal(2, player.Encounters.Enemy);
    }

    [Fact]
    public void A_lobby_with_nothing_recorded_simply_has_none()
    {
        var board = Build();

        Assert.All(board.Players, p => Assert.Null(p.WorstNote));
        Assert.All(board.Players, p => Assert.Null(p.Encounters));
    }
}

/// <summary>Flagging end to end, through the real store.</summary>
public class FlaggingTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "ssflag-" + Guid.NewGuid().ToString("N"));
    private readonly NotesStore _store;

    public FlaggingTests() => _store = new NotesStore(Path.Combine(_dir, "notes.sqlite3"));

    public void Dispose()
    {
        _store.Dispose();
        try { Directory.Delete(_dir, recursive: true); } catch (IOException) { }
    }

    [Fact]
    public async Task A_flag_survives_into_the_next_lobby()
    {
        await _store.AddNoteAsync("p2", Severity.Avoid, "griefed mid");

        // A different lobby entirely, same player.
        var notes = await _store.NotesForAsync(["p2", "someone-else"]);

        Assert.Equal(Severity.Avoid, notes["p2"].Single().Severity);
    }

    [Fact]
    public async Task Clearing_removes_every_flag_on_that_player()
    {
        await _store.AddNoteAsync("p2", Severity.Watch);
        await _store.AddNoteAsync("p2", Severity.Dodge);

        foreach (var note in (await _store.NotesForAsync(["p2"]))["p2"])
            await _store.DeleteNoteAsync(note.Id);

        Assert.Empty(await _store.NotesForAsync(["p2"]));
    }

    [Fact]
    public async Task Flagging_a_hidden_player_needs_no_name()
    {
        // Streamer Mode gives us a puuid and nothing else, and that is enough:
        // the note is keyed on it and comes back without ever recording who
        // they are.
        await _store.AddNoteAsync("incognito-puuid", Severity.Dodge);

        var notes = await _store.NotesForAsync(["incognito-puuid"]);

        Assert.Single(notes["incognito-puuid"]);
        Assert.Equal("", notes["incognito-puuid"][0].Text);
    }
}
