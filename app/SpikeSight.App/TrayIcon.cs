using System.Drawing;
using System.IO;
using System.Windows.Forms;

namespace SpikeSight.App;

/// <summary>
/// The notification-area icon.
///
/// <see cref="NotifyIcon"/> ships with the framework, so this costs a
/// reference rather than a dependency - about forty lines against the three
/// hundred the Python build spent hand-rolling one.
/// </summary>
public sealed class TrayIcon : IDisposable
{
    private readonly NotifyIcon _icon;
    private readonly ContextMenuStrip _menu;

    public TrayIcon(Action show, Action quit)
    {
        var menu = new ContextMenuStrip
        {
            // The shell menu, dressed to match: the default is a white strip
            // with a grey gutter, which looks like somebody else's app.
            Renderer = new ToolStripProfessionalRenderer(new TrayColours()),
            ShowImageMargin = false,
        };
        _menu = menu;
        menu.Items.Add(new ToolStripMenuItem("Show SpikeSight", null, (_, _) => show()));
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(new ToolStripMenuItem("Quit", null, (_, _) => quit()));

        _icon = new NotifyIcon
        {
            Icon = AppIcon(),
            Text = "SpikeSight",
            Visible = true,
            ContextMenuStrip = menu,
        };
        _icon.DoubleClick += (_, _) => show();
        Restyle();
    }

    /// <summary>Follow a theme change. The renderer reads the colours each
    /// time it paints, so only the strip's own two need setting.</summary>
    public void Restyle()
    {
        _menu.BackColor = Theme.Swatch("Surface2");
        _menu.ForeColor = Theme.Swatch("OnHigh");
    }

    public void Notify(string title, string text)
    {
        _icon.BalloonTipTitle = title;
        _icon.BalloonTipText = text;
        _icon.ShowBalloonTip(4000);
    }

    /// <summary>
    /// The shipped icon rather than one drawn at runtime.
    ///
    /// It is a real multi-resolution .ico, so Windows picks the 16px drawing
    /// for the tray instead of squashing a large one - which is the
    /// difference between a mark and a smudge at that size.
    /// </summary>
    private static Icon AppIcon()
    {
        try
        {
            var stream = System.Windows.Application.GetResourceStream(
                new Uri("pack://application:,,,/Assets/spikesight.ico"))?.Stream;
            if (stream is not null)
            {
                using (stream)
                {
                    return new Icon(stream, SystemInformation.SmallIconSize);
                }
            }
        }
        catch (Exception ex) when (ex is IOException or ArgumentException)
        {
            // Fall through: a tray icon is not worth failing to start over.
        }
        return SystemIcons.Application;
    }

    public void Dispose()
    {
        _icon.Visible = false;
        _icon.Dispose();
    }
}

/// <summary>Dark colours for the shell's own menu renderer.</summary>
internal sealed class TrayColours : ProfessionalColorTable
{
    // Read per paint rather than captured once, so a theme switch needs no
    // new menu - the shell asks the table again every time it opens.
    private static Color Surface => Theme.Swatch("Surface2");
    private static Color Hover => Theme.Swatch("Surface3");
    private static Color Edge => Theme.Swatch("Outline");

    public override Color MenuItemSelected => Hover;
    public override Color MenuItemSelectedGradientBegin => Hover;
    public override Color MenuItemSelectedGradientEnd => Hover;
    public override Color MenuItemBorder => Hover;
    public override Color MenuBorder => Edge;
    public override Color ToolStripDropDownBackground => Surface;
    public override Color ImageMarginGradientBegin => Surface;
    public override Color ImageMarginGradientMiddle => Surface;
    public override Color ImageMarginGradientEnd => Surface;
    public override Color SeparatorDark => Edge;
    public override Color SeparatorLight => Edge;
}
