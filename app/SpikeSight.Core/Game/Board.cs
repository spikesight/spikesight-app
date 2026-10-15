using System.Text.Json;

namespace SpikeSight.Core.Game;

/// <summary>One player as the board shows them, after redaction.</summary>
public sealed record BoardPlayer(
    string Puuid,
    string Team,
    string Side,
    DisplayName Name,
    string? AgentName,
    PrivacyFlags Flags,
    RankView Rank,
    (int Tier, string ActId)? Peak,
    int? Level,
    bool IsSelf)
{
    public bool LevelHidden => Flags.HideAccountLevel;
    public double? RatingPoints => Rank.Points;
}

public sealed record BoardSide(string Side, string Label, IReadOnlyList<BoardPlayer> Players);

public sealed record Board(
    string State,
    string MapName,
    string Label,
    bool IsRanked,
    IReadOnlyList<BoardSide> Sides)
{
    public IEnumerable<BoardPlayer> Players => Sides.SelectMany(s => s.Players);
}

/// <summary>
/// Turns a live match payload into the rows the window renders.
///
/// Every privacy decision happens here, before anything reaches the view: a
/// hidden value is not passed along and then styled away, it is never passed
/// along.
/// </summary>
public static class BoardBuilder
{
    /// <summary>
    /// Above this the two sides are not two teams playing each other, and a
    /// side-by-side reading of them would be meaningless.
    /// </summary>
    public const int MaxTeamSize = 5;

    private static string Str(JsonElement e, string name)
        => e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString() ?? "" : "";

    private static int? IntOrNull(JsonElement e, string name)
        => e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number
            ? v.GetInt32() : null;

    /// <summary>
    /// Is this live session a lobby, or just us on our own?
    ///
    /// The Range runs on a game server like any match, so the client calls it
    /// being in a game. Since 13.06 you can practise there while you queue,
    /// which makes "in a game with nobody else in it" an ordinary thing to be
    /// - and there is nobody in it to scout.
    /// </summary>
    public static bool IsLobby(JsonElement match)
        => match.TryGetProperty("Players", out var players)
           && players.ValueKind == JsonValueKind.Array
           && players.GetArrayLength() >= 2;

    public static Board Build(
        JsonElement match,
        string selfPuuid,
        IReadOnlyDictionary<string, JsonElement> mmrByPuuid,
        IReadOnlyDictionary<string, (string GameName, string TagLine)> names,
        string? currentActId,
        GameContent? content = null,
        string state = "INGAME")
    {
        content ??= GameContent.Bundled;

        // Agent select hands over only your own side; the enemy team is not
        // in the payload at all until the match starts.
        var entries = match.TryGetProperty("Players", out var list)
                      && list.ValueKind == JsonValueKind.Array
            ? list.EnumerateArray().ToList()
            : match.TryGetProperty("AllyTeam", out var allyTeam)
              && allyTeam.ValueKind == JsonValueKind.Object
              && allyTeam.TryGetProperty("Players", out var allyPlayers)
              && allyPlayers.ValueKind == JsonValueKind.Array
                ? allyPlayers.EnumerateArray().ToList()
                : [];

        var ownTeam = state == "PREGAME" ? "ally"
            : entries.FirstOrDefault(p => Str(p, "Subject") == selfPuuid)
                is { ValueKind: JsonValueKind.Object } me ? Str(me, "TeamID") : "";

        var players = new List<BoardPlayer>();
        foreach (var entry in entries)
        {
            var puuid = Str(entry, "Subject");
            if (puuid.Length == 0) continue;

            var identity = entry.TryGetProperty("PlayerIdentity", out var id) ? id : (JsonElement?)null;
            var mmr = mmrByPuuid.TryGetValue(puuid, out var m) ? m : (JsonElement?)null;

            var flags = PrivacyFlags.FromIdentity(identity).Merge(PrivacyFlags.FromMmr(mmr));
            var seasons = MmrView.Seasonal(mmr);
            var agent = content.AgentName(Str(entry, "CharacterID"));

            // Incognito players are never in `names` - they were never asked
            // about. Passing the dictionary here cannot leak one.
            var record = names.TryGetValue(puuid, out var n) ? n : ((string, string)?)null;

            // Pregame entries carry no TeamID: everyone in that payload is
            // on your side by definition.
            var team = Str(entry, "TeamID");
            if (team.Length == 0 && state == "PREGAME") team = "ally";
            players.Add(new BoardPlayer(
                Puuid: puuid,
                Team: team,
                Side: ownTeam.Length > 0 && team == ownTeam ? "ally" : "enemy",
                Name: Privacy.BuildDisplayName(flags, record, agent, puuid),
                AgentName: agent,
                Flags: flags,
                Rank: MmrView.CurrentRank(mmr, seasons, currentActId),
                // Peak comes from the act rank badge, so a player who hid
                // theirs has none to show.
                Peak: flags.ActRankHidden ? null : MmrView.Peak(seasons),
                Level: flags.HideAccountLevel || identity is not { } ident
                    ? null : IntOrNull(ident, "AccountLevel"),
                IsSelf: puuid == selfPuuid));
        }

        players.Sort((a, b) =>
        {
            var byPoints = (b.RatingPoints ?? -1).CompareTo(a.RatingPoints ?? -1);
            return byPoints != 0
                ? byPoints
                : string.Compare(a.Name.Display, b.Name.Display, StringComparison.OrdinalIgnoreCase);
        });

        var ally = players.Where(p => p.Side == "ally").ToList();
        var enemy = players.Where(p => p.Side == "enemy").ToList();
        var twoTeams = ally.Count <= MaxTeamSize && enemy.Count <= MaxTeamSize;

        var sides = new List<BoardSide>
        {
            // Free-for-all modes put the whole lobby on one team id, so
            // everyone lands on our side with nobody opposite. They are not
            // team mates - in Deathmatch every one of them is shooting at you.
            // Agent select is the other way round: the enemy exists, we just
            // cannot see them yet, so they stay "your team".
            new("ally", enemy.Count > 0 || state == "PREGAME"
                ? "Your team" : "Everyone in this match", ally),
            new("enemy", twoTeams ? "Enemy team" : "Everyone else", enemy),
        };

        var matchmaking = match.TryGetProperty("MatchmakingData", out var mm)
                          && mm.ValueKind == JsonValueKind.Object ? mm : (JsonElement?)null;
        var queue = matchmaking is { } data ? Str(data, "QueueID") : Str(match, "QueueID");
        var modeId = Str(match, "ModeID");
        var mode = modeId.Length > 0 ? modeId : Str(match, "Mode");
        var flow = Str(match, "ProvisioningFlow");
        var provisioning = flow.Length > 0 ? flow : Str(match, "ProvisioningFlowID");

        return new Board(
            State: state,
            MapName: content.MapName(Str(match, "MapID")) ?? "Unknown map",
            Label: Modes.Label(queue, mode, provisioning),
            IsRanked: queue == "competitive",
            Sides: sides);
    }
}
