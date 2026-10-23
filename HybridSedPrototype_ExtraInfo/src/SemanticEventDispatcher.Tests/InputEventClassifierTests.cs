using HybridSedPrototype.Contracts;
using HybridSedPrototype.SemanticEventDispatcher.Configuration;
using HybridSedPrototype.SemanticEventDispatcher.Inputs;
using HybridSedPrototype.SemanticEventDispatcher.Models;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace HybridSedPrototype.SemanticEventDispatcher.Tests;

public sealed class InputEventClassifierTests
{
    [Fact]
    public void Default_voice_key_remains_F12()
    {
        Assert.Equal(0x7B, new DispatcherOptions().VoiceKeyVirtualKey);
    }

    [Fact]
    public void F12_down_emits_recording_start_and_special_haptic()
    {
        IReadOnlyList<SemanticEvent> events = Create().Classify(
            virtualKey: 0x7B,
            isKeyDown: true,
            source: InputSource.LowLevelKeyboardHook,
            isInjected: false);

        Assert.Equal(2, events.Count);

        Assert.Equal(SemanticEventType.Voice, events[0].EventType);
        Assert.Equal(VoiceEventType.RecordingStart, events[0].VoiceEvent);
        Assert.Equal(InputSource.LowLevelKeyboardHook, events[0].Source);

        Assert.Equal(SemanticEventType.Haptic, events[1].EventType);
        Assert.Equal(HapticEventType.SpecialKey, events[1].HapticEvent);
    }

    [Fact]
    public void F12_up_emits_recording_stop_and_special_haptic()
    {
        IReadOnlyList<SemanticEvent> events = Create().Classify(
            virtualKey: 0x7B,
            isKeyDown: false,
            source: InputSource.LowLevelKeyboardHook,
            isInjected: true);

        Assert.Equal(VoiceEventType.RecordingStop, events[0].VoiceEvent);
        Assert.True(events[0].IsInjected);
        Assert.Equal(HapticEventType.SpecialKey, events[1].HapticEvent);
        Assert.True(events[1].IsInjected);
    }

    [Theory]
    [InlineData(0x10)]
    [InlineData(0x11)]
    [InlineData(0x12)]
    [InlineData(0xA0)]
    [InlineData(0xA5)]
    public void Modifier_keys_emit_only_modifier_haptic(int virtualKey)
    {
        IReadOnlyList<SemanticEvent> events = Create().Classify(
            virtualKey,
            isKeyDown: true,
            InputSource.LowLevelKeyboardHook,
            isInjected: false);

        SemanticEvent haptic = Assert.Single(events);
        Assert.Equal(HapticEventType.ModifierKey, haptic.HapticEvent);
    }

    [Theory]
    [InlineData(0x20)]
    [InlineData(0x0D)]
    [InlineData(0x08)]
    public void Space_enter_and_backspace_emit_special_haptic(int virtualKey)
    {
        IReadOnlyList<SemanticEvent> events = Create().Classify(
            virtualKey,
            isKeyDown: false,
            InputSource.LowLevelKeyboardHook,
            isInjected: false);

        SemanticEvent haptic = Assert.Single(events);
        Assert.Equal(HapticEventType.SpecialKey, haptic.HapticEvent);
    }

    [Fact]
    public void Other_keys_emit_normal_haptic_on_down_and_up()
    {
        InputEventClassifier classifier = Create();

        SemanticEvent down = Assert.Single(classifier.Classify(
            0x41,
            isKeyDown: true,
            InputSource.LowLevelKeyboardHook,
            isInjected: false));
        SemanticEvent up = Assert.Single(classifier.Classify(
            0x41,
            isKeyDown: false,
            InputSource.LowLevelKeyboardHook,
            isInjected: false));

        Assert.Equal(HapticEventType.NormalKey, down.HapticEvent);
        Assert.Equal(HapticEventType.NormalKey, up.HapticEvent);
    }

    [Fact]
    public void Configured_F24_becomes_the_voice_key_and_F12_becomes_normal()
    {
        InputEventClassifier classifier = Create(new DispatcherOptions
        {
            VoiceKeyVirtualKey = 0x87
        });

        IReadOnlyList<SemanticEvent> f24 = classifier.Classify(
            0x87,
            isKeyDown: true,
            InputSource.LowLevelKeyboardHook,
            isInjected: true);
        SemanticEvent f12 = Assert.Single(classifier.Classify(
            0x7B,
            isKeyDown: true,
            InputSource.LowLevelKeyboardHook,
            isInjected: false));

        Assert.Equal(VoiceEventType.RecordingStart, f24[0].VoiceEvent);
        Assert.Equal(HapticEventType.SpecialKey, f24[1].HapticEvent);
        Assert.Equal(HapticEventType.NormalKey, f12.HapticEvent);
    }

    [Fact]
    public void Voice_key_that_is_also_a_modifier_emits_both_haptic_types()
    {
        InputEventClassifier classifier = Create(new DispatcherOptions
        {
            VoiceKeyVirtualKey = 0x10
        });

        IReadOnlyList<SemanticEvent> events = classifier.Classify(
            0x10,
            isKeyDown: true,
            InputSource.LowLevelKeyboardHook,
            isInjected: false);

        Assert.Equal(3, events.Count);
        Assert.Equal(VoiceEventType.RecordingStart, events[0].VoiceEvent);
        Assert.Equal(HapticEventType.SpecialKey, events[1].HapticEvent);
        Assert.Equal(HapticEventType.ModifierKey, events[2].HapticEvent);
    }

    private static InputEventClassifier Create(DispatcherOptions? options = null)
    {
        return new InputEventClassifier(
            Options.Create(options ?? new DispatcherOptions()),
            NullLogger<InputEventClassifier>.Instance);
    }
}
