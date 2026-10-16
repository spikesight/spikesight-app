using System.Windows;
using System.Windows.Media;

namespace SpikeSight.App;

/// <summary>
/// Light and dark, without a restart.
///
/// Theme.xaml holds the dark set. Light mode merges Theme.Light.xaml over the
/// top, which is why every control that uses a themed colour asks for it with
/// DynamicResource rather than StaticResource - a StaticResource is resolved
/// once at load and would never notice.
///
/// Recolouring the dark brushes in place would have been a smaller change and
/// does not work: WPF freezes the Freezables in a compiled resource
/// dictionary, and freezes them again on the way back in, so they cannot be
/// changed at all.
///
/// Only the neutrals have a light set. The accents - rank tiers, party rings,
/// severity, win and loss - are mid-tone and saturated and read on either
/// background; a second set would be two palettes to keep honest for no gain.
/// </summary>
public static class Theme
{
    private static readonly Uri LightUri =
        new("pack://application:,,,/Theme.Light.xaml", UriKind.Absolute);

    private static ResourceDictionary? _light;

    public static bool IsLight { get; private set; }

    public static void Apply(bool light)
    {
        if (Application.Current?.Resources.MergedDictionaries is not { } merged) return;
        IsLight = light;

        if (light)
        {
            // Appended, so it wins over Theme.xaml for the keys it redefines
            // and leaves every other key alone.
            _light ??= new ResourceDictionary { Source = LightUri };
            if (!merged.Contains(_light)) merged.Add(_light);
        }
        else if (_light is not null)
        {
            merged.Remove(_light);
        }
    }

    /// <summary>A theme colour as a WinForms colour, for the tray menu.</summary>
    public static System.Drawing.Color Swatch(string key)
    {
        if (Application.Current?.TryFindResource(key) is not SolidColorBrush brush)
            return System.Drawing.Color.Black;
        var c = brush.Color;
        return System.Drawing.Color.FromArgb(c.R, c.G, c.B);
    }
}
