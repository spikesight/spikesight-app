using SpikeSight.Core.Store;

namespace SpikeSight.Core.Game;

/// <summary>
/// A lobby, a history and an encounter list made up out of thin air.
///
/// For screenshots, and for looking around without the Riot Client running.
/// Every name here is invented and every id is obviously fake, which is the
/// point: the alternative is publishing a picture of ten real players' ranks
/// and recent form because the app had no other way to show what it looks
/// like.
///
/// Deterministic on purpose. A screenshot retaken next year should be the
/// same screenshot.
/// </summary>
public static class Demo
{
    /// <summary>Tier numbers read the way the table does: Silver 1 is 9.</summary>
    private const int Bronze1 = 6, Bronze3 = 8, Silver1 = 9, Silver2 = 10, Silver3 = 11;
    private const int Gold1 = 12, Gold3 = 14, Platinum2 = 16, Diamond1 = 18;

    public static string SelfPuuid => Id(3);

    public static Board Lobby()
    {
        var ally = new List<BoardPlayer>
        {
            Player(1, "Sova", "ally", "quietkettle", "dusk", Gold1, peak: Gold3,
                   level: 214, matches: 5, kills: 95, deaths: 80, acs: 218, ps: 250),
            Player(2, "Killjoy", "ally", "pepperoniroll", "void", Silver2, peak: Gold1,
                   level: 88, matches: 5, kills: 112, deaths: 79, acs: 256, ps: 310,
                   party: new PartyAssignment(1, 2, "likely", "queued together recently")),
            Self(3, "Sage", Silver1, rr: 87, peak: Silver3,
                 level: 143, matches: 5, kills: 83, deaths: 80, acs: 212, ps: 241),
            Player(4, "Phoenix", "ally", "tinroofcat", "mew", Silver1, peak: Silver2,
                   level: 30, matches: 5, kills: 98, deaths: 80, acs: 267, ps: 278,
                   smurf: Smurfy(62)),
            Player(5, "Omen", "ally", "halfvolley", "slow", Bronze1, peak: Bronze1,
                   level: 37, matches: 5, kills: 68, deaths: 80, acs: 200, ps: 189,
                   party: new PartyAssignment(1, 2, "likely", "queued together recently")),
        };

        var enemy = new List<BoardPlayer>
        {
            // Two hidden players, because a screenshot that never shows one
            // suggests the app does not handle them.
            Hidden(6, "Jett", "enemy", Silver2, matches: 5,
                   kills: 101, deaths: 100, acs: 200, ps: 222),
            Player(7, "Breach", "enemy", "ninelives", "bang", Silver2, peak: Silver3,
                   level: 151, matches: 5, kills: 97, deaths: 100, acs: 228, ps: 278,
                   note: Severity.Avoid, met: new EncounterSummary(3, 1, 2, null)),
            Hidden(8, "Clove", "enemy", Silver1, matches: 5,
                   kills: 89, deaths: 100, acs: 229, ps: 300),
            Player(9, "Raze", "enemy", "sodafountain", "fizz", Bronze3, peak: Silver2,
                   level: 62, matches: 5, kills: 47, deaths: 100, acs: 115, ps: 114),
            Player(10, "Viper", "enemy", "coldbrewonly", "2am", Platinum2, peak: Diamond1,
                   level: 402, matches: 5, kills: 127, deaths: 100, acs: 260, ps: 355,
                   smurf: Smurfy(71), note: Severity.Dodge),
        };

        return new Board(
            State: "PREGAME",
            MapName: "Lotus",
            Label: "Competitive",
            IsRanked: true,
            Sides:
            [
                new BoardSide("ally", "Your team", ally),
                new BoardSide("enemy", "Enemy team", enemy),
            ],
            WinProbability: new WinProbability(
                0.47, 47, "rank-weighted", 512, 574,
                "Your team", "Enemy team", 10, 10,
                "Rank-weighted estimate from 10/10 visible ranks."));
    }

    /// <summary>Your best agents on this map, for the overlay panel.</summary>
    public static (string Heading, IReadOnlyList<AgentRecord> Best) Pool() =>
        ("Lotus  ·  7-4  ·  your best here",
         [
             new AgentRecord(CharacterId("Sage"), 5, 4, 1, 1.21, 241),
             new AgentRecord(CharacterId("Killjoy"), 4, 2, 2, 1.04, 218),
             new AgentRecord(CharacterId("Omen"), 2, 1, 1, 0.98, 203),
         ]);

