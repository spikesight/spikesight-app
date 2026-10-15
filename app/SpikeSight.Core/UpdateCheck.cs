using System.Reflection;
using System.Text.Json;

namespace SpikeSight.Core;

public sealed record UpdateInfo(string Current, string? Latest, string? Url, bool Available);

/// <summary>
/// Whether a newer SpikeSight has been released.
///
/// The only request this app makes that is not to 127.0.0.1 or Riot. It sends
/// the version being run and nothing else - no account, no match data, no
/// identifier - and it never downloads or installs anything. The download is
/// always the user's click.
/// </summary>
public sealed class UpdateCheck(HttpClient? http = null)
{
    private const string Releases =
        "https://api.github.com/repos/spikesight/spikesight-app/releases/latest";

    private readonly HttpClient _http = http ?? new HttpClient { Timeout = TimeSpan.FromSeconds(10) };

    public static string CurrentVersion =>
        Assembly.GetEntryAssembly()?.GetCustomAttribute<AssemblyInformationalVersionAttribute>()
            ?.InformationalVersion.Split('+')[0]
        ?? Assembly.GetEntryAssembly()?.GetName().Version?.ToString(3)
        ?? "0.0.0";

    public async Task<UpdateInfo> CheckAsync(CancellationToken ct = default)
    {
        var current = CurrentVersion;
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, Releases);
            // GitHub requires a user agent. It carries the version and nothing
            // else, which is also all the check needs to send.
            request.Headers.Add("User-Agent", $"SpikeSight/{current}");
            request.Headers.Add("Accept", "application/vnd.github+json");

            using var response = await _http.SendAsync(request, ct);
            if (!response.IsSuccessStatusCode) return new UpdateInfo(current, null, null, false);

            using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct));
            var root = document.RootElement;

            var tag = root.TryGetProperty("tag_name", out var t) ? t.GetString() ?? "" : "";
            var url = root.TryGetProperty("html_url", out var u) ? u.GetString() : null;
            if (tag.Length == 0) return new UpdateInfo(current, null, null, false);

            return new UpdateInfo(current, tag, url, IsNewer(tag, current));
        }
        catch (Exception ex) when (ex is HttpRequestException or JsonException or TaskCanceledException)
        {
            // Never worth bothering anybody about: not knowing whether there
            // is an update is the same as there not being one.
            return new UpdateInfo(current, null, null, false);
        }
    }

    /// <summary>
    /// Is <paramref name="candidate"/> a later release than
    /// <paramref name="current"/>?
    ///
    /// Compares numbers part by part, so 3.0.10 beats 3.0.9 rather than losing
    /// a string comparison. A pre-release suffix loses to the same version
    /// without one, which is what makes 3.0.0-preview upgrade to 3.0.0.
    /// </summary>
    public static bool IsNewer(string candidate, string current)
    {
        var (left, leftPre) = Parse(candidate);
        var (right, rightPre) = Parse(current);

        for (var i = 0; i < Math.Max(left.Count, right.Count); i++)
        {
            var a = i < left.Count ? left[i] : 0;
            var b = i < right.Count ? right[i] : 0;
            if (a != b) return a > b;
        }

        // Same numbers: a release beats a pre-release of itself.
        return !leftPre && rightPre;
    }

    private static (List<int> Parts, bool PreRelease) Parse(string version)
    {
        var text = version.TrimStart('v', 'V');
        var dash = text.IndexOf('-');
        var preRelease = dash >= 0;
        if (preRelease) text = text[..dash];

        var parts = text.Split('.')
            .Select(p => int.TryParse(p, out var n) ? n : 0)
            .ToList();
        return (parts, preRelease);
    }
}
