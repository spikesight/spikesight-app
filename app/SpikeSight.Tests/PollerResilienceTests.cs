using SpikeSight.Core.Game;
using SpikeSight.Core.Riot;

namespace SpikeSight.Tests;

/// <summary>
/// The loop must survive everything except being told to stop.
///
/// It did not. HttpClient reports a request timeout as a TaskCanceledException,
/// which is an OperationCanceledException, and catching that broadly made one
/// slow request end the loop for good - the window kept showing the lobby it
/// last knew, with no crash and nothing in the log. A stale board that looks
/// live is worse than one that admits it broke.
/// </summary>
public class PollerResilienceTests
{
    [Fact]
    public void A_request_timeout_is_not_a_cancellation()
    {
        // The exact type HttpClient throws when its timeout elapses.
        var timeout = new TaskCanceledException("The request was canceled due to timeout.");

        Assert.IsAssignableFrom<OperationCanceledException>(timeout);
    }

    [Fact]
    public async Task A_cancelled_token_stops_the_loop()
    {
        using var local = new LocalClient();
        var poller = new MatchPoller(local);
        using var stopping = new CancellationTokenSource();

        var running = poller.RunAsync(stopping.Token);
        await stopping.CancelAsync();

        // Returns rather than faulting: shutdown is the one reason to stop.
        await running.WaitAsync(TimeSpan.FromSeconds(5));
        Assert.True(running.IsCompletedSuccessfully);
    }

    [Fact]
    public async Task Nothing_else_stops_it()
    {
        // No Riot Client here, so every tick fails. The loop must keep going
        // regardless, because the client coming back is the normal case.
        using var local = new LocalClient();
        var poller = new MatchPoller(local)
        {
            Heartbeat = TimeSpan.FromMilliseconds(20),
            IdleDelay = TimeSpan.FromMilliseconds(20),
        };
        using var stopping = new CancellationTokenSource();

        var running = poller.RunAsync(stopping.Token);
        await Task.Delay(400);

        Assert.False(running.IsCompleted, "the loop gave up on a failing tick");
        Assert.False(running.IsFaulted);

        await stopping.CancelAsync();
        await running.WaitAsync(TimeSpan.FromSeconds(5));
    }
}
