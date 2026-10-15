using System.IO;
using System.Windows;

namespace SpikeSight.App;

public partial class App : Application
{
    private static readonly string CrashLog = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SpikeSight", "v3-crash.log");

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
