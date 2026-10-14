using System.Windows;
using System.Windows.Interop;
using SpikeSight.Core.Windows;

namespace SpikeSight.App;

public partial class OverlayWindow : Window
{
    public OverlayWindow()
    {
        InitializeComponent();
        // The styles have to go on once the handle exists, which for WPF is
        // SourceInitialized - before the window is ever shown, so nothing is
        // on screen in the wrong shape even for a frame. That is the whole
        // grey-flash bug the browser version had, gone by construction.
        SourceInitialized += (_, _) => Win32.StyleAsOverlay(Handle);
    }

    public IntPtr Handle => new WindowInteropHelper(this).Handle;

    public void Say(string text) => Status.Text = text;

    /// <summary>Top-right of the working area, like the panel it replaces.</summary>
    public void PlaceTopRight()
    {
        var work = SystemParameters.WorkArea;
        Left = work.Right - Width - 14;
        Top = work.Top + work.Height * 0.13;
    }
}
