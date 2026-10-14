using System.Runtime.InteropServices;

namespace SpikeSight.Core.Windows;

/// <summary>
/// The window behaviour the Python build spent 911 lines not getting from a
/// browser: stay on top, never take focus, let clicks through.
/// </summary>
/// <remarks>
/// Plain <c>DllImport</c> rather than the generated <c>LibraryImport</c>: all
/// of these take blittable arguments, so the generator buys nothing and would
/// mean turning on unsafe code for the whole project.
/// </remarks>
public static class Win32
{
    public const int GwlExStyle = -20;

    public const int WsExTransparent = 0x00000020;  // clicks pass through
    public const int WsExToolWindow  = 0x00000080;  // no taskbar button
    public const int WsExTopmost     = 0x00000008;
    public const int WsExLayered     = 0x00080000;  // needed for opacity
    public const int WsExNoActivate  = 0x08000000;  // never becomes foreground

    [DllImport("user32.dll", SetLastError = true, EntryPoint = "GetWindowLongW")]
    private static extern int GetWindowLong(IntPtr hWnd, int index);

    [DllImport("user32.dll", SetLastError = true, EntryPoint = "SetWindowLongW")]
    private static extern int SetWindowLong(IntPtr hWnd, int index, int value);

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    public static int ExtendedStyle(IntPtr hwnd) => GetWindowLong(hwnd, GwlExStyle);

    public static void AddExtendedStyle(IntPtr hwnd, int bits)
        => SetWindowLong(hwnd, GwlExStyle, ExtendedStyle(hwnd) | bits);

    public static bool HasStyle(IntPtr hwnd, int bit) => (ExtendedStyle(hwnd) & bit) != 0;

    /// <summary>
    /// Make a window an overlay: on top, click-through, no taskbar button,
    /// and incapable of taking focus from the game.
    /// </summary>
    public static void StyleAsOverlay(IntPtr hwnd)
        => AddExtendedStyle(hwnd, WsExTransparent | WsExToolWindow | WsExNoActivate | WsExLayered);

    /// <summary>Which process owns the foreground window right now.</summary>
    public static uint ForegroundProcessId()
    {
        var hwnd = GetForegroundWindow();
        if (hwnd == IntPtr.Zero) return 0;
        _ = GetWindowThreadProcessId(hwnd, out var pid);
        return pid;
    }
}
