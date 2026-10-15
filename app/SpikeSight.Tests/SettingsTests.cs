using System.Text.Json;
using SpikeSight.Core.Store;

namespace SpikeSight.Tests;

public class UserSettingsTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "ssset-" + Guid.NewGuid().ToString("N"));
    private readonly string _original = Paths.DataDir;

    public UserSettingsTests()
    {
        Directory.CreateDirectory(_dir);
        Paths.DataDir = _dir;
    }

    public void Dispose()
    {
        Paths.DataDir = _original;
        try { Directory.Delete(_dir, recursive: true); } catch (IOException) { }
    }

    [Fact]
    public void A_fresh_install_is_quiet()
    {
        var settings = UserSettings.Load();

        // Everything that costs something, or puts something on screen over
        // the game, starts off.
        Assert.False(settings.OverlayEnabled);
        Assert.False(settings.CloseToTray);
        Assert.False(settings.StartWithWindows);
        // And the things that cost nothing are on.
        Assert.True(settings.DeepStats);
        Assert.True(settings.SmurfDetection);
        Assert.Equal(50, settings.SmurfThreshold);
    }

    [Fact]
    public void Choices_survive_a_restart()
    {
        new UserSettings { OverlayEnabled = true, DeepStats = false, SmurfThreshold = 70 }.Save();

        var reloaded = UserSettings.Load();

        Assert.True(reloaded.OverlayEnabled);
        Assert.False(reloaded.DeepStats);
        Assert.Equal(70, reloaded.SmurfThreshold);
    }

    [Fact]
    public void An_unreadable_file_does_not_stop_the_app_starting()
    {
        File.WriteAllText(Path.Combine(_dir, "v3-settings.json"), "{ not json at all");

        var settings = UserSettings.Load();

        Assert.True(settings.DeepStats);   // back to defaults rather than a crash
    }

    [Fact]
    public void The_smurf_threshold_reaches_the_scorer()
    {
        var game = new UserSettings { SmurfThreshold = 80, SmurfDetection = false }.ToGameSettings();

        Assert.Equal(80, game.SmurfScoreThreshold);
        Assert.False(game.SmurfDetectionEnabled);
    }

    [Fact]
    public void The_file_is_readable_by_a_person()
    {
        // Somebody will open this in Notepad at some point.
        new UserSettings { OverlayEnabled = true }.Save();

        var text = File.ReadAllText(Path.Combine(_dir, "v3-settings.json"));

        Assert.Contains("overlay.enabled", text);
        Assert.Contains("\n", text);   // indented, not one long line
        using var document = JsonDocument.Parse(text);
        Assert.True(document.RootElement.GetProperty("overlay.enabled").GetBoolean());
    }
}
