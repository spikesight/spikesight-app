using System.IO;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SpikeSight.App;

/// <summary>
/// Agent portraits, shipped with the app.
///
/// The same 72px images the web build used, embedded as resources - so the
/// running app still loads nothing from anywhere, and a row shows who somebody
/// is picking rather than a word.
/// </summary>
public static class Portraits
{
    private static readonly Dictionary<string, ImageSource?> Cache = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>The portrait for a character id, or null when we have none.</summary>
    public static ImageSource? For(string? characterId)
    {
        var key = (characterId ?? "").Replace("-", "").ToLowerInvariant();
        if (key.Length == 0) return null;

        lock (Cache)
        {
            if (Cache.TryGetValue(key, out var cached)) return cached;

            ImageSource? image = null;
            try
            {
                var uri = new Uri($"pack://application:,,,/Assets/Agents/{key}.png", UriKind.Absolute);
                var bitmap = new BitmapImage();
                bitmap.BeginInit();
                bitmap.UriSource = uri;
                bitmap.CacheOption = BitmapCacheOption.OnLoad;
                bitmap.EndInit();
                bitmap.Freeze();
                image = bitmap;
            }
            catch (Exception ex) when (ex is IOException or NotSupportedException or UriFormatException)
            {
                // An agent that shipped after this build. The row still reads;
                // it just has no face on it.
            }

            Cache[key] = image;
            return image;
        }
    }
}
