using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace SpikeSight.Core.Game;

/// <summary>
/// Per-player privacy state, gathered from the match and MMR payloads.
///
/// Riot exposes several flags and SpikeSight honours all of them, before the
/// data reaches anything that renders - a hidden value is never handed to the
/// view at all, rather than hidden once it gets there.
///
/// <list type="bullet">
/// <item><c>Incognito</c> - Streamer Mode. The riot ID is never resolved,
/// never displayed and never written to the notes database. The one exception
/// is somebody in your own party, whose name the local client already gave
/// you, exactly as the game shows you your own party.</item>
/// <item><c>HideAccountLevel</c> - level is not shown and is left out of smurf
/// scoring. It is never reconstructed from match history, which would defeat
/// the setting.</item>
/// <item><c>IsActRankBadgeHidden</c> - peak and previous-act rank suppressed.</item>
/// <item><c>IsLeaderboardAnonymized</c> - placement suppressed.</item>
/// </list>
/// </summary>
public readonly record struct PrivacyFlags(
    bool Incognito = false,
    bool HideAccountLevel = false,
    bool ActRankHidden = false,
    bool LeaderboardAnonymized = false)
{
    public PrivacyFlags Merge(PrivacyFlags other) => new(
        Incognito || other.Incognito,
        HideAccountLevel || other.HideAccountLevel,
        ActRankHidden || other.ActRankHidden,
        LeaderboardAnonymized || other.LeaderboardAnonymized);

    public static PrivacyFlags FromIdentity(JsonElement? identity)
    {
        if (identity is not { ValueKind: JsonValueKind.Object } value) return new PrivacyFlags();
        return new PrivacyFlags(
            Incognito: Bool(value, "Incognito"),
            HideAccountLevel: Bool(value, "HideAccountLevel"));
    }

    public static PrivacyFlags FromMmr(JsonElement? mmr)
    {
        if (mmr is not { ValueKind: JsonValueKind.Object } value) return new PrivacyFlags();
        return new PrivacyFlags(
            ActRankHidden: Bool(value, "IsActRankBadgeHidden"),
            LeaderboardAnonymized: Bool(value, "IsLeaderboardAnonymized"));
    }

    private static bool Bool(JsonElement element, string name)
        => element.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.True;

    public IEnumerable<string> Reasons()
    {
        if (Incognito) yield return "Streamer Mode is on for this player";
        if (HideAccountLevel) yield return "Account level hidden by player";
        if (ActRankHidden) yield return "Act rank badge hidden by player";
        if (LeaderboardAnonymized) yield return "Leaderboard placement anonymized";
    }
}

/// <summary>What the UI is permitted to render for a player.</summary>
public sealed record DisplayName(
    string Display,
    string? GameName = null,
    string? TagLine = null,
    bool Hidden = false,
    string Reason = "",
    string? HiddenId = null);

public static class Privacy
{
    public const string HiddenName = "Hidden Player";

    // Crockford base32: no I, L, O or U, so a code is never misread and never
    // accidentally spells a word.
    private const string TagAlphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
    private const int TagLength = 4;

    /// <summary>
    /// A short, stable label for a player we are not allowed to name.
    ///
    /// Streamer Mode hides the riot ID, which left every hidden player looking
    /// identical - a wall of indistinguishable rows, and an agent name that
    /// says nothing about *which* Brimstone this is. Notes were always keyed
    /// on puuid and followed the right person; there was just no way to see
    /// that. This is the missing handle: derived from the puuid alone, the
    /// same every time you meet them, carrying nothing about who they are,
    /// and not reversible.
    /// </summary>
    /// <remarks>
    /// The Python build hashed with BLAKE2s, which .NET does not ship. SHA-256
    /// gives the same properties, so the codes a player saw before 3.0 will
    /// differ from the ones they see after - a one-time cosmetic change at a
    /// major version, and nothing is keyed on the code itself.
    /// </remarks>
    public static string HiddenTag(string puuid)
    {
        var digest = SHA256.HashData(Encoding.UTF8.GetBytes(puuid));
        var value = BitConverter.ToUInt64(digest.AsSpan(0, 8));
        // Big-endian read, to match how the Python version consumed its digest.
        if (BitConverter.IsLittleEndian) value = BinaryPrimitivesReverse(value);

        var tag = new char[TagLength];
        for (var i = 0; i < TagLength; i++)
        {
            tag[i] = TagAlphabet[(int)(value % (ulong)TagAlphabet.Length)];
            value /= (ulong)TagAlphabet.Length;
        }
        return new string(tag);
    }

    private static ulong BinaryPrimitivesReverse(ulong value)
        => System.Buffers.Binary.BinaryPrimitives.ReverseEndianness(value);

    /// <summary>
    /// The subset of a roster whose names we may look up.
    ///
    /// Incognito players are dropped here, which is what makes their riot ID
    /// never requested from Riot at all - not requested, not cached, not
    /// stored.
    /// </summary>
    public static List<string> ResolvablePuuids(IEnumerable<(string Puuid, PrivacyFlags Flags)> roster)
        => roster.Where(p => p.Puuid.Length > 0 && !p.Flags.Incognito)
                 .Select(p => p.Puuid).ToList();

    public static DisplayName BuildDisplayName(
        PrivacyFlags flags,
        (string GameName, string TagLine)? nameRecord,
        string? agentName,
        string? puuid = null,
        (string GameName, string TagLine)? partyName = null)
    {
        if (flags.Incognito)
        {
            // One exception, and only one: somebody in your own party, named
            // from the local client's own presence feed. Streamer Mode hides a
            // player from strangers - the game itself shows you your party, and
            // this name was never requested from Riot, it was already sitting
            // in the client. Nobody outside your party is ever named this way.
            if (partyName is { GameName.Length: > 0 } party)
            {
                return new DisplayName(
                    party.TagLine.Length > 0 ? $"{party.GameName}#{party.TagLine}" : party.GameName,
                    party.GameName, party.TagLine, Hidden: false,
                    Reason: "In your party. Hidden from everyone else by Streamer Mode.");
            }

            var code = puuid is { Length: > 0 } ? HiddenTag(puuid) : null;
            return new DisplayName(
                agentName is { Length: > 0 } ? agentName : HiddenName,
                Hidden: true,
                Reason: "Player has Streamer Mode enabled"
                        + (code is null ? "" : $". Tracked locally as {code}."),
                HiddenId: code);
        }

        if (nameRecord is not { GameName.Length: > 0 } record)
            return new DisplayName(agentName is { Length: > 0 } ? agentName : "Unknown");

        var display = record.TagLine.Length > 0
            ? $"{record.GameName}#{record.TagLine}"
            : record.GameName;
        return new DisplayName(display, record.GameName, record.TagLine);
    }

    /// <summary>
    /// Name and tag safe to keep in the local notes database.
    ///
    /// Nothing is stored for an incognito player: their note is keyed on puuid
    /// alone, so it still resurfaces next time without ever recording who they
    /// are.
    /// </summary>
    public static (string? GameName, string? TagLine) StorableAlias(PrivacyFlags flags, DisplayName name)
        => flags.Incognito || name.Hidden ? (null, null) : (name.GameName, name.TagLine);
}
