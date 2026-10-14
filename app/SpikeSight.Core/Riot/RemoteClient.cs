using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;

namespace SpikeSight.Core.Riot;

/// <summary>
/// Read-only client for Riot's pd/glz hosts, authenticated with the tokens
/// the local client already minted for the signed-in user.
///
/// Two verbs only:
///   GET for everything;
///   PUT for <c>/name-service/v2/players</c>, which is a batched *lookup* -
///   the puuid list is the query and the call has no side effects. It is the
///   only way the client exposes name resolution.
///
/// Nothing here can create, join, modify or leave anything.
/// </summary>
public sealed class RemoteClient : IDisposable
{
    private readonly LocalClient _local;
    private readonly HttpClient _http;
    private Credentials? _credentials;

    public Endpoints Endpoints { get; }

    public RemoteClient(LocalClient local, Endpoints endpoints, TimeSpan? timeout = null)
    {
        _local = local;
        Endpoints = endpoints;
        _http = new HttpClient { Timeout = timeout ?? TimeSpan.FromSeconds(12) };
    }

    private async Task<Credentials> CredentialsAsync(bool force, CancellationToken ct)
        => _credentials = (force || _credentials is null)
            ? await _local.GetCredentialsAsync(ct)
            : _credentials;

    private async Task<HttpRequestMessage> BuildAsync(
        HttpMethod method, string url, object? body, CancellationToken ct)
    {
        var credentials = await CredentialsAsync(false, ct);
        var request = new HttpRequestMessage(method, url);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", credentials.AccessToken);
        request.Headers.Add("X-Riot-Entitlements-JWT", credentials.Entitlement);
        request.Headers.Add("X-Riot-ClientPlatform", Endpoints.ClientPlatform);
        request.Headers.Add("X-Riot-ClientVersion", Endpoints.ClientVersion);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        if (body is not null) request.Content = JsonContent.Create(body);
        return request;
    }

    private async Task<JsonElement?> SendAsync(
        HttpMethod method, string url, object? body = null,
        bool allow404 = false, bool retried = false, CancellationToken ct = default)
    {
        HttpResponseMessage response;
        try
        {
            response = await _http.SendAsync(await BuildAsync(method, url, body, ct), ct);
        }
        catch (HttpRequestException ex)
        {
            throw new RemoteException($"{method} {url} failed: {ex.Message}", inner: ex);
        }

        if (response.StatusCode == HttpStatusCode.NotFound && allow404) return null;

        if (!retried && response.StatusCode
            is HttpStatusCode.BadRequest or HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden)
        {
            // Tokens rotate roughly hourly and the client re-mints them on its
            // own. Re-read once, then give up rather than hammering.
            await CredentialsAsync(force: true, ct);
            return await SendAsync(method, url, body, allow404, retried: true, ct);
        }

        if (response.StatusCode == HttpStatusCode.TooManyRequests)
            throw new RemoteException("Rate limited by Riot; backing off.", 429);

        if (!response.IsSuccessStatusCode)
        {
            var text = await response.Content.ReadAsStringAsync(ct);
            var error = new RemoteException(
                $"{method} {url} -> {(int)response.StatusCode} {text[..Math.Min(200, text.Length)]}",
                (int)response.StatusCode);
            if (response.StatusCode == HttpStatusCode.BadRequest)
            {
                // Riot checks X-Riot-ClientVersion, and ours is whatever the
                // last VALORANT session wrote to its log. The morning after a
                // patch that is yesterday's build.
                throw new RemoteException(error.Message, 400)
                { Hint = "If VALORANT just updated, launch the game once so SpikeSight can read the new build number." };
            }
            throw error;
        }

        await using var stream = await response.Content.ReadAsStreamAsync(ct);
        if (stream.CanSeek && stream.Length == 0) return null;
        using var document = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
        return document.RootElement.Clone();
    }

    private Task<JsonElement?> GetAsync(string url, bool allow404 = false, CancellationToken ct = default)
        => SendAsync(HttpMethod.Get, url, allow404: allow404, ct: ct);

    // -- live match state ---------------------------------------------------

    public Task<JsonElement?> CoregamePlayerAsync(string puuid, CancellationToken ct = default)
        => GetAsync($"{Endpoints.Glz}/core-game/v1/players/{puuid}", allow404: true, ct: ct);

    public Task<JsonElement?> CoregameMatchAsync(string matchId, CancellationToken ct = default)
        => GetAsync($"{Endpoints.Glz}/core-game/v1/matches/{matchId}", allow404: true, ct: ct);

    public Task<JsonElement?> PregamePlayerAsync(string puuid, CancellationToken ct = default)
        => GetAsync($"{Endpoints.Glz}/pregame/v1/players/{puuid}", allow404: true, ct: ct);

    public Task<JsonElement?> PregameMatchAsync(string matchId, CancellationToken ct = default)
        => GetAsync($"{Endpoints.Glz}/pregame/v1/matches/{matchId}", allow404: true, ct: ct);

    // -- per-player ---------------------------------------------------------

    public Task<JsonElement?> MmrAsync(string puuid, CancellationToken ct = default)
        => GetAsync($"{Endpoints.Pd}/mmr/v1/players/{puuid}", allow404: true, ct: ct);

    public Task<JsonElement?> MatchHistoryAsync(
        string puuid, int count = 10, string queue = "", CancellationToken ct = default)
    {
        var url = $"{Endpoints.Pd}/match-history/v1/history/{puuid}?startIndex=0&endIndex={count}";
        if (queue.Length > 0) url += $"&queue={queue}";
        return GetAsync(url, allow404: true, ct: ct);
    }

    public Task<JsonElement?> MatchDetailsAsync(string matchId, CancellationToken ct = default)
        => GetAsync($"{Endpoints.Pd}/match-details/v1/matches/{matchId}", allow404: true, ct: ct);

    /// <summary>
    /// Batched riot ID lookup.
    ///
    /// Callers MUST drop incognito players before calling this. SpikeSight
    /// does not resolve a name it is not allowed to display.
    /// </summary>
    public async Task<Dictionary<string, (string GameName, string TagLine)>> NamesAsync(
        IEnumerable<string> puuids, CancellationToken ct = default)
    {
        var wanted = puuids.Where(p => p.Length > 0).Distinct().ToList();
        var resolved = new Dictionary<string, (string, string)>();
        if (wanted.Count == 0) return resolved;

        var data = await SendAsync(
            HttpMethod.Put, $"{Endpoints.Pd}/name-service/v2/players", wanted, ct: ct);
        if (data is not { ValueKind: JsonValueKind.Array } array) return resolved;

        foreach (var entry in array.EnumerateArray())
        {
            if (entry.ValueKind != JsonValueKind.Object) continue;
            var subject = entry.TryGetProperty("Subject", out var s) ? s.GetString() ?? "" : "";
            if (subject.Length == 0) continue;
            resolved[subject] = (
                entry.TryGetProperty("GameName", out var g) ? g.GetString() ?? "" : "",
                entry.TryGetProperty("TagLine", out var t) ? t.GetString() ?? "" : "");
        }
        return resolved;
    }

    public void Dispose() => _http.Dispose();
}
