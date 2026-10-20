using SpikeSight.Core.Game;

namespace SpikeSight.Tests;

/// <summary>
/// The agent and map tables are embedded resources, and an embedded resource
/// that is not there fails silently: the app starts, talks to Riot, and shows
/// a lobby of blanks. The old build had a CI step for exactly this after a
/// missing data file reached a tagged release. This is that step, as a test,
/// so it runs on a desk as well as on a runner.
/// </summary>
public class BundledContentTests
{
    [Fact]
    public void Agent_and_map_tables_are_embedded()
    {
        Assert.True(GameContent.Bundled.AgentCount > 20,
                    $"only {GameContent.Bundled.AgentCount} agents embedded");
        Assert.True(GameContent.Bundled.MapCount > 5,
                    $"only {GameContent.Bundled.MapCount} maps embedded");
    }

    [Fact]
    public void Ids_resolve_to_names()
    {
        Assert.Equal("Jett", GameContent.Bundled.AgentName("add6443a-41bd-e414-f6ad-e58d267f4e95"));
        Assert.Equal("Lotus", GameContent.Bundled.MapName("/Game/Maps/Jam/Jam"));
    }
}
