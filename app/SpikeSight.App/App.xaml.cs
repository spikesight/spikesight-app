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
        // A crash that leaves nothing behind gets mistaken for someone closing
        // the window - which is exactly what happened with a mistyped colour
        // in a data template, because it only threw once a row was drawn.
        DispatcherUnhandledException += (_, e) => Record("UI thread", e.Exception);
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
            Record("background", e.ExceptionObject as Exception);
        TaskScheduler.UnobservedTaskException += (_, e) => Record("task", e.Exception);
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
