using Microsoft.Data.Sqlite;
using SpikeSight.Core.Store;

namespace SpikeSight.Tests;

/// <summary>
/// Bringing 2.x's notes across. Caught the hard way: v3 was opening 2.x's
/// live database, whose schema uses body, created_at and seen_at where v3
/// uses text, created and seen. CREATE TABLE IF NOT EXISTS quietly did
/// nothing and every query afterwards threw.
/// </summary>
public class LegacyImportTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "sslegacy-" + Guid.NewGuid().ToString("N"));
    private readonly string _legacy;

    public LegacyImportTests()
    {
        Directory.CreateDirectory(_dir);
        _legacy = Path.Combine(_dir, "notes.sqlite3");
        BuildLegacyDatabase();
    }

    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
        try { Directory.Delete(_dir, recursive: true); } catch (IOException) { }
    }

    /// <summary>2.x's schema, exactly as it is on disk.</summary>
    private void BuildLegacyDatabase()
    {
        using var db = new SqliteConnection($"Data Source={_legacy}");
        db.Open();
        foreach (var sql in new[]
        {
            """
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, puuid TEXT NOT NULL,
                severity INTEGER NOT NULL DEFAULT 2, tags TEXT NOT NULL DEFAULT '[]',
                body TEXT NOT NULL DEFAULT '', match_id TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)
            """,
            """
            CREATE TABLE encounters (
                id INTEGER PRIMARY KEY AUTOINCREMENT, puuid TEXT NOT NULL,
                match_id TEXT, seen_at TEXT NOT NULL, relation TEXT NOT NULL,
                agent TEXT, map_name TEXT, queue TEXT)
            """,
            """
            INSERT INTO notes (puuid, severity, tags, body, created_at, updated_at)
            VALUES ('p1', 3, '["thrower","toxic"]', 'threw the game',
                    '2026-09-18T21:23:00+00:00', '2026-09-18T21:23:00+00:00')
            """,
            """
            INSERT INTO notes (puuid, severity, tags, body, created_at, updated_at)
            VALUES ('p2', 1, '[]', '', '2026-09-01T10:00:00+00:00', '2026-09-01T10:00:00+00:00')
            """,
            """
            INSERT INTO encounters (puuid, match_id, seen_at, relation, agent, map_name, queue)
            VALUES ('p1', 'm1', '2026-09-18T21:00:00+00:00', 'enemy', 'Jett', 'Ascent', 'competitive')
            """,
            """
            INSERT INTO encounters (puuid, match_id, seen_at, relation, agent, map_name, queue)
            VALUES ('p3', 'm2', '2026-09-17T12:00:00+00:00', 'ally', NULL, NULL, NULL)
            """,
        })
        {
            using var command = db.CreateCommand();
            command.CommandText = sql;
            command.ExecuteNonQuery();
        }
    }

    private NotesStore NewStore() => new(Path.Combine(_dir, $"v3-{Guid.NewGuid():N}.sqlite3"));

    [Fact]
    public void Notes_and_encounters_come_across()
    {
        using var store = NewStore();

        var result = LegacyImport.ImportIfEmpty(store, _legacy);

        Assert.Equal(2, result.Notes);
        Assert.Equal(2, result.Encounters);
        Assert.Null(result.Error);
    }

    [Fact]
    public async Task A_note_keeps_its_severity_text_and_tags()
    {
        using var store = NewStore();
        LegacyImport.ImportIfEmpty(store, _legacy);

        var note = (await store.NotesForAsync(["p1"]))["p1"].Single();

        Assert.Equal(Severity.Dodge, note.Severity);
        Assert.Equal("threw the game", note.Text);
        Assert.Equal(["thrower", "toxic"], note.Tags);
    }

    [Fact]
    public async Task A_note_keeps_when_it_was_written()
    {
        // Imported notes must not all claim to be from today.
        using var store = NewStore();
        LegacyImport.ImportIfEmpty(store, _legacy);

        var note = (await store.NotesForAsync(["p1"]))["p1"].Single();

        Assert.Equal(2026, note.Created.Year);
        Assert.Equal(9, note.Created.Month);
        Assert.Equal(18, note.Created.Day);
    }

    [Fact]
    public async Task Encounters_keep_which_side_they_were_on()
    {
        using var store = NewStore();
        LegacyImport.ImportIfEmpty(store, _legacy);

        var summary = await store.EncounterSummaryAsync(["p1", "p3"]);

        Assert.Equal(1, summary["p1"].Enemy);
        Assert.Equal(1, summary["p3"].Ally);
    }

    [Fact]
    public void Importing_twice_does_not_double_the_history()
    {
        // Re-importing would quietly inflate "met 8 times".
        using var store = NewStore();

        var first = LegacyImport.ImportIfEmpty(store, _legacy);
        var second = LegacyImport.ImportIfEmpty(store, _legacy);

        Assert.Equal(2, first.Encounters);
        Assert.Equal(0, second.Encounters);
    }

    [Fact]
    public async Task A_store_with_anything_in_it_is_left_alone()
    {
        using var store = NewStore();
        await store.AddNoteAsync("mine", Severity.Watch);

        var result = LegacyImport.ImportIfEmpty(store, _legacy);

        Assert.Equal(0, result.Notes);
        Assert.Empty(await store.NotesForAsync(["p1"]));
    }

    [Fact]
    public void No_old_database_is_simply_nothing_to_do()
    {
        using var store = NewStore();

        var result = LegacyImport.ImportIfEmpty(store, Path.Combine(_dir, "absent.sqlite3"));

        Assert.False(result.Anything);
        Assert.Null(result.Error);
    }

    [Fact]
    public void An_old_file_of_the_wrong_shape_does_not_stop_the_app()
    {
        var wrong = Path.Combine(_dir, "wrong.sqlite3");
        using (var db = new SqliteConnection($"Data Source={wrong}"))
        {
            db.Open();
            using var command = db.CreateCommand();
            command.CommandText = "CREATE TABLE something_else (a TEXT)";
            command.ExecuteNonQuery();
        }

        using var store = NewStore();
        var result = LegacyImport.ImportIfEmpty(store, wrong);

        Assert.False(result.Anything);
        Assert.NotNull(result.Error);   // reported, not thrown
    }

    [Fact]
    public void The_old_database_is_never_written_to()
    {
        var before = File.GetLastWriteTimeUtc(_legacy);
        var bytes = new FileInfo(_legacy).Length;

        using var store = NewStore();
        LegacyImport.ImportIfEmpty(store, _legacy);
        SqliteConnection.ClearAllPools();

        // It is the live data of an app still in use.
        Assert.Equal(before, File.GetLastWriteTimeUtc(_legacy));
        Assert.Equal(bytes, new FileInfo(_legacy).Length);
    }
}
