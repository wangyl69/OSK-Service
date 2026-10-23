using HybridSedPrototype.Contracts;
using HybridSedPrototype.SemanticEventDispatcher.Configuration;
using HybridSedPrototype.SemanticEventDispatcher.Models;
using Microsoft.Extensions.Options;

namespace HybridSedPrototype.SemanticEventDispatcher.Inputs;

/// <summary>
/// Maps a virtual-key transition to zero or more semantic events.
/// Key sets come from DispatcherOptions. The rules themselves are unchanged:
/// the voice key emits voice plus a special-key haptic, modifiers and special
/// keys emit their haptic type, and every other key emits a normal-key haptic.
/// Key-up and key-down both produce haptic events. Only the voice event
/// differs between down and up.
/// </summary>
public sealed class InputEventClassifier
{
    private readonly int _voiceKey;
    private readonly HashSet<int> _modifierKeys;
    private readonly HashSet<int> _specialKeys;
    private readonly ILogger<InputEventClassifier> _logger;

    public InputEventClassifier(
        IOptions<DispatcherOptions> options,
        ILogger<InputEventClassifier> logger)
    {
        ArgumentNullException.ThrowIfNull(options);
        DispatcherOptions value = options.Value;

        _voiceKey = value.VoiceKeyVirtualKey;
        _modifierKeys = (value.ModifierKeys ?? []).ToHashSet();
        _specialKeys = (value.SpecialKeys ?? []).ToHashSet();
        _logger = logger;
    }

    public IReadOnlyList<SemanticEvent> Classify(
        int virtualKey,
        bool isKeyDown,
        InputSource source,
        bool isInjected)
    {
        if (_logger.IsEnabled(LogLevel.Debug)) _logger.LogDebug(
            "Classify VK={VirtualKey} Down={IsKeyDown} Source={Source} Injected={IsInjected}",
            virtualKey,
            isKeyDown,
            source,
            isInjected);

        var semanticEvents = new List<SemanticEvent>();

        if (virtualKey == _voiceKey)
        {
            semanticEvents.Add(new SemanticEvent
            {
                EventType = SemanticEventType.Voice,
                Source = source,
                IsInjected = isInjected,
                VoiceEvent = isKeyDown
                    ? VoiceEventType.RecordingStart
                    : VoiceEventType.RecordingStop
            });

            // The voice key is also a distinct keypress for haptics.
            semanticEvents.Add(
                CreateHapticEvent(HapticEventType.SpecialKey, source, isInjected));
        }

        if (_modifierKeys.Contains(virtualKey))
        {
            semanticEvents.Add(
                CreateHapticEvent(HapticEventType.ModifierKey, source, isInjected));
        }

        if (_specialKeys.Contains(virtualKey))
        {
            semanticEvents.Add(
                CreateHapticEvent(HapticEventType.SpecialKey, source, isInjected));
        }

        if (semanticEvents.Count == 0)
        {
            semanticEvents.Add(
                CreateHapticEvent(HapticEventType.NormalKey, source, isInjected));
        }

        return semanticEvents;
    }

    private static SemanticEvent CreateHapticEvent(
        HapticEventType hapticEventType,
        InputSource source,
        bool isInjected)
    {
        return new SemanticEvent
        {
            EventType = SemanticEventType.Haptic,
            Source = source,
            IsInjected = isInjected,
            HapticEvent = hapticEventType
        };
    }
}
