namespace SpikeSight.Core;

/// <summary>Base for every error SpikeSight raises deliberately.</summary>
public class SpikeSightException : Exception
{
    /// <summary>Short, user-facing line for the status bar.</summary>
    public string Hint { get; init; } = "";

    public SpikeSightException(string message, Exception? inner = null) : base(message, inner) { }
}

/// <summary>The Riot Client is not running, or nobody is signed into it.</summary>
public sealed class ClientNotRunningException : SpikeSightException
{
    public ClientNotRunningException(string message, Exception? inner = null) : base(message, inner)
        => Hint = "Start the Riot Client (and VALORANT) and this will connect automatically.";
}

/// <summary>A pd/glz endpoint answered with something we cannot use.</summary>
public sealed class RemoteException : SpikeSightException
{
    public int? Status { get; }

    public RemoteException(string message, int? status = null, Exception? inner = null)
        : base(message, inner) => Status = status;
}
