namespace SpikeSight.Core.Store;

/// <summary>Where SpikeSight keeps its own files.</summary>
public static class Paths
{
    public static string DataDir { get; set; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SpikeSight");

    /// <summary>
    /// v3's own notes database.
    ///
    /// Deliberately not 2.x's file. The schemas differ, and more to the point
    /// two apps writing one SQLite file is what corrupted that database twice
    /// already. Anything worth keeping is imported from it once, read-only.
    /// </summary>
    public static string NotesDb => Path.Combine(DataDir, "notes-v3.sqlite3");

    /// <summary>2.x's database, read once and never written to.</summary>
    public static string LegacyNotesDb => Path.Combine(DataDir, "notes.sqlite3");

    /// <summary>
    /// Match summaries, kept forever because a finished match cannot change.
    ///
    /// Separate from 2.x's folder: the two store different shapes, and both
    /// will be installed at once while this is still a preview.
    /// </summary>
    public static string MatchCacheDir => Path.Combine(DataDir, "matchcache-v3");

    public static void EnsureDataDir() => Directory.CreateDirectory(DataDir);
}
