using System.Drawing;
using System.Windows.Forms;

namespace SpikeSight.App;

/// <summary>
/// The notification-area icon.
///
/// <see cref="NotifyIcon"/> ships with the framework, so this costs a
/// reference rather than a dependency - and it is about forty lines against
/// the three hundred the Python build needed to hand-roll one.
/// </summary>
public sealed class TrayIcon : IDisposable
{
    private readonly NotifyIcon _icon;

    public TrayIcon(Action show, Action quit)
    {
        var menu = new ContextMenuStrip();
        menu.Items.Add("Show SpikeSight", null, (_, _) => show());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Quit", null, (_, _) => quit());

        _icon = new NotifyIcon
        {
            Icon = Reticle(),
            Text = "SpikeSight",
            Visible = true,
            ContextMenuStrip = menu,
        };
        _icon.DoubleClick += (_, _) => show();
    }

    public void Notify(string title, string text)
    {
        _icon.BalloonTipTitle = title;
        _icon.BalloonTipText = text;
        _icon.ShowBalloonTip(4000);
    }

    /// <summary>
    /// The app's mark, drawn rather than shipped: a red ring with a dot, which
    /// still reads at 16 pixels where anything more detailed turns to mush.
    /// </summary>
    private static Icon Reticle()
    {
        using var bitmap = new Bitmap(32, 32);
        using (var art = Graphics.FromImage(bitmap))
        {
            art.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            art.Clear(Color.Transparent);
            using var ring = new Pen(Color.FromArgb(255, 70, 85), 3.5f);
            art.DrawEllipse(ring, 6, 6, 20, 20);
            using var dot = new SolidBrush(Color.FromArgb(230, 233, 239));
            art.FillEllipse(dot, 14, 14, 4, 4);
        }
        return Icon.FromHandle(bitmap.GetHicon());
    }

    public void Dispose()
    {
        _icon.Visible = false;
        _icon.Dispose();
    }
}
