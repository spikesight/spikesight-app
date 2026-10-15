using SpikeSight.Core.Store;

namespace SpikeSight.Tests;

public class NotesStoreTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "spikesight-" + Guid.NewGuid().ToString("N"));
    private readonly NotesStore _store;

    public NotesStoreTests() => _store = new NotesStore(Path.Combine(_dir, "notes.sqlite3"));

    public void Dispose()
    {
        _store.Dispose();
        try { Directory.Delete(_dir, recursive: true); } catch (IOException) { }
    }

    [Fact]
    public async Task A_note_comes_back_for_the_player_it_was_left_on()
    {
        await _store.AddNoteAsync("p1", Severity.Dodge, "threw the game", ["thrower", "toxic"]);

        var notes = await _store.NotesForAsync(["p1", "p2"]);

        var note = Assert.Single(notes["p1"]);
        Assert.Equal(Severity.Dodge, note.Severity);
        Assert.Equal("threw the game", note.Text);
        Assert.Equal(["thrower", "toxic"], note.Tags);
        Assert.False(notes.ContainsKey("p2"));
    }

    [Fact]
    public async Task A_note_needs_nothing_but_a_puuid()
    {
        // This is what lets a note follow a player in Streamer Mode: it still
        // resurfaces without ever recording who they are.
        var id = await _store.AddNoteAsync("hidden-player", Severity.Avoid);

        var notes = await _store.NotesForAsync(["hidden-player"]);

        Assert.True(id > 0);
        Assert.Equal("", Assert.Single(notes["hidden-player"]).Text);
    }

    [Fact]
    public async Task The_worst_flag_is_listed_first()
    {
        await _store.AddNoteAsync("p1", Severity.Watch, "ok-ish");
        await _store.AddNoteAsync("p1", Severity.Dodge, "never again");

        var notes = await _store.NotesForAsync(["p1"]);

        Assert.Equal(Severity.Dodge, notes["p1"][0].Severity);
    }

    [Fact]
    public async Task A_deleted_note_is_gone()
    {
        var id = await _store.AddNoteAsync("p1", Severity.Watch);

        Assert.True(await _store.DeleteNoteAsync(id));
        Assert.False(await _store.DeleteNoteAsync(id));
        Assert.Empty(await _store.NotesForAsync(["p1"]));
    }

    [Fact]
    public async Task Encounters_count_which_side_they_were_on()
    {
        await _store.RecordEncounterAsync("p1", "m1", "enemy", "Jett", "Ascent", "competitive");
        await _store.RecordEncounterAsync("p1", "m2", "ally", "Omen", "Bind", "competitive");
        await _store.RecordEncounterAsync("p1", "m3", "enemy", "Sage", "Split", "unrated");

        var summary = (await _store.EncounterSummaryAsync(["p1"]))["p1"];

        Assert.Equal(3, summary.Count);
        Assert.Equal(1, summary.Ally);
        Assert.Equal(2, summary.Enemy);
        Assert.NotNull(summary.Last);
    }

    [Fact]
    public async Task The_same_match_is_only_counted_once()
    {
        // The loop can publish a roster more than once for one match; meeting
        // somebody twice in a single game is still meeting them once.
        await _store.RecordEncounterAsync("p1", "m1", "enemy");
        await _store.RecordEncounterAsync("p1", "m1", "enemy");
        await _store.RecordEncounterAsync("p1", "m1", "ally");

        Assert.Equal(1, (await _store.EncounterSummaryAsync(["p1"]))["p1"].Count);
    }

    [Fact]
    public async Task A_whole_lobby_is_read_in_one_go()
    {
        var lobby = Enumerable.Range(0, 10).Select(i => $"p{i}").ToList();
        foreach (var puuid in lobby.Take(3))
            await _store.AddNoteAsync(puuid, Severity.Watch);

        var notes = await _store.NotesForAsync(lobby);

        Assert.Equal(3, notes.Count);
    }

    [Fact]
    public async Task Nothing_asked_for_is_nothing_returned()
    {
        Assert.Empty(await _store.NotesForAsync([]));
        Assert.Empty(await _store.EncounterSummaryAsync([]));
        // An empty puuid is dropped rather than queried for.
        Assert.Empty(await _store.NotesForAsync([""]));
    }

    [Fact]
    public void A_fresh_database_passes_its_own_integrity_check()
        => Assert.True(_store.IsHealthy());

    [Fact]
    public async Task Concurrent_writers_do_not_trip_over_each_other()
    {
        // The Python build lost this database twice to concurrent access.
        var writes = Enumerable.Range(0, 50).Select(i =>
            i % 2 == 0
                ? _store.AddNoteAsync($"p{i}", Severity.Watch, $"note {i}")
                : _store.RecordEncounterAsync($"p{i}", $"m{i}", "ally").ContinueWith(_ => 0L));

        await Task.WhenAll(writes);

        Assert.True(_store.IsHealthy());
        Assert.Equal(25, (await _store.NotesForAsync(
            Enumerable.Range(0, 50).Select(i => $"p{i}"))).Count);
    }
}
