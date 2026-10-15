using SpikeSight.Core;

namespace SpikeSight.Tests;

public class UpdateCheckTests
{
    [Theory]
    [InlineData("3.0.1", "3.0.0", true)]
    [InlineData("3.0.0", "3.0.1", false)]
    [InlineData("3.0.0", "3.0.0", false)]
    [InlineData("3.1.0", "3.0.9", true)]
    [InlineData("4.0.0", "3.9.9", true)]
    public void Later_versions_win(string candidate, string current, bool newer)
        => Assert.Equal(newer, UpdateCheck.IsNewer(candidate, current));

    [Fact]
    public void Numbers_are_compared_as_numbers()
    {
        // The one a string comparison gets wrong: "3.0.10" < "3.0.9" as text.
        Assert.True(UpdateCheck.IsNewer("3.0.10", "3.0.9"));
        Assert.False(UpdateCheck.IsNewer("3.0.9", "3.0.10"));
    }

    [Fact]
    public void A_leading_v_is_ignored()
    {
        // Tags are written v3.0.1; the running version is not.
        Assert.True(UpdateCheck.IsNewer("v3.0.1", "3.0.0"));
        Assert.False(UpdateCheck.IsNewer("v3.0.0", "3.0.0"));
    }

    [Fact]
    public void A_release_beats_a_preview_of_itself()
    {
        // This is what gets 3.0.0-preview onto 3.0.0.
        Assert.True(UpdateCheck.IsNewer("3.0.0", "3.0.0-preview"));
        Assert.False(UpdateCheck.IsNewer("3.0.0-preview", "3.0.0"));
    }

    [Fact]
    public void Missing_parts_count_as_zero()
    {
        Assert.True(UpdateCheck.IsNewer("3.1", "3.0.9"));
        Assert.False(UpdateCheck.IsNewer("3.0", "3.0.0"));
    }

    [Fact]
    public void Nonsense_does_not_claim_an_update()
    {
        Assert.False(UpdateCheck.IsNewer("not-a-version", "3.0.0"));
        Assert.False(UpdateCheck.IsNewer("", "3.0.0"));
    }

    [Fact]
    public async Task A_failed_check_is_simply_no_update()
    {
        // Not knowing is the same as there not being one; it must never
        // interrupt anybody.
        using var http = new HttpClient(new FailingHandler()) { Timeout = TimeSpan.FromSeconds(1) };

        var info = await new UpdateCheck(http).CheckAsync();

        Assert.False(info.Available);
        Assert.Null(info.Latest);
        Assert.NotEmpty(info.Current);
    }

    private sealed class FailingHandler : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
            => throw new HttpRequestException("no network");
    }
}
