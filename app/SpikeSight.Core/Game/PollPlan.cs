namespace SpikeSight.Core.Game;

public enum PollAction
{
    /// <summary>Nothing changed; wait for the next heartbeat.</summary>
    Wait,

    /// <summary>Ask the game servers who is in the lobby.</summary>
    Rescan,

    /// <summary>Back in menus: keep the last board, mark it stale.</summary>
    Menus,
}

/// <summary>
/// When the loop is allowed to reach out to Riot.
///
/// The heartbeat is the <em>local</em> presence endpoint, which is free. Only
/// when that actually changes does anything touch glz or pd, which is what
/// keeps a scoreboard app from behaving like a scraper. Kept pure and
/// separate from the loop so the rules can be tested without a game running.
/// </summary>
public sealed class PollPlan
{
    /// <summary>Agent select keeps changing as people lock in.</summary>
    public double PregameRescanSeconds { get; init; } = 5.0;

    /// <summary>How often to probe glz directly when presence tells us nothing.</summary>
    public double BlindDetectSeconds { get; init; } = 12.0;

    private string _state = "UNKNOWN";
    private string? _matchId;
    private double _lastPregameScan;
    // Negative infinity so the first probe happens at once: a cold start with
    // no presence should not sit blind for a whole interval before looking.
    private double _lastBlindDetect = double.NegativeInfinity;

    public string State => _state;
    public string? MatchId => _matchId;

    /// <param name="presenceState">What the local client says, or null when
    /// it will not say - the game not running, or presence not published.</param>
    /// <param name="now">Monotonic seconds.</param>
    public PollAction Decide(string? presenceState, double now)
    {
        if (presenceState == "MENUS")
            return _state == "MENUS" ? PollAction.Wait : PollAction.Menus;

        if (presenceState is "PREGAME" or "INGAME")
        {
            // A state change, or we have not worked out which match yet.
            if (presenceState != _state || _matchId is null) return PollAction.Rescan;

            // Otherwise only agent select is worth re-reading, because the
            // roster is still filling up.
            if (_state == "PREGAME" && now - _lastPregameScan >= PregameRescanSeconds)
                return PollAction.Rescan;

            return PollAction.Wait;
        }

        // No presence at all. Fall back to a slow direct probe rather than
        // going blind - but slowly, because this is the expensive path.
        if (now - _lastBlindDetect < BlindDetectSeconds) return PollAction.Wait;
        _lastBlindDetect = now;
        return PollAction.Rescan;
    }

    /// <summary>Record what a rescan found.</summary>
    /// <returns>True when this is a different match from the last one.</returns>
    public bool Observed(string state, string? matchId, double now)
    {
        var fresh = matchId != _matchId;
        _state = state;
        _matchId = matchId;
        if (state == "PREGAME") _lastPregameScan = now;
        return fresh;
    }

    public void BackToMenus()
    {
        _state = "MENUS";
        _matchId = null;
    }
}
