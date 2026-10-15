using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Media;
using SpikeSight.Core.Game;
using SpikeSight.Core.Riot;
using SpikeSight.Core.Windows;

namespace SpikeSight.App;

/// <summary>One board row, flattened for binding.</summary>
public sealed record Row(
    string Agent, string Name, string Rank, string Peak, string Level,
    bool IsSelf, Brush NameBrush, Brush RankBrush);

public partial class MainWindow : Window
{
    private readonly StringBuilder _diagnostics = new();
    private readonly CancellationTokenSource _stopping = new();
    private readonly LocalClient _local = new();
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

        _overlay = new OverlayWindow();
        _overlay.PlaceTopRight();
        _overlay.Show();

        var hwnd = _overlay.Handle;
        Note($"focus: {ProcessName(foreground)} {(stolen ? "<- TOOK IT" : "(not us)")}  "
             + $"| overlay: click-through={Win32.HasStyle(hwnd, Win32.WsExTransparent)} "
             + $"no-activate={Win32.HasStyle(hwnd, Win32.WsExNoActivate)} "
             + $"topmost={Win32.HasStyle(hwnd, Win32.WsExTopmost)} "
             + $"| processes=1");

        _poller = new MatchPoller(_local);
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
            _overlay?.Say(snapshot.Message);
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
                    Name: player.Name.Display,
                    Rank: tier.IsRanked
                        ? $"{tier.Name}{(player.Rank.Rr is > 0 ? $"  {player.Rank.Rr}" : "")}"
                        : "Unranked",
                    Peak: player.Peak is { } peak ? $"peak {Ranks.Of(peak.Tier).Name}" : "",
                    Level: player.Level?.ToString() ?? (player.LevelHidden ? "hidden" : "-"),
                    IsSelf: player.IsSelf,
                    NameBrush: Swatch(player.Name.Hidden ? "#98A1B3" : "#E6E9EF"),
                    RankBrush: Swatch(tier.Color)));
            }
        }
        Rows.ItemsSource = rows;

        var first = board.Sides.First(s => s.Players.Count > 0);
        _overlay?.Say($"{first.Label}\n{rows.Count} players - {board.Label}");

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
                 + $"{(player.Name.Hidden ? "INCOGNITO " + player.Name.HiddenId : "named")}");
        }
    }

    private static Brush Swatch(string hex)
        => new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));

    private static string ProcessName(uint pid)
    {
        if (pid == 0) return "(none)";
        try { return Process.GetProcessById((int)pid).ProcessName; }
        catch (ArgumentException) { return "(gone)"; }
    }

    protected override void OnClosed(EventArgs e)
    {
        _stopping.Cancel();
        _poller?.Dispose();
        _local.Dispose();
        _overlay?.Close();
        base.OnClosed(e);
    }
}
