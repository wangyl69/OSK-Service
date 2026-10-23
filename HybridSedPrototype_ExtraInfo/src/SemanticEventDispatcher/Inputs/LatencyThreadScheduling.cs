using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security;

namespace HybridSedPrototype.SemanticEventDispatcher.Inputs;

internal static class LatencyThreadScheduling
{
    // Applies only to the current dedicated thread, never to the process or pool.
    public static void Apply(bool boost, ILogger logger, string role)
    {
        var requested = boost ? ThreadPriority.AboveNormal : ThreadPriority.Normal;
        try { Thread.CurrentThread.Priority = requested; }
        catch (Exception ex) when (ex is Win32Exception or SecurityException or ThreadStateException)
        {
            logger.LogWarning(ex, "Could not apply latency thread priority for {Role}; keeping current priority", role);
        }
        logger.LogInformation("Latency thread Role={Role} Win32Id={ThreadId} Requested={Requested} Effective={Effective}",
            role, GetCurrentThreadId(), requested, Thread.CurrentThread.Priority);
    }

    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();
}
