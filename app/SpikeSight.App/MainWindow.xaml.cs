using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using SpikeSight.Core.Game;
using SpikeSight.Core.Riot;
using SpikeSight.Core.Store;
using SpikeSight.Core.Windows;
using SpikeSight.Core.Windows;

namespace SpikeSight.App;

/// <summary>One board row, flattened for binding.</summary>
public sealed record Row(
    string Agent, string Name, string Rank, string Peak, string Level,
    bool IsSelf, Brush NameBrush, Brush RankBrush,
    string Form = "", string Smurf = "", string Party = "",
    string Puuid = "", string Note = "", string Met = "", Brush? NoteBrush = null);

public partial class MainWindow : Window
{
    private readonly StringBuilder _diagnostics = new();
    private readonly CancellationTokenSource _stopping = new();
    private readonly LocalClient _local = new();
    private readonly NotesStore _notes = new(Paths.NotesDb);
    private UserSettings _settings = UserSettings.Load();
    private TrayIcon? _tray;
    private bool _reallyQuitting;
    private OverlayWindow? _overlay;
    private MatchPoller? _poller;
    private string _lastState = "";

    private static readonly string LogPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SpikeSight", "v3-preview.log");

    public MainWindow()
    {
        InitializeComponent();
        Loaded += (_, _) => Start();
    }

