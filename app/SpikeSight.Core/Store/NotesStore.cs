using Microsoft.Data.Sqlite;

namespace SpikeSight.Core.Store;

/// <summary>How badly you want to avoid someone.</summary>
public enum Severity { Watch = 1, Avoid = 2, Dodge = 3 }

public sealed record PlayerNote(
    long Id, string Puuid, Severity Severity, string Text,
    IReadOnlyList<string> Tags, DateTimeOffset Created, DateTimeOffset Updated);

public sealed record EncounterSummary(int Count, int Ally, int Enemy, DateTimeOffset? Last);

/// <summary>
/// Your own notes on players, and a record of who you have actually met.
///
/// Everything is keyed on puuid and nothing else is required, which is what
/// lets a note follow a player whose riot ID is hidden: the note still
/// resurfaces without ever recording who they are.
///
/// Writes are serialised through one connection. The Python build lost this
/// database twice to concurrent access, and the second time the cause was a
/// cancelled query releasing its lock while the statement was still running.
/// One connection, one gate, no cleverness.
/// </summary>
public sealed class NotesStore : IDisposable
{
    private readonly SqliteConnection _db;
    private readonly SemaphoreSlim _gate = new(1, 1);

    public string Path { get; }

    public NotesStore(string path)
    {
        Path = path;
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path)!);

        _db = new SqliteConnection(new SqliteConnectionStringBuilder
        {
            DataSource = path,
            Mode = SqliteOpenMode.ReadWriteCreate,
            Pooling = false,
        }.ToString());
        _db.Open();

        // WAL so a reader never blocks the writer, and NORMAL because losing
        // the last note to a power cut matters less than writing constantly.
        Execute("PRAGMA journal_mode=WAL");
        Execute("PRAGMA synchronous=NORMAL");
        Execute("PRAGMA foreign_keys=ON");
        CreateSchema();
    }

    private void Execute(string sql)
    {
        using var command = _db.CreateCommand();
        command.CommandText = sql;
        command.ExecuteNonQuery();
    }

    private void CreateSchema()
    {
        Execute("""
            CREATE TABLE IF NOT EXISTS notes (
              id        INTEGER PRIMARY KEY,
              puuid     TEXT NOT NULL,
              severity  INTEGER NOT NULL,
              text      TEXT NOT NULL DEFAULT '',
              tags      TEXT NOT NULL DEFAULT '',
              created   INTEGER NOT NULL,
              updated   INTEGER NOT NULL
            )
            """);
        Execute("CREATE INDEX IF NOT EXISTS notes_puuid ON notes(puuid)");
        Execute("""
            CREATE TABLE IF NOT EXISTS encounters (
              id        INTEGER PRIMARY KEY,
              puuid     TEXT NOT NULL,
              match_id  TEXT NOT NULL,
              seen      INTEGER NOT NULL,
              relation  TEXT NOT NULL,
              agent     TEXT,
              map_name  TEXT,
              queue     TEXT,
              UNIQUE(puuid, match_id)
            )
            """);
        Execute("CREATE INDEX IF NOT EXISTS encounters_puuid ON encounters(puuid)");
    }

    /// <summary>
    /// Is the file readable at all?
    ///
    /// Checked on open because a corrupt database that is merely *opened*
    /// successfully will fail later, somewhere less obvious.
    /// </summary>
    public bool IsHealthy()
    {
        try
        {
            using var command = _db.CreateCommand();
            command.CommandText = "PRAGMA quick_check";
            return command.ExecuteScalar() as string == "ok";
        }
        catch (SqliteException)
        {
            return false;
        }
    }

    private async Task<T> LockedAsync<T>(Func<T> work, CancellationToken ct)
    {
        await _gate.WaitAsync(ct);
        try { return work(); }
        finally { _gate.Release(); }
    }

    /// <summary>Nothing recorded yet, so an import cannot duplicate anything.</summary>
    public bool IsEmpty()
    {
        using var command = _db.CreateCommand();
        command.CommandText =
            "SELECT (SELECT COUNT(*) FROM notes) + (SELECT COUNT(*) FROM encounters)";
        return Convert.ToInt64(command.ExecuteScalar()) == 0;
    }

    /// <summary>Insert a note keeping its original timestamp. Import only.</summary>
    public void InsertImportedNote(
        string puuid, Severity severity, string text, IEnumerable<string> tags, long created)
    {
        using var command = _db.CreateCommand();
        command.CommandText = """
            INSERT INTO notes (puuid, severity, text, tags, created, updated)
            VALUES ($puuid, $severity, $text, $tags, $created, $created)
            """;
        command.Parameters.AddWithValue("$puuid", puuid);
        command.Parameters.AddWithValue("$severity", (int)severity);
        command.Parameters.AddWithValue("$text", text);
        command.Parameters.AddWithValue("$tags", string.Join(',', tags));
        command.Parameters.AddWithValue("$created", created);
        command.ExecuteNonQuery();
    }

    /// <summary>Insert an encounter keeping when it happened. Import only.</summary>
    public void InsertImportedEncounter(
        string puuid, string matchId, long seen, string relation,
        string? agent, string? mapName, string? queue)
    {
        using var command = _db.CreateCommand();
        command.CommandText = """
            INSERT INTO encounters (puuid, match_id, seen, relation, agent, map_name, queue)
            VALUES ($puuid, $match, $seen, $relation, $agent, $map, $queue)
            ON CONFLICT(puuid, match_id) DO NOTHING
            """;
        command.Parameters.AddWithValue("$puuid", puuid);
        command.Parameters.AddWithValue("$match", matchId);
        command.Parameters.AddWithValue("$seen", seen);
        command.Parameters.AddWithValue("$relation", relation);
        command.Parameters.AddWithValue("$agent", (object?)agent ?? DBNull.Value);
        command.Parameters.AddWithValue("$map", (object?)mapName ?? DBNull.Value);
        command.Parameters.AddWithValue("$queue", (object?)queue ?? DBNull.Value);
        command.ExecuteNonQuery();
    }

    private static long Now() => DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    private static DateTimeOffset At(long seconds) => DateTimeOffset.FromUnixTimeSeconds(seconds);

    public Task<long> AddNoteAsync(
        string puuid, Severity severity, string text = "",
        IEnumerable<string>? tags = null, CancellationToken ct = default)
        => LockedAsync(() =>
        {
            using var command = _db.CreateCommand();
            command.CommandText = """
                INSERT INTO notes (puuid, severity, text, tags, created, updated)
                VALUES ($puuid, $severity, $text, $tags, $now, $now)
                RETURNING id
                """;
            command.Parameters.AddWithValue("$puuid", puuid);
            command.Parameters.AddWithValue("$severity", (int)severity);
            command.Parameters.AddWithValue("$text", text);
            command.Parameters.AddWithValue("$tags", string.Join(',', tags ?? []));
            command.Parameters.AddWithValue("$now", Now());
            return (long)command.ExecuteScalar()!;
        }, ct);

    public Task<bool> DeleteNoteAsync(long id, CancellationToken ct = default)
        => LockedAsync(() =>
        {
            using var command = _db.CreateCommand();
            command.CommandText = "DELETE FROM notes WHERE id = $id";
            command.Parameters.AddWithValue("$id", id);
            return command.ExecuteNonQuery() > 0;
        }, ct);

    /// <summary>Notes for a whole lobby in one query.</summary>
    public Task<Dictionary<string, List<PlayerNote>>> NotesForAsync(
        IEnumerable<string> puuids, CancellationToken ct = default)
        => LockedAsync(() =>
        {
            var wanted = puuids.Where(p => p.Length > 0).Distinct().ToList();
            var result = new Dictionary<string, List<PlayerNote>>();
            if (wanted.Count == 0) return result;

            using var command = _db.CreateCommand();
            var names = wanted.Select((_, i) => $"$p{i}").ToList();
            command.CommandText =
                $"SELECT id, puuid, severity, text, tags, created, updated FROM notes "
                + $"WHERE puuid IN ({string.Join(',', names)}) ORDER BY severity DESC, updated DESC";
            for (var i = 0; i < wanted.Count; i++)
                command.Parameters.AddWithValue(names[i], wanted[i]);

            using var reader = command.ExecuteReader();
            while (reader.Read())
            {
                var tags = reader.GetString(4);
                var note = new PlayerNote(
                    reader.GetInt64(0), reader.GetString(1), (Severity)reader.GetInt32(2),
                    reader.GetString(3),
                    tags.Length == 0 ? [] : tags.Split(','),
                    At(reader.GetInt64(5)), At(reader.GetInt64(6)));

                if (!result.TryGetValue(note.Puuid, out var list))
                    result[note.Puuid] = list = [];
                list.Add(note);
            }
            return result;
        }, ct);

    /// <summary>
    /// Record that a match actually happened.
    ///
    /// Only ever called once a match has started, never from agent select -
    /// a lobby you dodged was not an encounter, and logging it would make the
    /// history lie.
    /// </summary>
    public Task RecordEncounterAsync(
        string puuid, string matchId, string relation,
        string? agent = null, string? mapName = null, string? queue = null,
        CancellationToken ct = default)
        => LockedAsync<object?>(() =>
        {
            using var command = _db.CreateCommand();
            command.CommandText = """
                INSERT INTO encounters (puuid, match_id, seen, relation, agent, map_name, queue)
                VALUES ($puuid, $match, $now, $relation, $agent, $map, $queue)
                ON CONFLICT(puuid, match_id) DO NOTHING
                """;
            command.Parameters.AddWithValue("$puuid", puuid);
            command.Parameters.AddWithValue("$match", matchId);
            command.Parameters.AddWithValue("$now", Now());
            command.Parameters.AddWithValue("$relation", relation);
            command.Parameters.AddWithValue("$agent", (object?)agent ?? DBNull.Value);
            command.Parameters.AddWithValue("$map", (object?)mapName ?? DBNull.Value);
            command.Parameters.AddWithValue("$queue", (object?)queue ?? DBNull.Value);
            command.ExecuteNonQuery();
            return null;
        }, ct);

    public Task<Dictionary<string, EncounterSummary>> EncounterSummaryAsync(
        IEnumerable<string> puuids, CancellationToken ct = default)
        => LockedAsync(() =>
        {
            var wanted = puuids.Where(p => p.Length > 0).Distinct().ToList();
            var result = new Dictionary<string, EncounterSummary>();
            if (wanted.Count == 0) return result;

            using var command = _db.CreateCommand();
            var names = wanted.Select((_, i) => $"$p{i}").ToList();
            command.CommandText = $"""
                SELECT puuid,
                       COUNT(*),
                       SUM(relation = 'ally'),
                       SUM(relation = 'enemy'),
                       MAX(seen)
                FROM encounters WHERE puuid IN ({string.Join(',', names)})
                GROUP BY puuid
                """;
            for (var i = 0; i < wanted.Count; i++)
                command.Parameters.AddWithValue(names[i], wanted[i]);

            using var reader = command.ExecuteReader();
            while (reader.Read())
            {
                result[reader.GetString(0)] = new EncounterSummary(
                    reader.GetInt32(1), reader.GetInt32(2), reader.GetInt32(3),
                    reader.IsDBNull(4) ? null : At(reader.GetInt64(4)));
            }
            return result;
        }, ct);

    public void Dispose()
    {
        _gate.Dispose();
        _db.Dispose();
        SqliteConnection.ClearAllPools();
    }
}
