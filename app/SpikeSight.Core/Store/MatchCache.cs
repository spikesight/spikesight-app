using System.Collections.Concurrent;
using System.Text.Json;
using SpikeSight.Core.Game;

namespace SpikeSight.Core.Store;

/// <summary>
/// Disk cache for finished matches.
///
/// A finished match is immutable, so this keeps them forever. That removes
/// the single largest source of repeat traffic: ten players in a lobby very
/// often share recent games, and after the first lobby most of what a scan
/// needs is already here.
///
/// Only the compact summary is stored - the scoreboard line for each player,
/// never a name.
/// </summary>
public sealed class MatchCache
{
    private readonly string _dir;
    private readonly ConcurrentDictionary<string, MatchSummary> _memory = new();

    public MatchCache(string? directory = null)
    {
        _dir = directory ?? Paths.MatchCacheDir;
        Directory.CreateDirectory(_dir);
    }

    private string PathFor(string matchId)
    {
        // Match ids are uuids, but never trust one straight into a path.
        var safe = new string(matchId.Where(c => char.IsLetterOrDigit(c) || c is '-' or '_').ToArray());
        return Path.Combine(_dir, safe + ".json");
    }

    public MatchSummary? Peek(string matchId)
    {
        if (_memory.TryGetValue(matchId, out var cached)) return cached;

        var path = PathFor(matchId);
        if (!File.Exists(path)) return null;

        try
        {
            var summary = JsonSerializer.Deserialize<MatchSummary>(File.ReadAllText(path));
            // Written by an older build: fetch it again rather than serving a
            // shape this version does not understand.
            if (summary is null || summary.Version != MatchSummary.CurrentVersion) return null;
            _memory[matchId] = summary;
            return summary;
        }
        catch (Exception ex) when (ex is IOException or JsonException)
        {
            return null;
        }
    }

    public void Put(MatchSummary summary)
    {
        _memory[summary.MatchId] = summary;
        try
        {
            File.WriteAllText(PathFor(summary.MatchId), JsonSerializer.Serialize(summary));
        }
        catch (IOException)
        {
            // The memory copy is still good for this session.
        }
    }

    public int Count
    {
        get
        {
            try { return Directory.EnumerateFiles(_dir, "*.json").Count(); }
            catch (IOException) { return _memory.Count; }
        }
    }
}
