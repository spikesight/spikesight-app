namespace SpikeSight.Core.Store;

/// <summary>Where SpikeSight keeps its own files.</summary>
public static class Paths
{
    public static string DataDir { get; set; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SpikeSight");

    public static string NotesDb => Path.Combine(DataDir, "notes.sqlite3");

    /// <summary>
    /// Match summaries, kept forever because a finished match cannot change.
    ///
    /// Separate from 2.x's folder: the two store different shapes, and both
    /// will be installed at once while this is still a preview.
    /// </summary>
    public static string MatchCacheDir => Path.Combine(DataDir, "matchcache-v3");

    public static void EnsureDataDir() => Directory.CreateDirectory(DataDir);
}
