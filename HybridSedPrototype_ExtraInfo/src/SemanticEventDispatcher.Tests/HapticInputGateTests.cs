using HybridSedPrototype.SemanticEventDispatcher.Inputs;
using HybridSedPrototype.SemanticEventDispatcher.Models;
using Xunit;

namespace HybridSedPrototype.SemanticEventDispatcher.Tests;

public sealed class HapticInputGateTests
{
    [Theory]
    [InlineData(134, false, false, false)]
    [InlineData(134, true, false, false)]
    [InlineData(134, false, true, false)]
    [InlineData(134, true, true, true)]
    [InlineData(135, false, false, true)]
    [InlineData(135, true, false, true)]
    [InlineData(135, false, true, true)]
    [InlineData(135, true, true, true)]
    public void Early_filter_preserves_voice_edges_and_marked_haptic_down(int key, bool down, bool marked, bool expected)
        => Assert.Equal(expected, HapticInputGate.NeedsClassification(key, 135, down,
            marked ? HapticInputGate.OskExtraInfoMarker : 0));

    [Theory]
    [InlineData(true, true, true)]
    [InlineData(false, true, false)]
    [InlineData(true, false, false)]
    [InlineData(false, false, false)]
    public void Haptics_require_marked_keydown(bool down, bool marked, bool expected)
        => Assert.Equal(expected, HapticInputGate.ShouldRoute(SemanticEventType.Haptic,
            down, marked ? HapticInputGate.OskExtraInfoMarker : 0));

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void Voice_keeps_both_edges_without_marker(bool down)
        => Assert.True(HapticInputGate.ShouldRoute(SemanticEventType.Voice, down, 0));
}
