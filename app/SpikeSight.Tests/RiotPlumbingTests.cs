using System.Text;
using System.Text.Json;
using SpikeSight.Core;
using SpikeSight.Core.Riot;

namespace SpikeSight.Tests;

public class LockfileTests
{
    [Fact]
    public void Parses_the_ordinary_shape()
    {
        var lockfile = Lockfile.Parse("Riot Client:6824:56396:s3cr3t:https");

        Assert.Equal("Riot Client", lockfile.Name);
        Assert.Equal(6824, lockfile.Pid);
        Assert.Equal(56396, lockfile.Port);
        Assert.Equal("s3cr3t", lockfile.Password);
        Assert.Equal("https://127.0.0.1:56396", lockfile.BaseUrl);
    }

    [Fact]
    public void Reads_from_the_right_because_the_name_can_contain_a_colon()
    {
        var lockfile = Lockfile.Parse("Riot:Client:Beta:6824:56396:s3cr3t:https");

        Assert.Equal("Riot:Client:Beta", lockfile.Name);
        Assert.Equal(56396, lockfile.Port);
        Assert.Equal("s3cr3t", lockfile.Password);
    }

    [Theory]
    [InlineData("too:few:fields")]
    [InlineData("Riot Client:notapid:56396:s3cr3t:https")]
    [InlineData("")]
    public void Refuses_a_half_written_file(string raw)
    {
        // The client rewrites this on launch, so a torn read is normal and
        // must read as "not running", never as a crash.
        Assert.Throws<ClientNotRunningException>(() => Lockfile.Parse(raw));
    }
}

public class EndpointsTests
{
    [Fact]
    public void Builds_the_host_names()
    {
        var endpoints = new Endpoints("na", "na", "release-13.08-shipping-14-5627607");

        Assert.Equal("https://pd.na.a.pvp.net", endpoints.Pd);
        Assert.Equal("https://glz-na-1.na.a.pvp.net", endpoints.Glz);
        Assert.Equal("https://shared.na.a.pvp.net", endpoints.Shared);
    }

    [Fact]
    public void Takes_the_last_build_in_the_log_not_the_first()
    {
        // A log spans a patch when the game updates mid-session. Sending the
        // older build gets every request refused the morning after a patch.
        const string log = """
            LogShooter: release-13.06-shipping-13-5435758 starting
            ... hours of play ...
            LogShooter: release-13.08-shipping-14-5627607 starting
            """;

        Assert.Equal("release-13.08-shipping-14-5627607", Endpoints.VersionIn(log));
    }

    [Fact]
    public void Says_nothing_when_the_log_has_no_build_in_it()
        => Assert.Null(Endpoints.VersionIn("nothing of interest here"));

    [Fact]
    public void Reads_region_and_shard_from_the_host_the_game_used()
    {
        var found = Endpoints.RegionShardIn("connecting to glz-eu-1.eu.a.pvp.net/core-game");

        Assert.Equal(("eu", "eu"), found);
    }

    [Fact]
    public void Handles_a_region_that_shares_another_shard()
    {
        var found = Endpoints.RegionShardIn("glz-latam-1.na.a.pvp.net");

        Assert.Equal(("latam", "na"), found);
    }

    [Fact]
    public void Platform_header_is_the_constant_riot_expects()
    {
        var decoded = Encoding.UTF8.GetString(Convert.FromBase64String(Endpoints.ClientPlatform));

        Assert.Equal(Endpoints.PlatformJson, decoded);
        Assert.Contains("\"platformType\": \"PC\"", decoded);
    }
}

public class PresenceTests
{
    private static Presence WithPrivate(string json)
    {
        var blob = Convert.ToBase64String(Encoding.UTF8.GetBytes(json));
        var entry = JsonDocument.Parse($$"""
            {"puuid":"p1","game_name":"","game_tag":"","private":"{{blob}}"}
            """).RootElement;
        return Presence.From(entry);
    }

    [Fact]
    public void Reads_the_grouped_shape_current_clients_send()
    {
        var presence = WithPrivate("""
            {"matchPresenceData":{"sessionLoopState":"INGAME","queueId":"competitive"},
             "partyPresenceData":{"partySize":5,"partyId":"abc"}}
            """);

        Assert.Equal("INGAME", presence.SessionLoopState);
        Assert.Equal("competitive", presence.QueueId);
        Assert.Equal(5, presence.PartySize);
        Assert.Equal("abc", presence.PartyId);
    }

    [Fact]
    public void Still_reads_the_old_flat_shape()
    {
        // Both are accepted so a client update cannot quietly break state
        // detection - which is the one thing the whole app hangs off.
        var presence = WithPrivate("""{"sessionLoopState":"PREGAME","partySize":2}""");

        Assert.Equal("PREGAME", presence.SessionLoopState);
        Assert.Equal(2, presence.PartySize);
    }

    [Fact]
    public void A_blob_that_will_not_decode_is_simply_empty()
    {
        var entry = JsonDocument.Parse("""
            {"puuid":"p1","game_name":"","game_tag":"","private":"not base64 !!"}
            """).RootElement;

        var presence = Presence.From(entry);

        Assert.Null(presence.Private);
        Assert.Equal("", presence.SessionLoopState);
        Assert.Equal(0, presence.PartySize);
    }

    [Fact]
    public void A_party_size_that_is_not_a_number_does_not_throw()
    {
        var presence = WithPrivate("""{"partyPresenceData":{"partySize":"lots"}}""");

        Assert.Equal(0, presence.PartySize);
    }
}
