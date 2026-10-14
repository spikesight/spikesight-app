namespace SpikeSight.Core.Riot;

/// <summary>
/// The file the Riot Client writes so its own pages can talk to it.
/// </summary>
public sealed record Lockfile(string Name, int Pid, int Port, string Password, string Protocol)
{
    public string BaseUrl => $"{Protocol}://127.0.0.1:{Port}";

    public static string DefaultPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Riot Games", "Riot Client", "Config", "lockfile");

    /// <summary>Read and parse it, or throw <see cref="ClientNotRunningException"/>.</summary>
    public static Lockfile Read(string? path = null)
    {
        path ??= DefaultPath;
        if (!File.Exists(path))
            throw new ClientNotRunningException("Riot Client lockfile not found.");

        string raw;
        try
        {
            // The client holds this open, so share the read.
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            using var reader = new StreamReader(stream);
            raw = reader.ReadToEnd().Trim();
        }
        catch (IOException ex)
        {
            throw new ClientNotRunningException($"Could not read lockfile: {ex.Message}", ex);
        }

        return Parse(raw);
    }

    /// <summary>
    /// Split from the right: the product name can itself contain a colon, so
    /// the five fields are only unambiguous counting backwards from the end.
    /// </summary>
    public static Lockfile Parse(string raw)
    {
        var parts = raw.Split(':');
        if (parts.Length < 5)
            throw new ClientNotRunningException("Lockfile is malformed (client may be starting up).");

        var protocol = parts[^1];
        var password = parts[^2];
        if (!int.TryParse(parts[^3], out var port) || !int.TryParse(parts[^4], out var pid))
            throw new ClientNotRunningException("Lockfile is malformed.");

        var name = string.Join(':', parts[..^4]);
        return new Lockfile(name, pid, port, password, protocol);
    }
}
