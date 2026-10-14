using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows;
using SpikeSight.Core;
using SpikeSight.Core.Riot;
using SpikeSight.Core.Windows;

namespace SpikeSight.App;

public partial class MainWindow : Window
{
    private readonly StringBuilder _log = new();
    private OverlayWindow? _overlay;

    public MainWindow()
    {
        InitializeComponent();
        Loaded += async (_, _) => await StartAsync();
    }

    /// <summary>Where the preview writes its self-test, so it can be read
    /// back without squinting at a window mid-game.</summary>
    private static readonly string LogPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SpikeSight", "v3-preview.log");

    private void Line(string text)
    {
        _log.AppendLine(text);
        Report.Text = _log.ToString();
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
            File.WriteAllText(LogPath, _log.ToString());
        }
        catch (IOException) { /* the window still has it */ }
    }

    private async Task StartAsync()
    {
        // Who had focus before we put anything on screen. The windows are
        // already up by the time Loaded fires, so if this still names the
        // game, nothing was stolen.
        var foreground = Win32.ForegroundProcessId();
        var owner = ProcessName(foreground);
        var ours = Environment.ProcessId;

        Line("== focus ==");
        Line($"  foreground after our windows opened: {owner} (pid {foreground})");
        Line($"  we are pid {ours}");
        Line(foreground == ours
            ? "  TOOK FOCUS - this is the bug the rewrite exists to kill"
            : "  kept out of the way - foreground never changed hands");

        _overlay = new OverlayWindow();
        _overlay.PlaceTopRight();
        _overlay.Show();

        var hwnd = _overlay.Handle;
        Line("\n== overlay window ==");
        Line($"  click-through : {Win32.HasStyle(hwnd, Win32.WsExTransparent)}");
        Line($"  never focuses : {Win32.HasStyle(hwnd, Win32.WsExNoActivate)}");
        Line($"  off taskbar   : {Win32.HasStyle(hwnd, Win32.WsExToolWindow)}");
        Line($"  always on top : {Win32.HasStyle(hwnd, Win32.WsExTopmost)}");
        Line($"  processes used: 0 (it is a window, not a browser)");

        await ConnectAsync();
    }

    private async Task ConnectAsync()
    {
        Line("\n== riot ==");
        try
        {
            using var local = new LocalClient();
            var credentials = await local.GetCredentialsAsync();
            var endpoints = await Endpoints.ResolveAsync(local);
            Line($"  {endpoints.Region}/{endpoints.Shard} on {endpoints.ClientVersion}");

            using var remote = new RemoteClient(local, endpoints);
            var core = await remote.CoregamePlayerAsync(credentials.Puuid);
            var pre = core is null ? await remote.PregamePlayerAsync(credentials.Puuid) : null;

            static string? MatchId(JsonElement? e)
                => e is { ValueKind: JsonValueKind.Object } v
                   && v.TryGetProperty("MatchID", out var m) ? m.GetString() : null;

            var state = MatchId(core) is not null ? "INGAME"
                      : MatchId(pre) is not null ? "PREGAME" : "MENUS";
            Line($"  state: {state}");

            var payload = state switch
            {
                "INGAME" => await remote.CoregameMatchAsync(MatchId(core)!),
                "PREGAME" => await remote.PregameMatchAsync(MatchId(pre)!),
                _ => null,
            };

            if (payload is { } match)
            {
                var players = match.TryGetProperty("Players", out var list)
                    && list.ValueKind == JsonValueKind.Array
                    ? list.GetArrayLength()
                    : match.GetProperty("AllyTeam").GetProperty("Players").GetArrayLength();
                var mode = match.TryGetProperty("ModeID", out var m)
                    ? (m.GetString() ?? "").Split('/').LastOrDefault() : "";
                Line($"  {players} players · {mode}");
                _overlay?.Say($"{players} in the lobby\n{mode}");
            }
            else
            {
                _overlay?.Say("In menus — queue up.");
            }
        }
        catch (SpikeSightException ex)
        {
            Line($"  {ex.Message}");
            if (ex.Hint.Length > 0) Line($"  {ex.Hint}");
            _overlay?.Say(ex.Message);
        }
    }

    private static string ProcessName(uint pid)
    {
        if (pid == 0) return "(none)";
        try { return Process.GetProcessById((int)pid).ProcessName; }
        catch (ArgumentException) { return "(gone)"; }
    }

    protected override void OnClosed(EventArgs e)
    {
        _overlay?.Close();
        base.OnClosed(e);
    }
}
