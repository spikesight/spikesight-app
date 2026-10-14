using System.Reflection;
using System.Text.Json;

namespace SpikeSight.Core.Game;

/// <summary>
/// Agent and map names.
///
/// Riot's content-service stopped carrying these - it returns only seasons,
/// events and disabled ids - so they ship with the app instead. That also
/// means the running app talks to nothing but 127.0.0.1 and Riot's own hosts.
/// </summary>
public sealed class GameContent
{
    private readonly Dictionary<string, string> _agents;
    private readonly Dictionary<string, string> _mapsByPath;
    private readonly Dictionary<string, string> _mapsById;

    private GameContent(Dictionary<string, string> agents, Dictionary<string, string> maps)
    {
        _agents = agents;
        _mapsByPath = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        _mapsById = new Dictionary<string, string>();
        foreach (var (key, name) in maps)
        {
            if (key.StartsWith("uuid:", StringComparison.Ordinal)) _mapsById[key[5..]] = name;
            else _mapsByPath[key] = name;
        }
    }

    public static GameContent Bundled { get; } = new(Load("agents.json"), Load("maps.json"));

    private static Dictionary<string, string> Load(string file)
    {
        var assembly = Assembly.GetExecutingAssembly();
        var name = assembly.GetManifestResourceNames()
            .FirstOrDefault(n => n.EndsWith(file, StringComparison.OrdinalIgnoreCase));
        if (name is null) return [];

        using var stream = assembly.GetManifestResourceStream(name);
        if (stream is null) return [];

        using var document = JsonDocument.Parse(stream);
        return document.RootElement.EnumerateObject()
            .Where(p => p.Value.ValueKind == JsonValueKind.String)
            .ToDictionary(p => p.Name, p => p.Value.GetString()!);
    }

    public int AgentCount => _agents.Count;
    public int MapCount => _mapsByPath.Count;

    public string? AgentName(string? characterId)
    {
        var key = MmrView.NormalizeId(characterId);
        return key.Length > 0 && _agents.TryGetValue(key, out var name) ? name : null;
    }

    /// <summary>
    /// Map name from whatever the payload used to identify it.
    ///
    /// Falls back to the code name in the asset path, so a map that shipped
    /// after this table reads as "Duality" rather than as nothing at all.
    /// </summary>
    public string? MapName(string? mapId)
    {
        if (mapId is not { Length: > 0 }) return null;
        if (_mapsByPath.TryGetValue(mapId, out var byPath)) return byPath;
        if (_mapsById.TryGetValue(MmrView.NormalizeId(mapId), out var byId)) return byId;

        var tail = mapId.TrimEnd('/').Split('/').LastOrDefault();
        return tail is { Length: > 0 } ? tail : null;
    }
}
