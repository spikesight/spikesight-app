using System.Text;
using System.Text.Json;

namespace SpikeSight.Core.Riot;

/// <summary>
/// One player's published presence. The interesting half arrives as a
/// base64 JSON blob in <c>private</c>.
/// </summary>
public sealed record Presence(string Puuid, string GameName, string GameTag, JsonElement? Private)
{
    public static Presence From(JsonElement entry)
    {
        static string Str(JsonElement e, string name)
            => e.TryGetProperty(name, out var v) ? v.GetString() ?? "" : "";

        return new Presence(
            Str(entry, "puuid"), Str(entry, "game_name"), Str(entry, "game_tag"),
            Decode(Str(entry, "private")));
    }

    private static JsonElement? Decode(string blob)
    {
        if (string.IsNullOrEmpty(blob)) return null;
        try
        {
            var json = Encoding.UTF8.GetString(Convert.FromBase64String(blob));
            using var document = JsonDocument.Parse(json);
            return document.RootElement.Clone();
        }
        catch (Exception ex) when (ex is FormatException or JsonException or DecoderFallbackException)
        {
            return null;
        }
    }

    /// <summary>
    /// Read a field that moved into a sub-object in newer clients.
    ///
    /// The blob used to be flat (<c>sessionLoopState</c> at the top level) and
    /// is now grouped under <c>matchPresenceData</c>. Both shapes are accepted
    /// so a client update cannot quietly break state detection.
    /// </summary>
    private string Nested(string group, string key)
    {
        if (Private is not { } root || root.ValueKind != JsonValueKind.Object) return "";

        if (root.TryGetProperty(group, out var section)
            && section.ValueKind == JsonValueKind.Object
            && section.TryGetProperty(key, out var grouped)
            && grouped.ValueKind is not (JsonValueKind.Null or JsonValueKind.Undefined))
        {
            return Text(grouped);
        }

        return root.TryGetProperty(key, out var flat) ? Text(flat) : "";
    }

    private static string Text(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => value.GetString() ?? "",
        JsonValueKind.Null or JsonValueKind.Undefined => "",
        _ => value.ToString(),
    };

    public string SessionLoopState => Nested("matchPresenceData", "sessionLoopState").ToUpperInvariant();
    public string QueueId => Nested("matchPresenceData", "queueId");
    public string PartyId => Nested("partyPresenceData", "partyId");

    public int PartySize
        => int.TryParse(Nested("partyPresenceData", "partySize"), out var n) ? n : 0;
}
