using Microsoft.Win32;

namespace SpikeSight.Core.Windows;

/// <summary>
/// Starting with Windows.
///
/// One value under the current user's Run key. No admin rights, no service,
/// no scheduled task - and removing it is deleting one registry value, which
/// is what "uninstall is deleting the folder" depends on.
/// </summary>
public static class Autostart
{
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "SpikeSight";

    public static bool IsEnabled()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(RunKey);
            return key?.GetValue(ValueName) is string value && value.Length > 0;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    /// <returns>True if the registry now matches what was asked for.</returns>
    public static bool Set(bool enabled, string? exePath = null)
    {
        exePath ??= Environment.ProcessPath;
        if (enabled && exePath is not { Length: > 0 }) return false;

        try
        {
            using var key = Registry.CurrentUser.CreateSubKey(RunKey);
            if (key is null) return false;

            if (enabled) key.SetValue(ValueName, $"\"{exePath}\"");
            else key.DeleteValue(ValueName, throwOnMissingValue: false);
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException)
        {
            return false;
        }
    }
}
