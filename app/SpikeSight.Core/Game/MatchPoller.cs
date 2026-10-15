using System.Text.Json;
using SpikeSight.Core.Riot;

namespace SpikeSight.Core.Game;

/// <summary>What the window is currently showing.</summary>
public sealed record Snapshot(string State, Board? Board, string Message, bool Stale = false);

/// <summary>
/// Keeps the board in step with the game.
///
/// Local presence is the heartbeat; glz and pd are only touched when it
/// changes. A finished roster is never re-fetched, because it cannot change.
/// </summary>
public sealed class MatchPoller : IDisposable
{
    private readonly LocalClient _local;
    private readonly PollPlan _plan = new();
    private readonly Func<double> _now;

    private RemoteClient? _remote;
    private Credentials? _credentials;
    private Acts _acts = Acts.Empty;
    private Snapshot _last = new("STARTING", null, "Connecting to the Riot Client...");

    public MatchPoller(LocalClient local, Func<double>? now = null)
    {
        _local = local;
        _now = now ?? (() => Environment.TickCount64 / 1000.0);
    }

    public event Action<Snapshot>? Updated;

    public TimeSpan Heartbeat { get; init; } = TimeSpan.FromSeconds(2);
    public TimeSpan IdleDelay { get; init; } = TimeSpan.FromSeconds(5);

    /// <summary>
    /// How long to leave Riot alone after a 429. Long, deliberately: the
    /// polite answer to being told to slow down is to actually stop.
    /// </summary>
    public TimeSpan RateLimitBackoff { get; init; } = TimeSpan.FromSeconds(60);
    public Snapshot Current => _last;

    public async Task RunAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            var delay = Heartbeat;
            try
            {
                await ConnectAsync(ct);
                await TickAsync(ct);
            }
            catch (ClientNotRunningException ex)
            {
                Publish(new Snapshot("DISCONNECTED", null, ex.Message));
                delay = IdleDelay;
            }
            catch (RemoteException ex)
            {
                // Riot refusing a request is not a reason to throw away the
                // lobby already on screen - the board is still the best thing
                // we know. Say what happened and keep it.
                delay = ex.Status == 429 ? RateLimitBackoff : IdleDelay;
                Publish(_last.Board is null
                    ? new Snapshot("DISCONNECTED", null, Explain(ex))
                    : _last with { Message = Explain(ex) });
            }
            catch (SpikeSightException ex)
            {
                Publish(new Snapshot("DISCONNECTED", null, ex.Hint.Length > 0 ? ex.Hint : ex.Message));
                delay = IdleDelay;
            }
            catch (OperationCanceledException) { return; }

            try { await Task.Delay(delay, ct); }
            catch (OperationCanceledException) { return; }
        }
    }

    private async Task ConnectAsync(CancellationToken ct)
    {
        if (_remote is not null && _credentials is not null) return;

        _credentials = await _local.GetCredentialsAsync(ct);
        var endpoints = await Endpoints.ResolveAsync(_local, ct: ct);
        _remote = new RemoteClient(_local, endpoints);

        // The act timeline is the one thing still worth asking the content
        // service for, and it changes a few times a year.
        try
        {
            var content = await _remote.ContentAsync(ct);
            if (content is { } payload && payload.TryGetProperty("Seasons", out var seasons))
                _acts = Acts.FromSeasons(seasons);
        }
        catch (SpikeSightException)
        {
            // Acts are never worth failing over; peak-rank labels just lose
            // their act name.
        }
    }

    private async Task TickAsync(CancellationToken ct)
    {
        var presence = await PresenceStateAsync(ct);

        switch (_plan.Decide(presence, _now()))
        {
            case PollAction.Wait:
                return;

            case PollAction.Menus:
                _plan.BackToMenus();
                Publish(_last.Board is null
                    ? new Snapshot("MENUS", null, "In menus - queue up.")
                    : _last with { State = "MENUS", Stale = true,
                        Message = "Match over - the last lobby is still here." });
                return;

            case PollAction.Rescan:
                await ScanAsync(ct);
                return;
        }
    }

    private async Task<string?> PresenceStateAsync(CancellationToken ct)
    {
        try
        {
            var presence = await _local.GetSelfPresenceAsync(_credentials!.Puuid, ct);
            var state = presence?.SessionLoopState;
            return state is { Length: > 0 } ? state : null;
        }
        catch (SpikeSightException)
        {
            return null;
        }
    }

    private async Task ScanAsync(CancellationToken ct)
    {
        var remote = _remote!;
        var puuid = _credentials!.Puuid;

        var core = await remote.CoregamePlayerAsync(puuid, ct);
        var matchId = MatchId(core);
        var state = "INGAME";

        if (matchId is null)
        {
            var pre = await remote.PregamePlayerAsync(puuid, ct);
            matchId = MatchId(pre);
            state = "PREGAME";
        }

        if (matchId is null)
        {
            _plan.BackToMenus();
            Publish(_last.Board is null
                ? new Snapshot("MENUS", null, "In menus - queue up.")
                : _last with { State = "MENUS", Stale = true,
                    Message = "Match over - the last lobby is still here." });
            return;
        }

        var match = state == "INGAME"
            ? await remote.CoregameMatchAsync(matchId, ct)
            : await remote.PregameMatchAsync(matchId, ct);
        if (match is not { } payload) return;

        _plan.Observed(state, matchId, _now());

        if (state == "INGAME" && !BoardBuilder.IsLobby(payload))
        {
            // The Range, most likely. Since 13.06 people sit in it while they
            // queue, so "in a game with nobody else in it" is ordinary now.
            Publish(new Snapshot("MENUS", null, "Nobody else here - the Range, probably."));
            return;
        }

        var board = await BuildAsync(remote, payload, puuid, state, ct);
        Publish(new Snapshot(state, board, ""));
    }

    private async Task<Board> BuildAsync(
        RemoteClient remote, JsonElement match, string selfPuuid, string state, CancellationToken ct)
    {
        var entries = state == "PREGAME"
            ? match.GetProperty("AllyTeam").GetProperty("Players").EnumerateArray().ToList()
            : match.GetProperty("Players").EnumerateArray().ToList();

        var mmr = new Dictionary<string, JsonElement>();
        var resolvable = new List<string>();
        foreach (var entry in entries)
        {
            var puuid = entry.TryGetProperty("Subject", out var s) ? s.GetString() ?? "" : "";
            if (puuid.Length == 0) continue;
            if (await remote.MmrAsync(puuid, ct) is { } value) mmr[puuid] = value;

            var identity = entry.TryGetProperty("PlayerIdentity", out var id) ? id : default;
            if (!PrivacyFlags.FromIdentity(identity).Incognito) resolvable.Add(puuid);
        }

        // Incognito players are not in this list, so they are never asked about.
        var names = await remote.NamesAsync(resolvable, ct);

        return BoardBuilder.Build(match, selfPuuid, mmr, names, _acts.Current?.Id, state: state);
    }

    private static string Explain(RemoteException ex) => ex.Status switch
    {
        429 => "Riot asked us to slow down; waiting a minute.",
        _ when ex.Hint.Length > 0 => ex.Hint,
        _ => ex.Message,
    };

    private static string? MatchId(JsonElement? element)
        => element is { ValueKind: JsonValueKind.Object } value
           && value.TryGetProperty("MatchID", out var id) ? id.GetString() : null;

    private void Publish(Snapshot snapshot)
    {
        _last = snapshot;
        Updated?.Invoke(snapshot);
    }

    public void Dispose() => _remote?.Dispose();
}
