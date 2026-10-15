using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using SpikeSight.Core;
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
    private OverlayWindow? _overlay;

    private static readonly string LogPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SpikeSight", "v3-preview.log");

    public MainWindow()
    {
        InitializeComponent();
        Loaded += async (_, _) => await StartAsync();
    }

    private void Note(string text)
    {
        _diagnostics.AppendLine(text);
        Diagnostics.Text = _diagnostics.ToString();
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
            File.WriteAllText(LogPath, _diagnostics.ToString());
        }
        catch (IOException) { /* the window still has it */ }
    }

    private async Task StartAsync()
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

        await ConnectAsync();
    }

    private async Task ConnectAsync()
    {
        try
        {
            using var local = new LocalClient();
            var credentials = await local.GetCredentialsAsync();
            var endpoints = await Endpoints.ResolveAsync(local);
            using var remote = new RemoteClient(local, endpoints);
            Note($"riot : {endpoints.Region}/{endpoints.Shard} {endpoints.ClientVersion}");

            var core = await remote.CoregamePlayerAsync(credentials.Puuid);
            var matchId = MatchId(core);
            if (matchId is null)
            {
                MatchLine.Text = "In menus - queue up.";
                _overlay?.Say("In menus - queue up.");
                return;
            }

            if (await remote.CoregameMatchAsync(matchId) is not { } match) return;

            if (!BoardBuilder.IsLobby(match))
            {
                // The Range, most likely: a game server session with nobody
                // else in it, and so nothing to scout.
                MatchLine.Text = "Nobody else here - the Range, probably.";
                _overlay?.Say("In menus - queue up.");
                return;
            }

            // MMR for everyone, then names for only those we may ask about.
            var roster = match.GetProperty("Players").EnumerateArray().ToList();
            var mmr = new Dictionary<string, JsonElement>();
            var resolvable = new List<string>();
            foreach (var entry in roster)
            {
                var puuid = entry.GetProperty("Subject").GetString() ?? "";
                if (puuid.Length == 0) continue;
                if (await remote.MmrAsync(puuid) is { } m) mmr[puuid] = m;

                var identity = entry.TryGetProperty("PlayerIdentity", out var id) ? id : default;
                if (!PrivacyFlags.FromIdentity(identity).Incognito) resolvable.Add(puuid);
            }

            var names = await remote.NamesAsync(resolvable);
            Note($"roster: {roster.Count} players, {mmr.Count} with mmr, "
                 + $"{names.Count} named, {roster.Count - resolvable.Count} incognito never asked about");

            Render(BoardBuilder.Build(match, credentials.Puuid, mmr, names, currentActId: null));
        }
        catch (SpikeSightException ex)
        {
            MatchLine.Text = ex.Message;
            Note(ex.Hint.Length > 0 ? ex.Hint : ex.Message);
            _overlay?.Say(ex.Message);
        }
    }

    private void Render(Board board)
    {
        MatchLine.Text = $"{board.MapName}  -  {board.Label}";

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

        // Log the board without names, so a run can be checked afterwards
        // without putting anyone's riot ID in a file.
        foreach (var player in board.Players)
        {
            var tier = player.Rank.Resolved;
            Note($"  {(player.IsSelf ? "you " : "    ")}{player.AgentName ?? "-",-10} "
                 + $"{tier.Name,-12} lvl={(player.Level?.ToString() ?? (player.LevelHidden ? "hidden" : "-")),-6} "
                 + $"{(player.Name.Hidden ? "INCOGNITO " + player.Name.HiddenId : "named")}");
        }
        var first = board.Sides.First(s => s.Players.Count > 0);
        _overlay?.Say($"{first.Label}\n{rows.Count} players - {board.Label}");
    }

    private static Brush Swatch(string hex)
        => new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));

    private static string? MatchId(JsonElement? element)
        => element is { ValueKind: JsonValueKind.Object } value
           && value.TryGetProperty("MatchID", out var id) ? id.GetString() : null;

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
