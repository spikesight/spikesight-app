using System.IO;
using System.Windows;

namespace SpikeSight.App;

public partial class App : Application
{
    // A property for the same reason the preview log is one: demo mode moves
    // the data directory, and a field would have captured the real path
    // before that happened.
    private static string CrashLog
        => Path.Combine(SpikeSight.Core.Store.Paths.DataDir, "v3-crash.log");

    public App()
    {
        YieldToTheGame();

        // A crash that leaves nothing behind gets mistaken for someone closing
        // the window - which is exactly what happened with a mistyped colour
        // in a data template, because it only threw once a row was drawn.
        DispatcherUnhandledException += (_, e) => Record("UI thread", e.Exception);
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
            Record("background", e.ExceptionObject as Exception);
        TaskScheduler.UnobservedTaskException += (_, e) => Record("task", e.Exception);
    }

    /// <summary>
    /// Run below normal priority, always.
    ///
    /// This is a scoreboard sitting next to a game that needs every frame.
    /// Whenever Windows has to choose between the two, it should not be
    /// choosing us - and nothing here is urgent enough to argue about it.
    /// </summary>
    private static void YieldToTheGame()
    {
        try
        {
            using var self = System.Diagnostics.Process.GetCurrentProcess();
            self.PriorityClass = System.Diagnostics.ProcessPriorityClass.BelowNormal;
        }
        catch (Exception ex) when (ex is InvalidOperationException
                                      or System.ComponentModel.Win32Exception
                                      or NotSupportedException)
        {
            // Not being able to lower our own priority is not worth a word.
        }
    }

    protected override void OnStartup(StartupEventArgs e)
    {
        // Before any window, because MainWindow opens its notes database in a
        // field initializer. Demo mode gets its own folder so a screenshot
        // session cannot touch, and cannot be contaminated by, real flags.
        if (e.Args.Contains("--demo", StringComparer.OrdinalIgnoreCase))
        {
            SpikeSight.Core.Store.Paths.DataDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "SpikeSight", "demo");
        }
        base.OnStartup(e);
    }

    private static void Record(string where, Exception? error)
    {
        if (error is null) return;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(CrashLog)!);
            File.AppendAllText(CrashLog, $"{DateTime.Now:s} [{where}] {error}\n\n");
        }
        catch (IOException) { /* nothing useful left to do */ }
    }
}
