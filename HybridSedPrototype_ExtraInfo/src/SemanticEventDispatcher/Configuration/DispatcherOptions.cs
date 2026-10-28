using HybridSedPrototype.Contracts;

namespace HybridSedPrototype.SemanticEventDispatcher.Configuration;

/// <summary>
/// Runtime settings for key classification and the two existing outputs.
/// Missing appsettings values fall back to the defaults below, which match
/// the previous hardcoded prototype behavior.
/// </summary>
public sealed class DispatcherOptions
{
    public const string SectionName = "Dispatcher";

    /// <summary>
    /// Virtual-key code that starts and stops voice recording.
    /// Default is F12 (0x7B / 123) so the existing development flow keeps working.
    /// Set this to 135 (F24, VK 0x87) when the OSK voice button sends F24.
    /// </summary>
    public int VoiceKeyVirtualKey { get; set; } = 0x7B;

    /// <summary>
    /// Virtual-key codes that produce a modifier haptic event.
    /// </summary>
    public int[] ModifierKeys { get; set; } =
    [
        0x10, // VK_SHIFT
        0x11, // VK_CONTROL
        0x12, // VK_MENU
        0xA0, // VK_LSHIFT
        0xA1, // VK_RSHIFT
        0xA2, // VK_LCONTROL
        0xA3, // VK_RCONTROL
        0xA4, // VK_LMENU
        0xA5  // VK_RMENU
    ];

    /// <summary>
    /// Virtual-key codes that produce a special-key haptic event.
    /// Space, Enter, and Backspace.
    /// </summary>
    public int[] SpecialKeys { get; set; } =
    [
        0x20, // VK_SPACE
        0x0D, // VK_RETURN
        0x08  // VK_BACK
    ];

    public string HapticPipeName { get; set; } = HapticPipeProtocol.PipeName;

    /// <summary>
    /// Combined connection/write budget in the dedicated native-event sender.
    /// Cancellation is joined before reusing memory. The hook only queues.
    /// </summary>
    public int HapticPipeConnectTimeoutMs { get; set; } = 20;

    /// <summary>Enable configured priority for the hook and sender only; false restores Normal.</summary>
    public bool LatencyThreadPriorityBoost { get; set; } = true;
    /// <summary>normal, above-normal, or highest; process priority is unchanged.</summary>
    public string LatencyThreadPriority { get; set; } = "highest";

    /// <summary>Optional local memory-map name for bounded per-event timing. Empty disables diagnostics.</summary>
    public string HapticLatencyTraceName { get; set; } = "";

    /// <summary>Use a Windows event to wake the sender; false restores the previous BlockingCollection wait.</summary>
    public bool HapticSenderNativeWake { get; set; } = true;

    public string WmiNamespace { get; set; } = VoiceWmiProtocol.NamespacePath;

    public string WmiClassName { get; set; } = VoiceWmiProtocol.ClassName;

    public string WmiSource { get; set; } = VoiceWmiProtocol.DefaultSource;
}
