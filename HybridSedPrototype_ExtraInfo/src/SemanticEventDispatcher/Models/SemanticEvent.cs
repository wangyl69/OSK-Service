using HybridSedPrototype.Contracts;

namespace HybridSedPrototype.SemanticEventDispatcher.Models;

/// <summary>
/// One classified input. VoiceEvent is set only for voice events.
/// HapticEvent is set only for haptic events.
/// A single key can produce more than one SemanticEvent.
/// The voice key produces both a voice event and a special-key haptic event.
/// </summary>
public sealed class SemanticEvent
{
    // Internal diagnostic context, never part of the pipe/WMI wire contract.
    internal long HookReceivedTicks { get; set; }

    public SemanticEventType EventType { get; init; }

    /// <summary>
    /// Which input path produced this event. Routing does not branch on it yet.
    /// </summary>
    public InputSource Source { get; init; }

    /// <summary>
    /// True when the low-level hook reported LLKHF_INJECTED or
    /// LLKHF_LOWER_IL_INJECTED. On-screen keyboards often set this.
    /// Routing does not use it yet.
    /// </summary>
    public bool IsInjected { get; init; }

    public VoiceEventType? VoiceEvent { get; init; }

    public HapticEventType? HapticEvent { get; init; }
}
