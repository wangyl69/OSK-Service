namespace HybridSedPrototype.SemanticEventDispatcher.Models;

/// <summary>
/// Where a semantic event came from.
/// Only LowLevelKeyboardHook is implemented.
/// Planned sources that are not implemented: physical keyboard, OSK,
/// touchscreen, touchpad, and pen.
/// The hook currently receives both physical and injected keys.
/// SemanticEvent.IsInjected is the bit to use when those two are split later.
/// </summary>
public enum InputSource
{
    LowLevelKeyboardHook = 1
}
