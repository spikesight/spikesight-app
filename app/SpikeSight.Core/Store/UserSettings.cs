using System.Text.Json;
using System.Text.Json.Serialization;

namespace SpikeSight.Core.Store;

/// <summary>
/// What the user has chosen. Everything off by default except the things that
/// cost nothing, so a fresh install is quiet.
/// </summary>
public sealed record UserSettings
{
    [JsonPropertyName("overlay.enabled")]
    public bool OverlayEnabled { get; init; }

    [JsonPropertyName("stats.deep")]
    public bool DeepStats { get; init; } = true;

    [JsonPropertyName("smurf.enabled")]
    public bool SmurfDetection { get; init; } = true;

    [JsonPropertyName("smurf.threshold")]
    public int SmurfThreshold { get; init; } = 50;

    [JsonPropertyName("tray.closeToTray")]
    public bool CloseToTray { get; init; }

    [JsonPropertyName("tray.minimizeToTray")]
    public bool MinimizeToTray { get; init; }

    [JsonPropertyName("startWithWindows")]
    public bool StartWithWindows { get; init; }

    /// <summary>
    /// Show the diagnostic panel in the window.
    ///
    /// Off: it is developer output - which process holds focus, the roster
    /// without names, what the loop decided. It is always written to
    /// v3-preview.log either way, so turning this on is only about whether it
    /// is on screen.
    /// </summary>
    [JsonPropertyName("showDiagnostics")]
    public bool ShowDiagnostics { get; init; }

    public Game.Settings ToGameSettings() => Game.Settings.Default with
    {
        SmurfDetectionEnabled = SmurfDetection,
        SmurfScoreThreshold = SmurfThreshold,
    };

    private static string File => Path.Combine(Paths.DataDir, "v3-settings.json");

    public static UserSettings Load()
    {
        try
        {
            return System.IO.File.Exists(File)
                ? JsonSerializer.Deserialize<UserSettings>(System.IO.File.ReadAllText(File)) ?? new()
                : new UserSettings();
        }
        catch (Exception ex) when (ex is IOException or JsonException)
        {
            // An unreadable settings file must not stop the app starting.
            return new UserSettings();
        }
    }

    public void Save()
    {
        try
        {
            Paths.EnsureDataDir();
            System.IO.File.WriteAllText(File,
                JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch (IOException)
        {
            // Nothing useful to do; the choice still applies this session.
        }
    }
}
