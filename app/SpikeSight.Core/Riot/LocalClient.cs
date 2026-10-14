using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace SpikeSight.Core.Riot;

/// <summary>Tokens the local client minted for whoever is signed in.</summary>
public sealed record Credentials(string Puuid, string AccessToken, string Entitlement)
{
    public string Masked => $"{Puuid[..Math.Min(8, Puuid.Length)]}... (token {AccessToken.Length} chars)";
}

/// <summary>
/// GET-only client for the Riot Client's own loopback API.
///
/// TLS verification is off for 127.0.0.1 and nowhere else: the client serves
/// a self-signed certificate no public trust store will ever accept. Nothing
/// here talks to the game process, reads memory, or writes anything.
/// </summary>
public sealed class LocalClient : IDisposable
{
    private readonly TimeSpan _timeout;
    private HttpClient? _http;
    private Lockfile? _lockfile;

    public LocalClient(TimeSpan? timeout = null) => _timeout = timeout ?? TimeSpan.FromSeconds(8);

    private HttpClient Connect()
    {
        var lockfile = Lockfile.Read();
        if (_http is not null && _lockfile == lockfile) return _http;

        // Port and password change when the client restarts.
        _http?.Dispose();
        _lockfile = lockfile;

        var handler = new HttpClientHandler
        {
            ServerCertificateCustomValidationCallback = (_, _, _, _) => true,
        };
        var auth = Convert.ToBase64String(Encoding.UTF8.GetBytes($"riot:{lockfile.Password}"));
        _http = new HttpClient(handler)
        {
            BaseAddress = new Uri(lockfile.BaseUrl),
            Timeout = _timeout,
        };
        _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Basic", auth);
        _http.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        return _http;
    }

    public async Task<JsonElement> GetJsonAsync(string path, CancellationToken ct = default)
    {
        var http = Connect();
        HttpResponseMessage response;
        try
        {
            response = await http.GetAsync(path, ct);
        }
        catch (HttpRequestException ex)
        {
            // Usually: the client closed between reading the lockfile and now.
            _http?.Dispose();
            _http = null;
            throw new ClientNotRunningException($"Local API unreachable: {ex.Message}", ex);
        }

        if (response.StatusCode == System.Net.HttpStatusCode.NotFound)
            throw new SpikeSightException($"Local endpoint not found: {path}");

        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(ct);
        using var document = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
        return document.RootElement.Clone();
    }

    public async Task<Credentials> GetCredentialsAsync(CancellationToken ct = default)
    {
        JsonElement data;
        try
        {
            data = await GetJsonAsync("/entitlements/v1/token", ct);
        }
        catch (ClientNotRunningException)
        {
            throw;
        }
        catch (SpikeSightException ex)
        {
            // Served only once somebody is signed in. Sitting at the login
            // screen with the client open is normal, never more so than on
            // patch day.
            throw new ClientNotRunningException(
                "The Riot Client is running, but nobody is signed in yet.", ex)
            { Hint = "Sign in and this will connect on its own." };
        }

        if (data.ValueKind != JsonValueKind.Object
            || !data.TryGetProperty("accessToken", out var token)
            || token.GetString() is not { Length: > 0 } accessToken)
        {
            throw new ClientNotRunningException("Local client has no signed-in session yet.");
        }

        return new Credentials(
            data.TryGetProperty("subject", out var s) ? s.GetString() ?? "" : "",
            accessToken,
            data.TryGetProperty("token", out var t) ? t.GetString() ?? "" : "");
    }

    /// <summary>Presence for every friend the client is publishing, and us.</summary>
    public async Task<IReadOnlyList<Presence>> GetPresencesAsync(CancellationToken ct = default)
    {
        var data = await GetJsonAsync("/chat/v4/presences", ct);
        if (data.ValueKind != JsonValueKind.Object
            || !data.TryGetProperty("presences", out var entries)
            || entries.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<Presence>();
        }

        var result = new List<Presence>();
        foreach (var entry in entries.EnumerateArray())
        {
            if (entry.ValueKind != JsonValueKind.Object) continue;
            var product = entry.TryGetProperty("product", out var p) ? p.GetString() : null;
            if (product is not (null or "valorant")) continue;
            result.Add(Presence.From(entry));
        }
        return result;
    }

    public async Task<Presence?> GetSelfPresenceAsync(string puuid, CancellationToken ct = default)
        => (await GetPresencesAsync(ct)).FirstOrDefault(p => p.Puuid == puuid);

    public void Dispose()
    {
        _http?.Dispose();
        _http = null;
        _lockfile = null;
    }
}
