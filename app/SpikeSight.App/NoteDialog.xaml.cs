using System.Windows;
using SpikeSight.Core.Store;

namespace SpikeSight.App;

public partial class NoteDialog : Window
{
    public Severity Severity { get; private set; } = Severity.Watch;
    public string Body { get; private set; } = "";
    public IReadOnlyList<string> Tags { get; private set; } = [];

    /// <param name="who">Whatever the board calls them - a riot ID, or the
    /// local code for somebody in Streamer Mode. Never resolved just for this.</param>
    /// <param name="existing">What is already on them, so this edits the flag
    /// rather than quietly stacking a second one underneath it.</param>
    public NoteDialog(string who, string context, PlayerNote? existing = null)
    {
        InitializeComponent();
        Who.Text = who;
        Context.Text = context;

        if (existing is not null)
        {
            Title = "Edit flag";
            SaveButton.Content = "Update flag";
            SevWatch.IsChecked = existing.Severity == Severity.Watch;
            SevAvoid.IsChecked = existing.Severity == Severity.Avoid;
            SevDodge.IsChecked = existing.Severity == Severity.Dodge;
            BodyBox.Text = existing.Text;
            TagsBox.Text = string.Join(", ", existing.Tags);
        }

        Loaded += (_, _) =>
        {
            BodyBox.Focus();
            BodyBox.CaretIndex = BodyBox.Text.Length;
        };
    }

    private void Save(object sender, RoutedEventArgs e)
    {
        Severity = SevDodge.IsChecked == true ? Severity.Dodge
                 : SevAvoid.IsChecked == true ? Severity.Avoid
                 : Severity.Watch;

        Body = BodyBox.Text.Trim();
        Tags = TagsBox.Text
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            // Tags are stored comma-separated, so a comma inside one would
            // split it in half on the way back out.
            .Select(tag => tag.Replace(',', ' ').Trim())
            .Where(tag => tag.Length > 0)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        DialogResult = true;
    }
}