    public static Career History()
    {
        var matches = new List<CareerMatch>
        {
            Match("Lotus", "Competitive", "Sage", true, 13, 9, 21, 14, 6, 248, 268, 19, 87, Silver1),
            Match("Ascent", "Competitive", "Sage", false, 10, 13, 15, 17, 8, 196, 203, -16, 68, Silver1),
            Match("Sunset", "Competitive", "Killjoy", true, 13, 7, 19, 11, 4, 263, 281, 21, 84, Silver1),
            Match("Bind", "Swiftplay", "Omen", true, 5, 3, 11, 8, 7, 219, 230, null, null, 0),
            Match("Haven", "Competitive", "Sage", false, 11, 13, 16, 18, 9, 204, 215, -14, 63, Silver1),
            Match("Icebox", "Competitive", "Killjoy", true, 13, 11, 23, 16, 5, 271, 294, 18, 77, Silver1),
            Match("Split", "Unrated", "Phoenix", false, 8, 13, 12, 17, 3, 171, 178, null, null, 0),
            Match("Lotus", "Competitive", "Sage", true, 13, 4, 24, 9, 8, 301, 322, 23, 59, Silver1),
        };

        return new Career(
            Summary: new CareerSummary(
                Matches: matches.Count, Wins: 5, Losses: 3, Winrate: 0.625,
                Kd: 1.28, Acs: 234, Performance: 249, NetRr: 51, RankedMatches: 6),
            Matches: matches,
            Current: new RankView(Silver1, 87),
            Peak: (Silver3, ""));
    }

    /// <summary>
    /// Fill a notes database so the encounters view has something in it.
    ///
    /// Safe to call twice: an encounter is keyed on match id, and a note is
    /// only added to a player who has none.
    /// </summary>
    public static async Task SeedAsync(NotesStore notes, CancellationToken ct = default)
    {
        foreach (var person in People)
        {
            if (person.Name is { Length: > 0 })
                await notes.RememberNameAsync(person.Puuid, person.Name, person.Tag, ct);

            for (var i = 0; i < person.Met; i++)
            {
                await notes.RecordEncounterAsync(
                    person.Puuid, $"demo-{person.Agent}-{i}".ToLowerInvariant(),
                    i < person.Ally ? "ally" : "enemy",
                    person.Agent, person.Map, "Competitive", ct);
            }

            if (person.Note is { } severity
                && (await notes.NotesForAsync([person.Puuid], ct))
                   .GetValueOrDefault(person.Puuid) is not { Count: > 0 })
            {
                await notes.AddNoteAsync(person.Puuid, severity, person.Why, person.Tags, ct);
            }
        }
    }

    private sealed record Person(
        string Puuid, string? Name, string? Tag, int Met, int Ally,
        string Agent, string Map, Severity? Note = null, string Why = "")
    {
        public IReadOnlyList<string> Tags { get; init; } = [];
    }

    private static readonly Person[] People =
    [
        new(Id(7), "ninelives", "bang", 3, 1, "Breach", "Sunset",
            Severity.Avoid, "Went afk in overtime twice.") { Tags = ["afk"] },
        new(Id(10), "coldbrewonly", "2am", 5, 2, "Viper", "Icebox",
            Severity.Dodge, "Abusive in voice from round one.") { Tags = ["toxic", "voice"] },
        new(Id(2), "pepperoniroll", "void", 4, 4, "Killjoy", "Lotus",
            Severity.Watch, "Good site anchor - queue with them again.") { Tags = ["good"] },
        new(Id(9), "sodafountain", "fizz", 2, 0, "Raze", "Bind"),
        new(Id(4), "tinroofcat", "mew", 1, 1, "Phoenix", "Ascent"),
        // Never nameable: they were in Streamer Mode every time we met.
        new(Id(6), null, null, 2, 0, "Jett", "Haven"),
    ];

    private static string Id(int n) => $"00000000-0000-4000-8000-{n:D12}";

    /// <summary>
    /// Real character ids, because the portraits are keyed on them.
    ///
    /// The agent names in this file are decoration; this is the part the UI
    /// actually looks up, and passing a name here is what made every row in
    /// the first demo screenshot a grey circle.
    /// </summary>
    private static readonly Dictionary<string, string> Characters = new()
    {
        ["Sova"] = "320b2a48-4d9b-a075-30f1-1f93a9b638fa",
        ["Killjoy"] = "1e58de9c-4950-5125-93e9-a0aee9f98746",
        ["Sage"] = "569fdd95-4d10-43ab-ca70-79becc718b46",
        ["Phoenix"] = "eb93336a-449b-9c1b-0a54-a891f7921d69",
        ["Omen"] = "8e253930-4c05-31dd-1b6c-968525494517",
        ["Jett"] = "add6443a-41bd-e414-f6ad-e58d267f4e95",
        ["Breach"] = "5f8d3a7f-467b-97f3-062c-13acf203c006",
        ["Clove"] = "1dbf2edd-4729-0984-3115-daa5eed44993",
        ["Raze"] = "f94c3b30-42be-e959-889c-5aa313dba261",
        ["Viper"] = "707eab51-4836-f488-046a-cda6bf494859",
    };

