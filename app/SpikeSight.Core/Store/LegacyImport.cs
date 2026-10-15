using System.Globalization;
using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace SpikeSight.Core.Store;

public sealed record ImportResult(int Notes, int Encounters, string? Error = null)
{
    public bool Anything => Notes > 0 || Encounters > 0;
}

/// <summary>
/// Bringing 2.x's notes and encounter history into v3.
///
/// The old database is opened read-only and never written to: it is the live
/// data of an app the user is still running, and its schema is not ours.
/// 2.x keeps `body`, `created_at`, `seen_at` and ISO timestamps; v3 keeps
/// `text`, `created`, `seen` and unix seconds.
///
/// Runs once, and only into an empty database - re-importing would duplicate
/// every encounter and quietly inflate "met 8 times".
/// </summary>
public static class LegacyImport
{
    public static ImportResult ImportIfEmpty(NotesStore store, string? legacyPath = null)
    {
        legacyPath ??= Paths.LegacyNotesDb;
        if (!File.Exists(legacyPath)) return new ImportResult(0, 0);
        // The marker, not emptiness: emptiness stops being true the moment
        // anything is written, and a second import would duplicate every note.
        if (store.HasImported() || !store.IsEmpty()) return new ImportResult(0, 0);

        try
        {
            return store.InTransaction(() => Import(store, legacyPath));
        }
        catch (SqliteException ex)
        {
            // A missing or unexpected table means the old file is not what we
            // thought. Starting empty is fine; losing the old file is not.
            return new ImportResult(0, 0, ex.Message);
        }
    }

    private static ImportResult Import(NotesStore store, string legacyPath)
    {
        using var old = new SqliteConnection(new SqliteConnectionStringBuilder
        {
            DataSource = legacyPath,
            Mode = SqliteOpenMode.ReadOnly,
            Pooling = false,
        }.ToString());
        old.Open();

        var notes = 0;
        var encounters = 0;

        using (var command = old.CreateCommand())
        {
            command.CommandText =
                "SELECT puuid, severity, body, tags, created_at FROM notes ORDER BY id";
            using var reader = command.ExecuteReader();
            while (reader.Read())
            {
                var puuid = reader.GetString(0);
                if (puuid.Length == 0) continue;

                var severity = (Severity)Math.Clamp(reader.GetInt32(1), 1, 3);
                var body = reader.IsDBNull(2) ? "" : reader.GetString(2);
                var tags = ParseTags(reader.IsDBNull(3) ? "" : reader.GetString(3));
                var created = ParseTime(reader.IsDBNull(4) ? null : reader.GetString(4));

                store.InsertImportedNote(puuid, severity, body, tags, created);
                notes++;
            }
        }

        using (var command = old.CreateCommand())
        {
            command.CommandText =
                "SELECT puuid, match_id, seen_at, relation, agent, map_name, queue "
                + "FROM encounters ORDER BY id";
            using var reader = command.ExecuteReader();
            while (reader.Read())
            {
                var puuid = reader.GetString(0);
                var matchId = reader.IsDBNull(1) ? "" : reader.GetString(1);
                if (puuid.Length == 0 || matchId.Length == 0) continue;

                store.InsertImportedEncounter(
                    puuid, matchId,
                    ParseTime(reader.IsDBNull(2) ? null : reader.GetString(2)),
                    reader.IsDBNull(3) ? "ally" : reader.GetString(3),
                    reader.IsDBNull(4) ? null : reader.GetString(4),
                    reader.IsDBNull(5) ? null : reader.GetString(5),
                    reader.IsDBNull(6) ? null : reader.GetString(6));
                encounters++;
            }
        }

        return new ImportResult(notes, encounters);
    }

    /// <summary>2.x stored tags as a JSON array; v3 keeps them comma-separated.</summary>
    private static List<string> ParseTags(string raw)
    {
        if (raw.Length == 0 || raw == "[]") return [];
        try
        {
            using var document = JsonDocument.Parse(raw);
            if (document.RootElement.ValueKind != JsonValueKind.Array) return [];
            return document.RootElement.EnumerateArray()
                .Where(e => e.ValueKind == JsonValueKind.String)
                .Select(e => e.GetString()!)
                .Where(t => t.Length > 0 && !t.Contains(','))
                .ToList();
        }
        catch (JsonException)
        {
            return [];
        }
    }

    /// <summary>2.x stored ISO text; v3 keeps unix seconds.</summary>
    private static long ParseTime(string? raw)
        => DateTimeOffset.TryParse(raw, CultureInfo.InvariantCulture,
               DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var parsed)
            ? parsed.ToUnixTimeSeconds()
            : DateTimeOffset.UtcNow.ToUnixTimeSeconds();
}
