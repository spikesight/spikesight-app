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
    private StatsEngine? _stats;
    private Credentials? _credentials;
    private Acts _acts = Acts.Empty;
    private Snapshot _last = new("STARTING", null, "Connecting to the Riot Client...");

    /// <summary>
    /// Recent form for the lobby currently on screen.
    ///
    /// Agent select rebuilds the roster every few seconds as people lock in,
    /// and each rebuild is a cheap one that fetches no stats. Without holding
    /// them here, the K/D column would fill in once and then blank out for the
    /// rest of the lobby - which is exactly what 2.x did until 2.2.2.
    /// </summary>
    private (string MatchId, IReadOnlyDictionary<string, PlayerStats> Stats)? _form;
    private StatsResult? _lastStats;

    /// <summary>
    /// A match in progress, held until it ends.
    ///
    /// Agent select is never held here: a lobby you back out of must leave no
    /// trace, or the encounter history starts lying about who you have played
    /// with.
    /// </summary>
    private (Board Board, string MatchId)? _pending;

    /// <summary>Set when a flag was added or cleared and the board is stale.</summary>
    private volatile bool _notesChanged;

    /// <summary>The payload of the lobby on screen, kept so a flag can be
    /// redrawn without asking Riot anything.</summary>
    private (JsonElement Match, string State, string MatchId)? _onScreen;

    public MatchPoller(LocalClient local, Func<double>? now = null)
    {
        _local = local;
        _now = now ?? (() => Environment.TickCount64 / 1000.0);
    }

    /// <summary>Where encounters are recorded, once a match has ended.</summary>
    public Store.NotesStore? Notes { get; init; }

    public event Action<Snapshot>? Updated;

    /// <summary>Something worth saying out loud, such as the scoring field
    /// no longer meaning what ACS assumes.</summary>
    public event Action<string>? Warned;

    /// <summary>Fetch recent form as well as ranks. Off makes a scan much lighter.</summary>
    public bool DeepStats { get; init; } = true;

    /// <summary>Thresholds the board scores against.</summary>
    public Settings Settings { get; init; } = Settings.Default;

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
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                return;   // shutting down, which is the only reason to stop
            }
            catch (OperationCanceledException)
            {
                // A request timed out. HttpClient reports that as a
                // TaskCanceledException, which is an OperationCanceledException
                // - so catching that broadly used to kill the loop outright on
                // one slow request, leaving the window showing whatever it last
                // knew with no crash and nothing in the log.
                Warned?.Invoke("A request to Riot timed out; still watching.");
                delay = IdleDelay;
            }
            catch (Exception ex)
            {
                // Nothing may kill this loop except shutdown. A scoreboard
                // that silently stops updating is worse than one that says it
                // broke, because the stale board still looks live.
                Warned?.Invoke($"Polling hit an unexpected error ({ex.GetType().Name}); "
                               + "still watching.");
                delay = IdleDelay;
            }

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
        _stats = new StatsEngine(_remote);
        _stats.Warning += message => Warned?.Invoke(message);

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
        if (_notesChanged)
        {
            _notesChanged = false;
            if (_onScreen is { } shown && _remote is { } client)
            {
                Publish(new Snapshot(shown.State,
                    await BuildAsync(client, shown.Match, _credentials!.Puuid, shown.State, ct), ""));
                return;
            }
        }

        var presence = await PresenceStateAsync(ct);

        switch (_plan.Decide(presence, _now()))
        {
            case PollAction.Wait:
                return;

            case PollAction.Menus:
                await FinishMatchAsync(ct);
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
            await FinishMatchAsync(ct);
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

        var fresh = matchId != _form?.MatchId;
        if (fresh)
        {
            // Moved on to a different match: bank the previous one first.
            if (_pending is { } held && held.MatchId != matchId) await FinishMatchAsync(ct);
            _form = null;
            _lastStats = null;
        }

        _onScreen = (payload, state, matchId);

        // Quick pass: ranks and names, on screen straight away.
        Publish(new Snapshot(state, await BuildAsync(remote, payload, puuid, state, ct), ""));

        if (!DeepStats || !fresh || _stats is null) return;

        // Then recent form, which is the slow part, behind the board already
        // being useful.
        var roster = Roster(payload, state).ToList();
        var queue = QueueOf(payload);
        StatsResult result;
        try
        {
            result = await _stats.CollectAsync(roster, queue, ct: ct);
        }
        catch (SpikeSightException)
        {
            return;   // the board without form beats no board
        }

        if (_plan.MatchId != matchId) return;   // moved on while we were reading
        _form = (matchId, result.Players);
        _lastStats = result;
        Publish(new Snapshot(state, await BuildAsync(remote, payload, puuid, state, ct), ""));
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

        // Party ids the local client already publishes, free of charge. Riot
        // will not answer about anyone outside our own party, so this is the
        // only confirmed source there is.
        var partyIds = new Dictionary<string, string>();
        var presenceNames = new Dictionary<string, (string, string)>();
        try
        {
            foreach (var presence in await _local.GetPresencesAsync(ct))
            {
                if (presence.Puuid.Length == 0) continue;
                if (presence.PartyId.Length > 0) partyIds[presence.Puuid] = presence.PartyId;
                if (presence.GameName.Length > 0)
                    presenceNames[presence.Puuid] = (presence.GameName, presence.GameTag);
            }
        }
        catch (SpikeSightException) { /* inference still works without it */ }

        // Notes and encounter counts are read before this lobby is recorded,
        // so "3rd time" means three previous games, not counting this one.
        var roster = Roster(match, state).ToList();
        var notes = Notes is null ? null : await Notes.NotesForAsync(roster, ct);
        var encounters = Notes is null ? null : await Notes.EncounterSummaryAsync(roster, ct);

        var usable = _form?.MatchId == _plan.MatchId;
        var board = BoardBuilder.Build(
            match, selfPuuid, mmr, names, _acts.Current?.Id, settings: Settings, state: state,
            stats: usable ? _form?.Stats : null,
            partyIds: partyIds,
            coParty: usable ? _lastStats?.CoParty : null,
            coParties: usable ? _lastStats?.CoParties : null,
            notes: notes,
            encounters: encounters);
        return board;
    }

    private static string Explain(RemoteException ex) => ex.Status switch
    {
        429 => "Riot asked us to slow down; waiting a minute.",
        _ when ex.Hint.Length > 0 => ex.Hint,
        _ => ex.Message,
    };

    /// <summary>
    /// A live match ended - now, and only now, log who was in it.
    ///
    /// This is what keeps dodges out of the history: agent-select rosters are
    /// never banked, so a lobby you back out of was never an encounter.
    /// </summary>
    private async Task FinishMatchAsync(CancellationToken ct)
    {
        if (_pending is not { } held || Notes is null) { _pending = null; return; }
        _pending = null;

        foreach (var player in held.Board.Players)
        {
            if (player.IsSelf || player.Puuid.Length == 0) continue;
            try
            {
                await Notes.RecordEncounterAsync(
                    player.Puuid, held.MatchId, player.Side,
                    player.AgentName, held.Board.MapName, held.Board.Label, ct);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                return;   // bookkeeping must never break the loop
            }
        }
    }

    private static IEnumerable<string> Roster(JsonElement match, string state)
    {
        var entries = state == "PREGAME"
            ? match.GetProperty("AllyTeam").GetProperty("Players").EnumerateArray()
            : match.GetProperty("Players").EnumerateArray();

        foreach (var entry in entries)
        {
            if (entry.TryGetProperty("Subject", out var s) && s.GetString() is { Length: > 0 } puuid)
                yield return puuid;
        }
    }

    /// <summary>
    /// Which queue's history to read.
    ///
    /// Follows the lobby. Pulling competitive history while you are in a
    /// Swiftplay game is the wrong evidence twice over: the K/D is from a
    /// different mode, and a group who only play Swiftplay together would
    /// show no shared parties at all.
    /// </summary>
    private static string QueueOf(JsonElement match)
    {
        if (match.TryGetProperty("MatchmakingData", out var mm)
            && mm.ValueKind == JsonValueKind.Object
            && mm.TryGetProperty("QueueID", out var q))
        {
            return q.GetString() ?? "";
        }
        return match.TryGetProperty("QueueID", out var direct) ? direct.GetString() ?? "" : "";
    }

    private static string? MatchId(JsonElement? element)
        => element is { ValueKind: JsonValueKind.Object } value
           && value.TryGetProperty("MatchID", out var id) ? id.GetString() : null;

    private void Publish(Snapshot snapshot)
    {
        _last = snapshot;
        // Held until the match ends; see FinishMatchAsync. Only a live match
        // counts, which is what keeps a dodged lobby out of the history.
        if (snapshot is { State: "INGAME", Stale: false, Board: { } board }
            && _plan.MatchId is { Length: > 0 } matchId)
        {
            _pending = (board, matchId);
        }
        Updated?.Invoke(snapshot);
    }

    /// <summary>
    /// Re-read the board after a flag changes.
    ///
    /// Nothing about the lobby moved, so this must not cost a scan: the
    /// roster, ranks and form are all still good and only the notes differ.
    /// </summary>
    public void RefreshNotes() => _notesChanged = true;

    public void Dispose() => _remote?.Dispose();
}