    private static string CharacterId(string agent)
        => Characters.TryGetValue(agent, out var id) ? id : agent;

    private static BoardPlayer Player(
        int n, string agent, string side, string name, string tag, int tier,
        int peak, int? level, int matches, int kills, int deaths, int acs, int ps,
        SmurfVerdict? smurf = null, PartyAssignment? party = null,
        Severity? note = null, EncounterSummary? met = null)
        => new(
            Puuid: Id(n),
            Team: side == "ally" ? "Blue" : "Red",
            Side: side,
            Name: new DisplayName($"{name}#{tag}", name, tag),
            AgentName: agent,
            CharacterId: CharacterId(agent),
            Flags: new PrivacyFlags(),
            Rank: new RankView(tier),
            Peak: (peak, ""),
            Level: level,
            IsSelf: false,
            Smurf: smurf,
            Stats: Stats(Id(n), matches, kills, deaths, acs, ps),
            Party: party,
            Notes: note is { } severity
                ? [new PlayerNote(n, Id(n), severity, "", [], Then, Then)]
                : null,
            Encounters: met);

    private static BoardPlayer Self(
        int n, string agent, int tier, int rr, int peak, int level,
        int matches, int kills, int deaths, int acs, int ps)
        => Player(n, agent, "ally", "you", "demo", tier, peak, level,
                  matches, kills, deaths, acs, ps) with
        {
            Rank = new RankView(tier, rr),
            IsSelf = true,
        };

    private static BoardPlayer Hidden(
        int n, string agent, string side, int tier,
        int matches, int kills, int deaths, int acs, int ps)
        => Player(n, agent, side, "hidden", "x", tier, tier, level: null,
                  matches, kills, deaths, acs, ps) with
        {
            // Exactly what the real path produces for Streamer Mode: the name
            // was never requested, so there is none to show.
            Flags = new PrivacyFlags(Incognito: true, HideAccountLevel: true),
            Name = new DisplayName(Privacy.HiddenName, Hidden: true,
                                   Reason: "Streamer Mode",
                                   HiddenId: Privacy.HiddenTag(Id(n))),
            Peak = null,
        };

    private static PlayerStats Stats(
        string puuid, int matches, int kills, int deaths, int acs, int ps)
    {
        var rounds = matches * 20;
        return new PlayerStats(puuid)
        {
            Matches = matches,
            Kills = kills,
            Deaths = deaths,
            Assists = kills / 2,
            Rounds = rounds,
            Score = acs * rounds,
            Wins = matches / 2,
            Losses = matches - matches / 2,
            PerformanceTotal = ps * (double)matches,
            PerformanceMatches = matches,
        };
    }

    private static SmurfVerdict Smurfy(int score) => new(
        score, Flagged: true,
        [
            new Signal("Account level", 24, "low for the rank"),
            new Signal("Recent K/D", 22, "well above the lobby"),
            new Signal("Win rate", 16, "winning most games"),
        ],
        LevelHidden: false);

    private static CareerMatch Match(
        string map, string queue, string agent, bool won, int roundsWon, int roundsLost,
        int kills, int deaths, int assists, int acs, int ps,
        int? rr, int? rrAfter, int tierAfter)
        => new(
            MatchId: $"demo-{map}-{queue}".ToLowerInvariant(),
            StartedMs: 0,
            Map: map,
            Queue: queue,
            Agent: agent,
            CharacterId: CharacterId(agent),
            Won: won,
            RoundsWon: roundsWon,
            RoundsLost: roundsLost,
            Kills: kills,
            Deaths: deaths,
            Assists: assists,
            Kd: Math.Round(kills / (double)deaths, 2),
            Acs: acs,
            Performance: ps,
            RrEarned: rr,
            RrAfter: rrAfter,
            TierAfter: tierAfter,
            StatsMissing: false);

    private static readonly DateTimeOffset Then = DateTimeOffset.UnixEpoch.AddYears(55);
}
