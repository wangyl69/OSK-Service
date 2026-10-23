namespace HybridSedPrototype.Contracts;

/// <summary>
/// Haptic semantic event and the named-pipe wire code.
/// The numeric value is the byte HapticPipePublisher writes and
/// HapticPipeConsumer reads. Do not renumber existing values.
/// TouchPad is reserved for a future touchpad input. Nothing emits it yet.
/// </summary>
public enum HapticEventType : byte
{
    NormalKey = 1,
    ModifierKey = 2,
    SpecialKey = 3,
    TouchPad = 4
}
