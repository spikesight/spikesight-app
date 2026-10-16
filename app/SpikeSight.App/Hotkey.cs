using System.Runtime.InteropServices;
using System.Windows.Interop;

namespace SpikeSight.App;

/// <summary>
/// One system-wide hotkey: Ctrl+Alt+S, to show or hide the overlay.
///
/// System-wide because the whole point is pressing it while the game has
/// focus, and a game has keyboard focus absolutely. Registered against the
/// main window's handle, which already exists and is already pumping
/// messages, so this needs no window of its own.
///
/// Read-only as far as the game is concerned: Windows delivers the key to us
/// and nothing is injected, hooked or sent anywhere.
/// </summary>
public sealed class Hotkey : IDisposable
{
    private const int WmHotkey = 0x0312;
    private const uint ModAlt = 0x0001;
    private const uint ModControl = 0x0002;
    private const uint ModNoRepeat = 0x4000;
    private const uint VkS = 0x53;

    public const string Description = "Ctrl+Alt+S";

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint modifiers, uint vk);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    private readonly IntPtr _hwnd;
    private readonly HwndSource _source;
    private readonly Action _pressed;
    private readonly int _id = 0x5157;   // anything unique within this process
    private bool _registered;

    private Hotkey(HwndSource source, Action pressed)
    {
        _source = source;
        _hwnd = source.Handle;
        _pressed = pressed;
        _source.AddHook(Hook);
    }

    /// <summary>
    /// Claim the hotkey, or report that something else already has it.
    ///
    /// Another app holding Ctrl+Alt+S is ordinary and not an error worth a
    /// dialog - the overlay still works, it just has no shortcut.
    /// </summary>
    public static Hotkey? Register(IntPtr windowHandle, Action pressed)
    {
        if (HwndSource.FromHwnd(windowHandle) is not { } source) return null;

        var hotkey = new Hotkey(source, pressed);
        // NOREPEAT: holding the key down must toggle once, not forty times.
        hotkey._registered = RegisterHotKey(
            windowHandle, hotkey._id, ModControl | ModAlt | ModNoRepeat, VkS);

        if (hotkey._registered) return hotkey;
        hotkey.Dispose();
        return null;
    }

    private IntPtr Hook(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg != WmHotkey || wParam.ToInt32() != _id) return IntPtr.Zero;
        handled = true;
        _pressed();
        return IntPtr.Zero;
    }

    public void Dispose()
    {
        _source.RemoveHook(Hook);
        if (_registered) UnregisterHotKey(_hwnd, _id);
        _registered = false;
    }
}
