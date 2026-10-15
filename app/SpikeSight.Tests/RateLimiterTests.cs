using System.Diagnostics;
using SpikeSight.Core.Riot;

namespace SpikeSight.Tests;

/// <summary>
/// Riot publishes no rate limit for client traffic, so the policy is to stay
/// well under anything a real client would do. Scanning a ten-player lobby is
/// eleven requests; a dodge and a re-queue does it twice in a few seconds,
/// which is how the first build of this port earned a 429.
/// </summary>
public class RateLimiterTests
{
    [Fact]
    public async Task The_burst_goes_straight_through()
    {
        // A full bucket should not make the first lobby scan feel slow.
        using var limiter = new RateLimiter(ratePerSecond: 4, burst: 8, maxConcurrency: 3);
        var clock = Stopwatch.StartNew();

        for (var i = 0; i < 8; i++) (await limiter.SlotAsync()).Dispose();

        Assert.True(clock.ElapsedMilliseconds < 200, $"took {clock.ElapsedMilliseconds}ms");
        Assert.Equal(8, limiter.TotalRequests);
    }

    [Fact]
    public async Task Past_the_burst_it_slows_to_the_configured_rate()
    {
        using var limiter = new RateLimiter(ratePerSecond: 10, burst: 2, maxConcurrency: 3);
        var clock = Stopwatch.StartNew();

        // 2 free, then 4 more at 10/s = roughly 400ms.
        for (var i = 0; i < 6; i++) (await limiter.SlotAsync()).Dispose();

        Assert.InRange(clock.ElapsedMilliseconds, 250, 1500);
        Assert.Equal(6, limiter.TotalRequests);
    }

    [Fact]
    public async Task Only_so_many_are_ever_in_flight_at_once()
    {
        using var limiter = new RateLimiter(ratePerSecond: 1000, burst: 1000, maxConcurrency: 3);
        var live = 0;
        var peak = 0;
        var gate = new object();

        await Task.WhenAll(Enumerable.Range(0, 30).Select(async _ =>
        {
            using var slot = await limiter.SlotAsync();
            lock (gate) { live++; peak = Math.Max(peak, live); }
            await Task.Delay(15);
            lock (gate) { live--; }
        }));

        Assert.True(peak <= 3, $"peaked at {peak} in flight");
    }

    [Fact]
    public async Task A_slot_releases_once_even_if_disposed_twice()
    {
        using var limiter = new RateLimiter(ratePerSecond: 1000, burst: 1000, maxConcurrency: 1);
        var slot = await limiter.SlotAsync();
        slot.Dispose();
        slot.Dispose();

        // The single concurrency permit must not have been handed back twice,
        // or the cap silently stops meaning anything.
        using var second = await limiter.SlotAsync();
        var third = limiter.SlotAsync();
        Assert.False(third.IsCompleted);

        second.Dispose();
        (await third).Dispose();
    }

    [Fact]
    public async Task Cancelling_while_waiting_does_not_leak_a_permit()
    {
        using var limiter = new RateLimiter(ratePerSecond: 0.5, burst: 1, maxConcurrency: 1);
        (await limiter.SlotAsync()).Dispose();   // empties the bucket

        using var cancelling = new CancellationTokenSource(50);
        await Assert.ThrowsAnyAsync<OperationCanceledException>(
            () => limiter.SlotAsync(cancelling.Token));

        // The concurrency permit must have gone back, or everything after a
        // cancelled scan would hang forever.
        using var after = await limiter.SlotAsync();
        Assert.True(true);
    }
}
