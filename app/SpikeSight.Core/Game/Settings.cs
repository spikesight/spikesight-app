namespace SpikeSight.Core.Game;

/// <summary>
/// The numbers that decide what the board flags.
///
/// Defaults match what 2.x ships with, including the smurf threshold raised
/// to 50: a middling score was never a reason to play differently, and a board
/// covered in them made the real flags easier to miss.
/// </summary>
public sealed record Settings
{
    public bool SmurfDetectionEnabled { get; init; } = true;

    /// <summary>Below this, an account is young enough to be worth noting.</summary>
    public int LevelThreshold { get; init; } = 60;

    public double KdThreshold { get; init; } = 1.45;
    public double WinrateThreshold { get; init; } = 0.65;
    public int MinGamesForWinrate { get; init; } = 8;

    /// <summary>Only scores at or above this are shown at all.</summary>
    public int SmurfScoreThreshold { get; init; } = 50;

    /// <summary>How much a rating-point gap moves the odds.</summary>
    public double EloScale { get; init; } = 120.0;

    public static Settings Default { get; } = new();
}
