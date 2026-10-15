using SpikeSight.Core.Game;

namespace SpikeSight.Tests;

/// <summary>
/// The rules that decide when the app is allowed to touch Riot. Local
/// presence is free and is the heartbeat; everything else costs a request,
/// which is why a scoreboard app has any business being polite at all.
/// </summary>
public class PollPlanTests
{
    [Fact]
    public void Menus_is_announced_once_and_then_left_alone()
    {
        var plan = new PollPlan();

        Assert.Equal(PollAction.Menus, plan.Decide("MENUS", 0));
        plan.BackToMenus();

        // Sitting in menus must not keep republishing or keep asking.
        Assert.Equal(PollAction.Wait, plan.Decide("MENUS", 2));
        Assert.Equal(PollAction.Wait, plan.Decide("MENUS", 600));
    }

    [Fact]
    public void Walking_into_agent_select_triggers_one_scan()
    {
        var plan = new PollPlan();
        plan.BackToMenus();

        Assert.Equal(PollAction.Rescan, plan.Decide("PREGAME", 10));
        plan.Observed("PREGAME", "match-1", 10);

        // Same match, moments later: nothing new to learn yet.
        Assert.Equal(PollAction.Wait, plan.Decide("PREGAME", 12));
    }

    [Fact]
    public void Agent_select_is_re_read_while_people_lock_in()
    {
        var plan = new PollPlan { PregameRescanSeconds = 5 };
        plan.Observed("PREGAME", "match-1", 10);

        Assert.Equal(PollAction.Wait, plan.Decide("PREGAME", 14));
        Assert.Equal(PollAction.Rescan, plan.Decide("PREGAME", 15));
    }

    [Fact]
    public void A_live_match_roster_is_never_re_read()
    {
        // It cannot change, so asking again is pure waste.
        var plan = new PollPlan();
        plan.Observed("INGAME", "match-1", 0);

        foreach (var second in new double[] { 5, 30, 300, 3000 })
            Assert.Equal(PollAction.Wait, plan.Decide("INGAME", second));
    }

    [Fact]
    public void Agent_select_turning_into_a_match_is_a_scan()
    {
        var plan = new PollPlan();
        plan.Observed("PREGAME", "match-1", 0);

        Assert.Equal(PollAction.Rescan, plan.Decide("INGAME", 1));
    }

    [Fact]
    public void No_presence_falls_back_to_a_slow_probe()
    {
        // The game not running, or presence not published. Going blind is
        // worse than probing, but probing is the expensive path.
        var plan = new PollPlan { BlindDetectSeconds = 12 };

        Assert.Equal(PollAction.Rescan, plan.Decide(null, 0));
        Assert.Equal(PollAction.Wait, plan.Decide(null, 5));
        Assert.Equal(PollAction.Wait, plan.Decide(null, 11));
        Assert.Equal(PollAction.Rescan, plan.Decide(null, 12));
    }

    [Fact]
    public void A_new_match_is_reported_as_fresh()
    {
        var plan = new PollPlan();

        Assert.True(plan.Observed("PREGAME", "match-1", 0));
        Assert.False(plan.Observed("INGAME", "match-1", 1));
        Assert.True(plan.Observed("INGAME", "match-2", 2));
    }
}

public class ActTests
{
    private static Acts Parse(string json)
    {
        using var document = System.Text.Json.JsonDocument.Parse(json);
        return Acts.FromSeasons(document.RootElement.Clone());
    }

    [Fact]
    public void Carries_the_episode_name_down_onto_its_acts()
    {
        // Seasons arrive newest first, each episode followed by its own acts.
        var acts = Parse("""
            [{"Type":"episode","Name":"V26","ID":"e1"},
             {"Type":"act","Name":"ACT V","ID":"a5","IsActive":true,
              "StartTime":"2026-08-18T00:00:00Z"},
             {"Type":"act","Name":"ACT IV","ID":"a4",
              "StartTime":"2026-06-23T00:00:00Z"}]
            """);

        Assert.Equal(2, acts.All.Count);
        Assert.Equal("V26A5", acts.Current!.Short);
        Assert.Equal("V26 // ACT V", acts.Current.Full);
    }

    [Fact]
    public void Still_understands_the_old_episode_numbering()
    {
        var acts = Parse("""
            [{"Type":"episode","Name":"EPISODE 7","ID":"e7"},
             {"Type":"act","Name":"ACT II","ID":"a2","IsActive":true}]
            """);

        Assert.Equal("E7A2", acts.Current!.Short);
    }

    [Fact]
    public void Without_an_active_flag_it_takes_the_newest_act_that_has_started()
    {
        // A stale cache has no IsActive anywhere, and guessing beats blank.
        var acts = Parse("""
            [{"Type":"act","Name":"ACT II","ID":"a2","StartTime":"2026-06-01T00:00:00Z"},
             {"Type":"act","Name":"ACT I","ID":"a1","StartTime":"2026-03-01T00:00:00Z"},
             {"Type":"act","Name":"ACT III","ID":"a3","StartTime":"2099-01-01T00:00:00Z"}]
            """);

        Assert.Equal("a2", acts.Current!.Id);
    }

    [Fact]
    public void An_empty_or_broken_payload_is_simply_no_acts()
    {
        Assert.Null(Parse("[]").Current);
        Assert.Null(Parse("""{"not":"an array"}""").Current);
        Assert.Null(Acts.Empty.Current);
    }
}
