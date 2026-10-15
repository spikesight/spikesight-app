using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using SpikeSight.Core;
using SpikeSight.Core.Game;
using SpikeSight.Core.Riot;
using SpikeSight.Core.Store;
using SpikeSight.Core.Windows;

namespace SpikeSight.App;

/// <summary>One board row, flattened for binding.</summary>
public sealed record HistoryRow(
    string Map, string Queue, string Agent, string Score, string Kda, string Form,
    string Rr, string RankAfter, Brush ResultBrush, Brush RrBrush, ImageSource? Portrait = null);

public sealed record MetRow(
    string Puuid, string Who, string Code, Visibility CodeVisible,
    string Note, Brush NoteBrush, Brush NoteTint, Visibility NoteVisible,
    string Met, string Split, string Last);

public sealed record AlertRow(string Title, string Text, Brush Tint, Brush Edge);

public sealed record Row(
    string Agent, string Name, string Rank, string Peak, string Level,
    bool IsSelf, Brush NameBrush, Brush RankBrush,
    string Form = "", string Smurf = "", string Party = "",
    string Puuid = "", string Note = "", string Met = "", Brush? NoteBrush = null,
    ImageSource? Portrait = null, Brush? PartyBrush = null, string Sub = "",
    Visibility SubVisible = Visibility.Collapsed,
    Visibility SmurfVisible = Visibility.Collapsed,
    Visibility PartyVisible = Visibility.Collapsed,
    Visibility NoteVisible = Visibility.Collapsed,
    Brush? NoteTint = null, Brush? PartyTint = null);

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
    private Board? _board;
    private bool _loggedForm;

    private static readonly string LogPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SpikeSight", "v3-preview.log");

    public MainWindow()
    {
        InitializeComponent();
        FitToScreen();
        Loaded += (_, _) => Start();
    }

    /// <summary>
    /// Open big enough to show a full lobby without scrolling, but never
    /// bigger than the screen it is on. Ten players, two team headings and
    /// the status line need about 860px; a laptop may not have it.
    /// </summary>
    private void FitToScreen()
    {
        var work = SystemParameters.WorkArea;
        Height = Math.Min(880, work.Height - 60);
        Width = Math.Min(1060, work.Width - 60);
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

        // Bring across 2.x's flags and history, once, read-only.
        var imported = LegacyImport.ImportIfEmpty(_notes);
        if (imported.Anything)
            Note($"imported {imported.Notes} notes and {imported.Encounters} encounters from 2.x");
        else if (imported.Error is { Length: > 0 } problem)
            Note($"could not read 2.x's notes ({problem}); starting empty");

        _tray = new TrayIcon(
            show: () => Dispatcher.InvokeAsync(Reveal),
            quit: () => Dispatcher.InvokeAsync(() => { _reallyQuitting = true; Close(); }));

        ShowSettings();
        _ = CheckForUpdateAsync();
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
            _loggedForm = false;
            return;
        }

        _board = board;
        MatchLine.Text = snapshot.Stale
            ? $"{board.MapName}  -  {board.Label}  (over)"
            : $"{board.MapName}  -  {board.Label}";

        var ally = board.Sides.FirstOrDefault(sd => sd.Side == "ally");
        var enemy = board.Sides.FirstOrDefault(sd => sd.Side == "enemy");

        Rows.ItemsSource = (ally?.Players ?? []).Select(RowFor).ToList();
        AllyLabel.Text = (ally?.Label ?? "Your team").ToUpperInvariant();
        AllyMeta.Text = Side(ally);
        AllyHead.Visibility = ally is { Players.Count: > 0 } ? Visibility.Visible : Visibility.Collapsed;

        var haveEnemy = enemy is { Players.Count: > 0 };
        EnemyRows.ItemsSource = haveEnemy ? enemy!.Players.Select(RowFor).ToList() : null;
        EnemyLabel.Text = (enemy?.Label ?? "Enemy team").ToUpperInvariant();
        EnemyMeta.Text = Side(enemy);
        EnemyHead.Visibility = haveEnemy ? Visibility.Visible : Visibility.Collapsed;

        ShowOdds(board);
        ShowAlerts(board);

        // Agent select only. Once the match starts the panel has done its
        // job, and leaving it up is just something drawn over the game.
        if (_settings.OverlayEnabled && snapshot.State == "PREGAME" && !snapshot.Stale)
        {
            var side = board.Sides.First(s => s.Players.Count > 0);
            _overlay?.ShowLobby($"{side.Label} - {board.MapName}",
                                side.Players.Select(RowFor).ToList());
        }
        else
        {
            _overlay?.HidePanel();
        }

        // The enriched pass republishes with the same state, so logging only
        // on a state change would miss recent form entirely - visible on
        // screen, absent from the log that makes a run checkable afterwards.
        var hasForm = board.Players.Any(p => p.Stats is { Matches: > 0 });
        if (snapshot.State == _lastState && (!hasForm || _loggedForm)) return;
        _loggedForm = hasForm;
        _lastState = snapshot.State;

        var hidden = board.Players.Count(p => p.Flags.Incognito);
        Note($"state: {snapshot.State} - {board.MapName}, {board.Label}, "
             + $"{board.Players.Count()} players, {hidden} incognito never asked about");
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

    /// <summary>A short summary for a team heading: how many, and how strong.</summary>
    private static string Side(BoardSide? side)
    {
        if (side is not { Players.Count: > 0 }) return "";
        var ranked = side.Players.Where(p => p.RatingPoints is not null).ToList();
        var count = $"{side.Players.Count} player{(side.Players.Count == 1 ? "" : "s")}";
        return ranked.Count == 0
            ? count
            : $"{count}  ·  avg {Ranks.PointsToLabel(ranked.Average(p => p.RatingPoints!.Value))}";
    }

    private Row RowFor(BoardPlayer player)
    {
        var tier = player.Rank.Resolved;
        var party = player.Party;
        var sub = Subtitle(player);
        var partyColour = party is null
            ? "#00000000"
            : PartyColours[(party.Group - 1) % PartyColours.Length];

        return new Row(
            Agent: player.AgentName ?? "-",
            // A hidden player is shown by agent beside this, so repeating it
            // here says nothing. The local code does: it is what tells two
            // hidden players apart and what a note is filed under.
            Name: player.Name.Hidden ? $"#{player.Name.HiddenId ?? "hidden"}" : player.Name.Display,
            // Rank badge for everyone, RR for yourself only - the game shows
            // other players' rank and not their RR, and that reads as
            // deliberate.
            Rank: tier.IsRanked
                ? $"{tier.Name}{(player.IsSelf && player.Rank.Rr is > 0 ? $"  {player.Rank.Rr}" : "")}"
                : "Unranked",
            Peak: "",
            Level: player.Level?.ToString() ?? (player.LevelHidden ? "hidden" : "-"),
            IsSelf: player.IsSelf,
            NameBrush: Swatch(player.Name.Hidden ? "#A9B2C1" : "#EDF0F5"),
            RankBrush: Swatch(tier.Color),
            Form: Form(player),
            Smurf: player.Smurf is { Flagged: true } verdict ? $"SMURF {verdict.Score}" : "",
            Party: party is null
                ? ""
                : $"{party.Size}-STACK{(party.Confidence == "likely" ? "?" : "")}",
            Puuid: player.Puuid,
            Note: player.WorstNote?.ToString().ToUpperInvariant() ?? "",
            Met: "",
            NoteBrush: Swatch(NoteColour(player.WorstNote)),
            Portrait: Portraits.For(player.CharacterId),
            PartyBrush: Swatch(partyColour),
            Sub: sub,
            SubVisible: sub.Length > 0 ? Visibility.Visible : Visibility.Collapsed,
            SmurfVisible: player.Smurf is { Flagged: true } ? Visibility.Visible : Visibility.Collapsed,
            PartyVisible: party is null ? Visibility.Collapsed : Visibility.Visible,
            NoteVisible: player.WorstNote is null ? Visibility.Collapsed : Visibility.Visible,
            NoteTint: Swatch(NoteColour(player.WorstNote), 0x26),
            PartyTint: Swatch(partyColour, 0x22));
    }

    private static string NoteColour(Severity? severity) => severity switch
    {
        Severity.Dodge => "#FF4655",
        Severity.Avoid => "#E8B04B",
        Severity.Watch => "#2FD4BE",
        _ => "#788192",
    };

    private void ShowOdds(Board board)
    {
        if (board.WinProbability is not { } odds || odds.KnownRanks == 0)
        {
            OddsRow.Visibility = Visibility.Collapsed;
            return;
        }

        OddsRow.Visibility = Visibility.Visible;
        OddsAlly.Text = $"{odds.Percent}%";
        OddsEnemy.Text = $"{100 - odds.Percent}%";
        MatchSub.Text = odds.Note;

        // Sized on layout rather than guessed, so it is right at any width.
        var bar = (FrameworkElement)OddsFill.Parent;
        OddsFill.Width = Math.Max(0, bar.ActualWidth * odds.Percent / 100.0);
        OddsFill.Height = 6;
        bar.SizeChanged -= ResizeOdds;
        bar.SizeChanged += ResizeOdds;
    }

    private void ResizeOdds(object sender, SizeChangedEventArgs e)
    {
        if (_board?.WinProbability is not { } odds) return;
        OddsFill.Width = Math.Max(0, e.NewSize.Width * odds.Percent / 100.0);
    }

    /// <summary>
    /// What is worth saying out loud about this lobby, above the board.
    /// </summary>
    private void ShowAlerts(Board board)
    {
        var alerts = new List<AlertRow>();

        var flagged = board.Players.Where(p => !p.IsSelf && p.WorstNote is not null).ToList();
        if (flagged.Count > 0)
        {
            var worst = flagged.Max(p => p.WorstNote!.Value);
            alerts.Add(new AlertRow(
                worst == Severity.Dodge ? "DODGE" : worst.ToString().ToUpperInvariant(),
                $"{flagged.Count} player{(flagged.Count == 1 ? "" : "s")} you have flagged: "
                + string.Join(", ", flagged.Take(4).Select(p => p.Name.Display)),
                Swatch("#22FF4655"), Swatch("#FF4655")));
        }

        var smurfs = board.Players.Where(p => !p.IsSelf && p.Smurf is { Flagged: true }).ToList();
        if (smurfs.Count > 0)
        {
            var enemy = smurfs.Count(p => p.Side == "enemy");
            alerts.Add(new AlertRow(
                "SMURF",
                $"{smurfs.Count} possible smurf{(smurfs.Count == 1 ? "" : "s")}"
                + (enemy > 0 ? $", {enemy} on the other side" : ""),
                Swatch("#22E0A33A"), Swatch("#E0A33A")));
        }

        Alerts.ItemsSource = alerts;
    }

    /// <summary>Colours for premade groups, in the order they are found.</summary>
    private static readonly string[] PartyColours =
        ["#2FB3A4", "#E0A33A", "#8B7FD4", "#4FA3E3", "#D4786A"];

    /// <summary>
    /// The quiet line under a name: peak rank, and how often you have met.
    /// </summary>
    private static string Subtitle(BoardPlayer player)
    {
        var bits = new List<string>();
        if (player.Peak is { } peak) bits.Add($"peak {Ranks.Of(peak.Tier).Name}");
        if (player.Encounters is { Count: > 0 } met)
            bits.Add($"met {met.Count}x ({met.Ally} with, {met.Enemy} against)");
        return string.Join("  ·  ", bits);
    }

    private static string CharacterOf(BoardPlayer player) => player.CharacterId;

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
        if (puuid.Length == 0) return;

        try
        {
            if (tag == "note")
            {
                await AddNoteWithTextAsync(puuid);
                return;
            }
            if (!int.TryParse(tag, out var severity)) return;

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
        OptDiagnostics.IsChecked = _settings.ShowDiagnostics;
        ApplyDiagnosticsVisibility();
    }

    /// <summary>
    /// The panel is developer output, so it is hidden unless asked for. The
    /// log file is written either way - that is what makes a test run
    /// readable afterwards without anybody transcribing anything.
    /// </summary>
    private void ApplyDiagnosticsVisibility()
        => Diagnostics.Visibility = _settings.ShowDiagnostics
            ? Visibility.Visible : Visibility.Collapsed;

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
            ShowDiagnostics = OptDiagnostics.IsChecked == true,
        };
        _settings.Save();
        ApplyDiagnosticsVisibility();

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

    /// <summary>
    /// Flag somebody with a reason attached.
    ///
    /// A severity on its own says you did not want to see them again; the
    /// text is what tells you why, three weeks later, when the name means
    /// nothing.
    /// </summary>
    private async Task AddNoteWithTextAsync(string puuid)
    {
        var player = _board?.Players.FirstOrDefault(p => p.Puuid == puuid);
        var who = player?.Name.Hidden == false && player.Name.Display.Length > 0
            ? player.Name.Display
            : $"#{Privacy.HiddenTag(puuid)}";
        var context = player is null
            ? "From your encounter history."
            : $"{player.AgentName ?? "unknown agent"} - {player.Rank.Resolved.Name}";

        var dialog = new NoteDialog(who, context) { Owner = this };
        if (dialog.ShowDialog() != true) return;

        await _notes.AddNoteAsync(puuid, dialog.Severity, dialog.Body, dialog.Tags,
                                  _stopping.Token);
        Note($"flagged {who} as {dialog.Severity}"
             + (dialog.Body.Length > 0 ? " with a note" : ""));
        _poller?.RefreshNotes();
        if (MetView.Visibility == Visibility.Visible) await LoadMetAsync();
    }

    private string? _releaseUrl;

    private async Task CheckForUpdateAsync()
    {
        var info = await new UpdateCheck().CheckAsync(_stopping.Token);
        if (!info.Available || info.Latest is null) return;

        _releaseUrl = info.Url;
        UpdateText.Text = $"SpikeSight {info.Latest.TrimStart('v')} is out - you have {info.Current}.";
        UpdateBar.Visibility = Visibility.Visible;
        Note($"update available: {info.Latest}");
    }

    private void OpenRelease(object sender, RoutedEventArgs e)
    {
        if (_releaseUrl is not { Length: > 0 } url) return;
        // The download is always the user's click; this opens a page and
        // nothing else.
        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
    }

    /// <summary>Lobby, history or encounters. Each loads when first opened.</summary>
    private async void SwitchView(object sender, RoutedEventArgs e)
    {
        if (sender is not RadioButton { Tag: string view } || !IsLoaded) return;

        LobbyPanel.Visibility = view == "lobby" ? Visibility.Visible : Visibility.Collapsed;
        // The status line and odds belong to the lobby, not the other views.
        StatusRow.Visibility = Visibility.Visible;
        HistoryView.Visibility = view == "history" ? Visibility.Visible : Visibility.Collapsed;
        MetView.Visibility = view == "met" ? Visibility.Visible : Visibility.Collapsed;

        if (view == "history") await LoadHistoryAsync();
        if (view == "met") await LoadMetAsync();
    }

    private async Task LoadHistoryAsync()
    {
        MatchLine.Text = "Reading your recent matches...";
        try
        {
            if (_poller?.Remote is not { } remote || _poller.Stats is not { } stats)
            {
                MatchLine.Text = "Not connected to the Riot Client yet.";
                return;
            }

            var career = await CareerBuilder.BuildAsync(
                remote, stats, GameContent.Bundled, _poller.SelfPuuid,
                _poller.CurrentActId, ct: _stopping.Token);

            HistoryRows.ItemsSource = career.Matches.Select(m => new HistoryRow(
                Map: m.Map,
                Queue: m.Queue.Length > 0 ? m.Queue : "-",
                Agent: m.Agent ?? "-",
                Score: m.RoundsWon is not null && m.RoundsLost is not null
                    ? $"{m.RoundsWon}-{m.RoundsLost}" : "-",
                Kda: m.Kills is null ? "-" : $"{m.Kills}/{m.Deaths}/{m.Assists}",
                Form: Parts(m),
                Rr: m.RrEarned is { } earned ? $"{(earned > 0 ? "+" : "")}{earned} RR" : "",
                RankAfter: m.TierAfter is > 0 ? $"{Ranks.Of(m.TierAfter).Name} {m.RrAfter}" : "",
                Portrait: Portraits.For(m.CharacterId),
                ResultBrush: Swatch(m.Won switch
                {
                    true => "#2FB3A4",
                    false => "#FF4655",
                    _ => "#3A4150",
                }),
                RrBrush: Swatch(m.RrEarned is null ? "#6E7687" : m.RrEarned >= 0 ? "#2FB3A4" : "#FF4655")))
                .ToList();

            var s = career.Summary;
            MatchLine.Text = $"Last {s.Matches}:  {s.Wins}W {s.Losses}L"
                             + (s.Winrate is { } rate ? $"  ({rate:P0})" : "")
                             + (s.Kd is { } kd ? $"   {kd:F2} K/D" : "")
                             + (s.Acs is { } acs ? $"   {acs} ACS" : "")
                             + (s.Performance is { } ps ? $"   {ps} PS" : "")
                             + (s.NetRr is { } rr ? $"   {(rr > 0 ? "+" : "")}{rr} RR" : "");
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            MatchLine.Text = $"Could not read your history: {ex.Message}";
        }
    }

    private static string Parts(CareerMatch match)
    {
        var bits = new List<string>();
        if (match.Kd is { } kd) bits.Add($"{kd:F2} K/D");
        if (match.Acs is { } acs) bits.Add($"{acs} ACS");
        if (match.Performance is { } ps) bits.Add($"{ps} PS");
        return string.Join("  ", bits);
    }

    private async Task LoadMetAsync()
    {
        MatchLine.Text = "Reading who you have played with...";
        var people = await _notes.RecentPlayersAsync(ct: _stopping.Token);

        MetRows.ItemsSource = people.Select(p => new MetRow(
            Puuid: p.Puuid,
            // Their riot ID when we were allowed to keep it, and the local
            // code otherwise. A list of codes is unreadable; a list of names
            // with codes only where there is no name is not.
            Who: p.RiotId ?? $"#{Privacy.HiddenTag(p.Puuid)}",
            Code: p.RiotId is null ? "" : $"#{Privacy.HiddenTag(p.Puuid)}",
            CodeVisible: p.RiotId is null ? Visibility.Collapsed : Visibility.Visible,
            Note: p.WorstNote?.ToString().ToUpperInvariant() ?? "",
            NoteBrush: Swatch(NoteColour(p.WorstNote)),
            NoteTint: Swatch(NoteColour(p.WorstNote), 0x26),
            NoteVisible: p.WorstNote is null ? Visibility.Collapsed : Visibility.Visible,
            Met: p.Met == 1 ? "met once" : $"met {p.Met} times",
            Split: (p.LastAgent is { Length: > 0 } agent ? $"last as {agent}  ·  " : "")
                   + $"{p.Ally} with you, {p.Enemy} against",
            Last: Ago(p.LastSeen))).ToList();

        MatchLine.Text = $"{people.Count} players you have actually played with.";
    }

    private static string Ago(DateTimeOffset when)
    {
        var span = DateTimeOffset.UtcNow - when;
        return span switch
        {
            { TotalMinutes: < 60 } => $"{(int)span.TotalMinutes}m ago",
            { TotalHours: < 24 } => $"{(int)span.TotalHours}h ago",
            { TotalDays: < 30 } => $"{(int)span.TotalDays}d ago",
            _ => when.ToLocalTime().ToString("d MMM yyyy"),
        };
    }

    /// <summary>A brush from a hex colour, optionally at a lower alpha for
    /// the soft fill behind a chip.</summary>
    private static Brush Swatch(string hex, byte alpha = 0xFF)
    {
        var colour = (Color)ColorConverter.ConvertFromString(hex);
        if (alpha != 0xFF) colour = Color.FromArgb(alpha, colour.R, colour.G, colour.B);
        var brush = new SolidColorBrush(colour);
        brush.Freeze();
        return brush;
    }

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
