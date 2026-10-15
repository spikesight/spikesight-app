using SpikeSight.Core.Riot;

namespace SpikeSight.Tests;

/// <summary>
/// Most of the politeness lives here, not in the limiter. Agent select is
/// re-read every few seconds while people lock in; without a cache each pass
/// refetches every player's MMR, which is how a Swiftplay earned a 429.
/// </summary>
public class TtlCacheTests
{
    [Fact]
    public async Task The_second_ask_does_not_fetch_again()
    {
        var cache = new TtlCache();
        var fetches = 0;

        for (var i = 0; i < 5; i++)
            await cache.GetOrFetchAsync<string>("mmr:p1", 90, () =>
            {
                fetches++;
                return Task.FromResult<string?>("payload");
            });

        Assert.Equal(1, fetches);
        Assert.Equal(4, cache.Hits);
        Assert.Equal(1, cache.Misses);
    }

    [Fact]
    public async Task An_expired_entry_is_fetched_again()
    {
        var cache = new TtlCache();
        var fetches = 0;
        Task<string?> Fetch() { fetches++; return Task.FromResult<string?>("v"); }

        await cache.GetOrFetchAsync("k", 0.05, Fetch);
        await Task.Delay(120);
        await cache.GetOrFetchAsync("k", 0.05, Fetch);

        Assert.Equal(2, fetches);
    }

    [Fact]
    public async Task A_lobby_asking_at_once_causes_one_fetch_not_ten()
    {
        // Ten rows rendering together must not become ten identical requests.
        var cache = new TtlCache();
        var fetches = 0;

        await Task.WhenAll(Enumerable.Range(0, 10).Select(_ =>
            cache.GetOrFetchAsync<string>("mmr:p1", 90, async () =>
            {
                Interlocked.Increment(ref fetches);
                await Task.Delay(30);
                return "payload";
            })));

        Assert.Equal(1, fetches);
    }

    [Fact]
    public async Task A_null_answer_is_not_remembered()
    {
        // A player with no MMR yet is worth asking about again, and a failed
        // call must not stick around pretending to be an answer.
        var cache = new TtlCache();
        var fetches = 0;
        Task<string?> Nothing() { fetches++; return Task.FromResult<string?>(null); }

        await cache.GetOrFetchAsync("k", 90, Nothing);
        await cache.GetOrFetchAsync("k", 90, Nothing);

        Assert.Equal(2, fetches);
    }

    [Fact]
    public void Peeking_at_something_absent_says_so()
    {
        var cache = new TtlCache();

        Assert.False(cache.TryPeek<string>("nope", out var value));
        Assert.Null(value);
    }

    [Fact]
    public void Entries_can_be_read_back_by_value()
    {
        var cache = new TtlCache();
        cache.Put("name:p1", ("Player", "NA1"), 3600);

        Assert.True(cache.TryPeek<object>("name:p1", out var hit));
        Assert.Equal(("Player", "NA1"), Assert.IsType<ValueTuple<string, string>>(hit));
    }
}