    private void Note(string text)
    {
        _diagnostics.AppendLine($"{DateTime.Now:HH:mm:ss}  {text}");
        Diagnostics.Text = _diagnostics.ToString();
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
            File.WriteAllText(LogPath, _diagnostics.ToString());
        }
        catch (IOException) { /* the window still has it */ }
    }

    private void Start()
    {
        // Who holds foreground now that our windows are up. If this still
        // names the game, nothing was taken.
        var foreground = Win32.ForegroundProcessId();
        var stolen = foreground == Environment.ProcessId;

        // Built now so the handle exists and the styles are on, but not shown:
        // it belongs over agent select and nowhere else.
        _overlay = new OverlayWindow();
        _overlay.PlaceTopRight();

        var hwnd = _overlay.Handle;
        Note($"focus: {ProcessName(foreground)} {(stolen ? "<- TOOK IT" : "(not us)")}  "
             + $"| overlay: click-through={Win32.HasStyle(hwnd, Win32.WsExTransparent)} "
             + $"no-activate={Win32.HasStyle(hwnd, Win32.WsExNoActivate)} "
             + $"topmost={Win32.HasStyle(hwnd, Win32.WsExTopmost)} "
             + $"| processes=1");

        _tray = new TrayIcon(
            show: () => Dispatcher.InvokeAsync(Reveal),
            quit: () => Dispatcher.InvokeAsync(() => { _reallyQuitting = true; Close(); }));

        ShowSettings();
        _poller = new MatchPoller(_local)
        {
            Notes = _notes,
            DeepStats = _settings.DeepStats,
            Settings = _settings.ToGameSettings(),
        };
        _poller.Warned += message => Dispatcher.InvokeAsync(() => Note(message));
        // The loop runs off the UI thread; marshal each snapshot back onto it.
        _poller.Updated += snapshot => Dispatcher.InvokeAsync(() => Show(snapshot));
        _ = _poller.RunAsync(_stopping.Token);
    }

    private void Show(Snapshot snapshot)
    {
        if (snapshot.Board is not { } board)
        {
            MatchLine.Text = snapshot.Message;
            Rows.ItemsSource = null;
            _overlay?.HidePanel();
            if (snapshot.State != _lastState) Note($"state: {snapshot.State} - {snapshot.Message}");
            _lastState = snapshot.State;
            return;
        }

        MatchLine.Text = snapshot.Stale
            ? $"{board.MapName}  -  {board.Label}  (over)"
            : $"{board.MapName}  -  {board.Label}";

        var rows = new List<Row>();
        foreach (var side in board.Sides.Where(s => s.Players.Count > 0))
        {
            foreach (var player in side.Players)
            {
                var tier = player.Rank.Resolved;
                rows.Add(new Row(
                    Agent: player.AgentName ?? "-",
                    // A hidden player is shown by agent in the column beside
                    // this one, so repeating it here says nothing. The local
                    // code does: it is what tells two hidden players apart,
                    // and what a note on one of them is filed under.
                    Name: player.Name.Hidden
                        ? $"#{player.Name.HiddenId ?? "hidden"}"
                        : player.Name.Display,
                    Rank: tier.IsRanked
                        ? $"{tier.Name}{(player.Rank.Rr is > 0 ? $"  {player.Rank.Rr}" : "")}"
                        : "Unranked",
                    Peak: player.Peak is { } peak ? $"peak {Ranks.Of(peak.Tier).Name}" : "",
                    Level: player.Level?.ToString() ?? (player.LevelHidden ? "hidden" : "-"),
                    IsSelf: player.IsSelf,
                    NameBrush: Swatch(player.Name.Hidden ? "#98A1B3" : "#E6E9EF"),
                    RankBrush: Swatch(tier.Color),
                    Form: Form(player),
                    // Only scores worth acting on: a middling one was never a
                    // reason to play differently.
                    Smurf: player.Smurf is { Flagged: true } verdict
                        ? $"SMURF {verdict.Score}" : "",
                    Party: player.Party is { } party
                        ? $"{party.Size}-STACK{(party.Confidence == "likely" ? "?" : "")}" : "",
                    Puuid: player.Puuid,
                    Note: player.WorstNote is { } worst ? worst.ToString().ToUpperInvariant() : "",
                    // Counted before this lobby is recorded, so "3rd time"
                    // means three previous games, not counting this one.
                    Met: player.Encounters is { Count: > 0 } met ? $"met {met.Count}x" : "",
                    NoteBrush: Swatch(player.WorstNote switch
                    {
                        Severity.Dodge => "#FF4655",
                        Severity.Avoid => "#E0A33A",
                        Severity.Watch => "#2FB3A4",
                        _ => "#6E7687",
                    })));
            }
        }
        Rows.ItemsSource = rows;

        // Agent select only. Once the match starts the panel has done its
        // job, and leaving it up is just something drawn over the game.
        if (_settings.OverlayEnabled && snapshot.State == "PREGAME" && !snapshot.Stale)
        {
            var side = board.Sides.First(s => s.Players.Count > 0);
            _overlay?.ShowLobby($"{side.Label} - {board.MapName}", rows);
        }
        else
        {
            _overlay?.HidePanel();
        }

        if (snapshot.State == _lastState) return;
        _lastState = snapshot.State;

        var hidden = board.Players.Count(p => p.Flags.Incognito);
        Note($"state: {snapshot.State} - {board.MapName}, {board.Label}, "
             + $"{rows.Count} players, {hidden} incognito never asked about");
        // Names stay out of the log; this is enough to check a run afterwards.
        foreach (var player in board.Players)
        {
            Note($"    {(player.IsSelf ? "you " : "    ")}{player.AgentName ?? "-",-10} "
                 + $"{player.Rank.Resolved.Name,-12} "
                 + $"lvl={(player.Level?.ToString() ?? (player.LevelHidden ? "hidden" : "-")),-6} "
                 + $"{(player.Name.Hidden ? "INCOGNITO " + player.Name.HiddenId : "named")}"
                 + $"{(player.Party is { } p ? $"  {p.Size}-stack/{p.Confidence}" : "")}"
                 + $"{(player.Smurf is { Flagged: true } v ? $"  SMURF {v.Score}" : "")}"
                 + $"{(player.Stats is { Matches: > 0 } st ? $"  {st.Kd:F2}kd {st.Acs}acs {st.Performance}ps" : "")}");
        }
    }

    private static string Form(BoardPlayer player)
    {
        if (player.Stats is not { Matches: > 0 } stats) return "";
        var parts = new List<string> { $"{stats.Kd:F2} K/D" };
        if (stats.Acs is { } acs) parts.Add($"{acs} ACS");
        if (stats.Performance is { } performance) parts.Add($"{performance} PS");
        return string.Join("  ", parts);
    }

    /// <summary>Flag or unflag the player whose row was right-clicked.</summary>
    private async void FlagPlayer(object sender, RoutedEventArgs e)
    {
        if (sender is not MenuItem { Tag: string tag } item) return;
        if (item.Parent is not ContextMenu { PlacementTarget: FrameworkElement { Tag: string puuid } })
            return;
        if (puuid.Length == 0 || !int.TryParse(tag, out var severity)) return;

        try
        {
            if (severity == 0)
            {
                var existing = await _notes.NotesForAsync([puuid], _stopping.Token);
                foreach (var note in existing.GetValueOrDefault(puuid) ?? [])
                    await _notes.DeleteNoteAsync(note.Id, _stopping.Token);
                Note($"flags cleared for {puuid[..4]}...");
            }
            else
            {
                await _notes.AddNoteAsync(puuid, (Severity)severity, ct: _stopping.Token);
                Note($"flagged {puuid[..4]}... as {(Severity)severity}");
            }
            // Nothing about the lobby changed, so re-read rather than rescan.
            _poller?.RefreshNotes();
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            Note($"could not save that flag: {ex.Message}");
        }
    }

    private void Reveal()
    {
        Show();
        WindowState = WindowState.Normal;
        Activate();
    }

    private void ToggleSettings(object sender, RoutedEventArgs e)
    {
        var opening = SettingsPane.Visibility != Visibility.Visible;
        SettingsPane.Visibility = opening ? Visibility.Visible : Visibility.Collapsed;
        MatchLine.Visibility = opening ? Visibility.Collapsed : Visibility.Visible;
    }

    private void ShowSettings()
    {
        OptOverlay.IsChecked = _settings.OverlayEnabled;
        OptDeepStats.IsChecked = _settings.DeepStats;
        OptSmurf.IsChecked = _settings.SmurfDetection;
        OptCloseToTray.IsChecked = _settings.CloseToTray;
        // Read from the registry rather than the settings file: that is where
        // the truth is, and somebody may have removed it by hand.
        OptAutostart.IsChecked = Autostart.IsEnabled();
    }

    private void SettingChanged(object sender, RoutedEventArgs e)
    {
        var wanted = OptAutostart.IsChecked == true;
        var autostartOk = wanted == Autostart.IsEnabled() || Autostart.Set(wanted);

        _settings = _settings with
        {
            OverlayEnabled = OptOverlay.IsChecked == true,
            DeepStats = OptDeepStats.IsChecked == true,
            SmurfDetection = OptSmurf.IsChecked == true,
            CloseToTray = OptCloseToTray.IsChecked == true,
            StartWithWindows = autostartOk && wanted,
        };
        _settings.Save();

        if (!autostartOk)
        {
            OptAutostart.IsChecked = Autostart.IsEnabled();
            SettingsNote.Text = "Windows would not let that registry value be written.";
        }
        else
        {
            SettingsNote.Text = "Deep stats and smurf changes apply to the next lobby.";
        }

        if (!_settings.OverlayEnabled) _overlay?.HidePanel();
    }

    private static Brush Swatch(string hex)
        => new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));

    private static string ProcessName(uint pid)
    {
        if (pid == 0) return "(none)";
        try { return Process.GetProcessById((int)pid).ProcessName; }
        catch (ArgumentException) { return "(gone)"; }
    }

    protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
    {
        // Closing to the tray keeps the lobby being watched, which is the
        // point of it - but only when that was asked for.
        if (_settings.CloseToTray && !_reallyQuitting)
        {
            e.Cancel = true;
            Hide();
            _tray?.Notify("SpikeSight", "Still watching. Double-click to bring it back.");
            return;
        }
        base.OnClosing(e);
    }

    protected override void OnClosed(EventArgs e)
    {
        _stopping.Cancel();
        _tray?.Dispose();
        _poller?.Dispose();
        _notes.Dispose();
        _local.Dispose();
        _overlay?.Close();
        base.OnClosed(e);
    }
}
