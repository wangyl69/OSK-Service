using HybridSedPrototype.SemanticEventDispatcher.Models;

namespace HybridSedPrototype.SemanticEventDispatcher.Inputs;

/// <summary>Only marked OSK key-down edges may request haptic playback.</summary>
public static class HapticInputGate
{
    public const ulong OskExtraInfoMarker = 0x5345445F4F534B;

    // Ignored edges need no semantic-event allocations. Voice still needs both
    // edges from every source, independently of the haptic marker.
    public static bool NeedsClassification(int virtualKey, int voiceKey, bool isKeyDown, ulong extraInfo)
        => virtualKey == voiceKey || (isKeyDown && extraInfo == OskExtraInfoMarker);

    public static bool ShouldRoute(SemanticEventType eventType, bool isKeyDown, ulong extraInfo)
        => eventType != SemanticEventType.Haptic ||
           (isKeyDown && extraInfo == OskExtraInfoMarker);
}
