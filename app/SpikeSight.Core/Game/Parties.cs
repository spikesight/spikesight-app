namespace SpikeSight.Core.Game;

public sealed record PartyAssignment(int Group, int Size, string Confidence, string Evidence);

/// <summary>
/// Premade detection, by two independent methods in order of trust.
///
/// <para><b>Confirmed</b> - party ids from the local presence feed. The client
/// publishes a party id for you and your friends, so your own stack is known
/// for certain and costs nothing. Riot will not answer
/// <c>/parties/v1/players/{puuid}</c> about anyone outside your party, so it
/// is only ever asked about yourself.</para>
///
/// <para><b>Likely</b> - inferred from match history already fetched for the
/// form column. A group actually observed partied together is strongest; a
/// pair who keep turning up in the same party is weaker but real.</para>
///
/// Groups larger than two are only reported when that exact set has every
/// member partied with every other member. Chaining pairs - A duos with B, B
/// duos with C, therefore a trio - invents premades that do not exist, so it
/// is deliberately not done.
///
/// Parties never span teams, so grouping always happens within one.
/// </summary>
public static class Parties
{
    /// <summary>Shared recent parties before two players count as premade.</summary>
    public const int DefaultMinShared = 2;

    /// <summary>
    /// Above this many on a side, stop hunting for the largest party. Only
    /// Deathmatch gets near it and premades there are not worth the work -
    /// the search is combinatorial.
    /// </summary>
    private const int MaxPartySearch = 10;

    public static Dictionary<string, PartyAssignment> Detect(
        IReadOnlyDictionary<string, List<string>> teams,
        IReadOnlyDictionary<string, string> partyIds,
        IReadOnlyDictionary<(string, string), int>? coParty = null,
        IReadOnlyDictionary<string, int>? coParties = null,
        int minShared = DefaultMinShared)
    {
        coParty ??= new Dictionary<(string, string), int>();
        coParties ??= new Dictionary<string, int>();

        var assignments = new Dictionary<string, PartyAssignment>();
        var nextGroup = 1;

        foreach (var (_, members) in teams.OrderBy(t => t.Key, StringComparer.Ordinal))
        {
            var claimed = new HashSet<string>();

            // --- confirmed: live party ids ---------------------------------
            var byParty = new Dictionary<string, List<string>>();
            foreach (var puuid in members)
            {
                if (!partyIds.TryGetValue(puuid, out var id) || id.Length == 0) continue;
                if (!byParty.TryGetValue(id, out var group)) byParty[id] = group = [];
                group.Add(puuid);
            }

            foreach (var (_, group) in byParty.OrderBy(g => g.Key, StringComparer.Ordinal))
            {
                if (group.Count < 2) continue;
                foreach (var puuid in group)
                {
                    assignments[puuid] = new PartyAssignment(
                        nextGroup, group.Count, "confirmed", "Shared live party id");
                    claimed.Add(puuid);
                }
                nextGroup++;
            }

            // --- likely: inferred from recent matches -----------------------
            foreach (var group in Inferred(members, coParty, claimed, minShared))
            {
                var size = group.Count;
                var key = string.Join('|', group);
                var exact = coParties.GetValueOrDefault(key);

                var evidence = exact > 0
                    ? $"Seen together as a {size}-stack in {exact} recent match{(exact == 1 ? "" : "es")}"
                    : size > 2
                        ? $"All {size} have partied with each other recently"
                        : Pair(group, coParty);

                foreach (var puuid in group)
                {
                    assignments[puuid] = new PartyAssignment(nextGroup, size, "likely", evidence);
                    claimed.Add(puuid);
                }
                nextGroup++;
            }
        }

        return assignments;
    }

    private static string Pair(List<string> group, IReadOnlyDictionary<(string, string), int> coParty)
    {
        var shared = coParty.GetValueOrDefault((group[0], group[1]));
        return $"Partied together in {shared} recent match{(shared == 1 ? "" : "es")}";
    }

    /// <summary>
    /// Candidate parties, biggest first.
    ///
    /// A party is a set where every member has partied with every other
    /// member. Anything looser lets two unrelated duos who share a friend
    /// look like a trio.
    /// </summary>
    private static List<List<string>> Inferred(
        IEnumerable<string> members,
        IReadOnlyDictionary<(string, string), int> coParty,
        HashSet<string> claimed,
        int minShared)
    {
        var nodes = members.Where(p => !claimed.Contains(p))
                           .OrderBy(p => p, StringComparer.Ordinal).ToList();
        var found = new List<List<string>>();
        if (nodes.Count < 2) return found;

        var nodeSet = nodes.ToHashSet();
        var edges = new HashSet<(string, string)>();
        foreach (var ((a, b), count) in coParty)
        {
            if (count < minShared || !nodeSet.Contains(a) || !nodeSet.Contains(b)) continue;
            edges.Add(Edge(a, b));
        }
        if (edges.Count == 0) return found;

        var taken = new HashSet<string>();
        var largest = Math.Min(nodes.Count, MaxPartySearch);
        for (var size = largest; size >= 2; size--)
        {
            foreach (var candidate in Combinations(nodes, size))
            {
                if (candidate.Any(taken.Contains)) continue;
                if (!FullyConnected(candidate, edges)) continue;
                found.Add(candidate);
                foreach (var puuid in candidate) taken.Add(puuid);
            }
        }
        return found;
    }

    private static (string, string) Edge(string a, string b)
        => string.CompareOrdinal(a, b) <= 0 ? (a, b) : (b, a);

    private static bool FullyConnected(List<string> group, HashSet<(string, string)> edges)
    {
        for (var i = 0; i < group.Count; i++)
            for (var j = i + 1; j < group.Count; j++)
                if (!edges.Contains(Edge(group[i], group[j]))) return false;
        return true;
    }

    private static IEnumerable<List<string>> Combinations(List<string> source, int size)
    {
        if (size == 0) { yield return []; yield break; }
        for (var i = 0; i <= source.Count - size; i++)
        {
            var head = source[i];
            foreach (var tail in Combinations(source.GetRange(i + 1, source.Count - i - 1), size - 1))
            {
                tail.Insert(0, head);
                yield return tail;
            }
        }
    }
}
