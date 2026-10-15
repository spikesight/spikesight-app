using System.Diagnostics;

namespace SpikeSight.Core.Riot;

/// <summary>
/// A deliberately conservative outbound rate limiter.
///
/// Riot publishes no rate limit for client traffic, so the only responsible
/// policy is to stay well under anything a real client would do. Every remote
/// request goes through one shared limiter: a token bucket for the average
/// rate, and a semaphore capping how many are in flight at once.
///
/// This is not optional politeness. Without it, scanning a ten-player lobby
/// fires eleven requests as fast as the socket will take them, and a dodge
/// followed by a re-queue does it twice in a few seconds - which is exactly
/// how the first build of this port earned a 429.
/// </summary>
public sealed class RateLimiter : IDisposable
{
    private readonly double _ratePerSecond;
    private readonly double _capacity;
    private readonly SemaphoreSlim _concurrency;
    private readonly SemaphoreSlim _bucketGate = new(1, 1);
    private readonly Stopwatch _clock = Stopwatch.StartNew();

    private double _tokens;
    private double _updated;

    public RateLimiter(double ratePerSecond = 4.0, int burst = 8, int maxConcurrency = 3)
    {
        _ratePerSecond = Math.Max(0.1, ratePerSecond);
        _capacity = Math.Max(1.0, burst);
        _tokens = _capacity;
        _updated = Seconds;
        _concurrency = new SemaphoreSlim(Math.Max(1, maxConcurrency));
    }

    private double Seconds => _clock.Elapsed.TotalSeconds;

    public long TotalRequests { get; private set; }

    /// <summary>Hold the returned slot for the life of one request.</summary>
    public async Task<IDisposable> SlotAsync(CancellationToken ct = default)
    {
        await _concurrency.WaitAsync(ct);
        try
        {
            await TakeTokenAsync(ct);
        }
        catch
        {
            _concurrency.Release();
            throw;
        }
        return new Slot(_concurrency);
    }

    private async Task TakeTokenAsync(CancellationToken ct)
    {
        while (true)
        {
            double wait;
            await _bucketGate.WaitAsync(ct);
            try
            {
                var now = Seconds;
                _tokens = Math.Min(_capacity, _tokens + (now - _updated) * _ratePerSecond);
                _updated = now;

                if (_tokens >= 1.0)
                {
                    _tokens -= 1.0;
                    TotalRequests++;
                    return;
                }
                wait = (1.0 - _tokens) / _ratePerSecond;
            }
            finally
            {
                _bucketGate.Release();
            }

            // Capped so a cancellation is noticed promptly.
            await Task.Delay(TimeSpan.FromSeconds(Math.Min(wait, 1.0)), ct);
        }
    }

    private sealed class Slot(SemaphoreSlim concurrency) : IDisposable
    {
        private int _released;

        public void Dispose()
        {
            if (Interlocked.Exchange(ref _released, 1) == 0) concurrency.Release();
        }
    }

    public void Dispose()
    {
        _concurrency.Dispose();
        _bucketGate.Dispose();
    }
}
