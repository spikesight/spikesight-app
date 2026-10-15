using System.Diagnostics;

namespace SpikeSight.Core.Riot;

/// <summary>
/// In-memory cache with a lifetime per entry.
///
/// This is where most of the politeness lives. Agent select is re-read every
/// few seconds while people lock in, and without a cache each pass refetches
/// every player's MMR - about four times the traffic, for answers that cannot
/// have changed. A rate limiter only paces requests; this stops them being
/// made at all.
///
/// Concurrent callers asking for the same key wait on one fetch rather than
/// starting five of their own.
/// </summary>
public sealed class TtlCache
{
    private readonly record struct Entry(double Expires, object? Value);

    private readonly Dictionary<string, Entry> _entries = [];
    private readonly Dictionary<string, SemaphoreSlim> _gates = [];
    private readonly object _lock = new();
    private readonly Stopwatch _clock = Stopwatch.StartNew();

    public long Hits { get; private set; }
    public long Misses { get; private set; }
    public int Count { get { lock (_lock) return _entries.Count; } }

    private double Now => _clock.Elapsed.TotalSeconds;

    public bool TryPeek<T>(string key, out T? value)
    {
        lock (_lock)
        {
            if (_entries.TryGetValue(key, out var entry) && entry.Expires >= Now)
            {
                value = (T?)entry.Value;
                return true;
            }
            _entries.Remove(key);
            value = default;
            return false;
        }
    }

    public void Put(string key, object? value, double ttlSeconds)
    {
        lock (_lock) _entries[key] = new Entry(Now + ttlSeconds, value);
    }

    public async Task<T?> GetOrFetchAsync<T>(
        string key, double ttlSeconds, Func<Task<T?>> fetch, CancellationToken ct = default)
    {
        if (TryPeek<T>(key, out var cached)) { lock (_lock) Hits++; return cached; }

        SemaphoreSlim gate;
        lock (_lock)
        {
            if (!_gates.TryGetValue(key, out gate!))
                _gates[key] = gate = new SemaphoreSlim(1, 1);
        }

        await gate.WaitAsync(ct);
        try
        {
            // Somebody may have filled it while we queued for the gate.
            if (TryPeek<T>(key, out var filled)) { lock (_lock) Hits++; return filled; }

            lock (_lock) Misses++;
            var value = await fetch();
            // A null answer is not cached: a player with no MMR yet is worth
            // asking about again, and a failed call must not stick.
            if (value is not null) Put(key, value, ttlSeconds);
            return value;
        }
        finally
        {
            gate.Release();
        }
    }

    public void Clear()
    {
        lock (_lock)
        {
            _entries.Clear();
            _gates.Clear();
        }
    }
}
