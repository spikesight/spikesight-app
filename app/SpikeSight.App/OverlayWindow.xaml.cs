using System.Windows;
using System.Windows.Interop;
using SpikeSight.Core.Windows;

namespace SpikeSight.App;

public partial class OverlayWindow : Window
{
    public OverlayWindow()
    {
        InitializeComponent();
        // Styles go on once the handle exists, which for WPF is
        // SourceInitialized - before the window is ever shown, so it is never
        // on screen in the wrong shape even for a frame. That is the grey
        // flash the browser version had, gone by construction.
        SourceInitialized += (_, _) => Win32.StyleAsOverlay(Handle);

        // Build the handle now rather than at first Show. Until it exists the
        // window is unstyled, and anything asking whether it is click-through
        // gets told "no" because there is nothing to ask about yet.
        new WindowInteropHelper(this).EnsureHandle();
    }

    public IntPtr Handle => new WindowInteropHelper(this).Handle;

    /// <summary>
    /// Show the lobby, or nothing at all.
    ///
    /// The panel belongs over agent select and nowhere else. It is not a
    /// status bar: sitting in menus it has nothing to say, and during a round
    /// it is something drawn over the game for no reason.
    /// </summary>
    public void ShowLobby(string heading, IReadOnlyList<Row> rows)
    {
        Heading.Text = heading.ToUpperInvariant();
        Rows.ItemsSource = rows;
        if (!IsVisible) Show();
    }

    public void HidePanel()
    {
        if (IsVisible) Hide();
    }

    /// <summary>Top-right of the working area, clear of the agent grid.</summary>
    public void PlaceTopRight()
    {
        var work = SystemParameters.WorkArea;
        Left = work.Right - Width - 14;
        Top = work.Top + work.Height * 0.13;
    }
}
