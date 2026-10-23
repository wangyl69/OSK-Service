using System.IO.Pipes;
using HybridSedPrototype.Contracts;
using HybridSedPrototype.SemanticEventDispatcher.Configuration;
using HybridSedPrototype.SemanticEventDispatcher.Models;
using HybridSedPrototype.SemanticEventDispatcher.Publishers;
using HybridSedPrototype.SemanticEventDispatcher.Routing;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace HybridSedPrototype.SemanticEventDispatcher.Tests;

public sealed class RoutingAndPipeTests
{
    [Fact]
    public void Missing_service_does_not_block_hook_side_publish()
    {
        var watch = System.Diagnostics.Stopwatch.StartNew();
        using var publisher = new HapticPipePublisher(
            Options.Create(new DispatcherOptions
            {
                HapticPipeName = "Missing" + Guid.NewGuid().ToString("N"),
                HapticPipeConnectTimeoutMs = 1000
            }), NullLogger<HapticPipePublisher>.Instance);
        for (int i = 0; i < 100; i++)
            publisher.Publish(new SemanticEvent
            {
                EventType = SemanticEventType.Haptic,
                HapticEvent = HapticEventType.NormalKey
            });
        Assert.True(watch.ElapsedMilliseconds < 500,
            "Hook-side enqueue must not wait for the absent pipe server.");
    }

    [Fact]
    public void Router_forwards_one_event_to_every_publisher_in_order()
    {
        var first = new RecordingPublisher();
        var second = new RecordingPublisher();
        var router = new SemanticEventRouter(
            [first, second],
            NullLogger<SemanticEventRouter>.Instance);

        var semanticEvent = new SemanticEvent
        {
            EventType = SemanticEventType.Haptic,
            HapticEvent = HapticEventType.NormalKey,
            Source = InputSource.LowLevelKeyboardHook
        };

        router.Route(semanticEvent);

        Assert.Same(semanticEvent, Assert.Single(first.Events));
        Assert.Same(semanticEvent, Assert.Single(second.Events));
    }

    [Fact]
    public void Router_stops_after_a_publisher_throws()
    {
        var second = new RecordingPublisher();
        var router = new SemanticEventRouter(
            [new ThrowingPublisher(), second],
            NullLogger<SemanticEventRouter>.Instance);

        Assert.Throws<InvalidOperationException>(() =>
            router.Route(new SemanticEvent
            {
                EventType = SemanticEventType.Voice,
                VoiceEvent = VoiceEventType.RecordingStart
            }));

        Assert.Empty(second.Events);
    }

    [Fact]
    public void Voice_publisher_ignores_haptic_events_without_calling_wmi()
    {
        using var publisher = new WmiVoicePublisher(
            Options.Create(new DispatcherOptions()),
            NullLogger<WmiVoicePublisher>.Instance);

        publisher.Publish(new SemanticEvent
        {
            EventType = SemanticEventType.Haptic,
            HapticEvent = HapticEventType.NormalKey
        });
    }

    [Fact]
    public void Voice_publisher_rejects_a_voice_event_without_a_payload()
    {
        using var publisher = new WmiVoicePublisher(
            Options.Create(new DispatcherOptions()),
            NullLogger<WmiVoicePublisher>.Instance);

        Assert.Throws<InvalidOperationException>(() =>
            publisher.Publish(new SemanticEvent
            {
                EventType = SemanticEventType.Voice
            }));
    }

    [Fact]
    public void Haptic_publisher_drops_events_when_no_consumer_is_listening()
    {
        using var publisher = new HapticPipePublisher(
            Options.Create(new DispatcherOptions
            {
                HapticPipeName = "HybridSedHapticMissing" + Guid.NewGuid().ToString("N"),
                HapticPipeConnectTimeoutMs = 50
            }),
            NullLogger<HapticPipePublisher>.Instance);

        publisher.Publish(new SemanticEvent
        {
            EventType = SemanticEventType.Haptic,
            HapticEvent = HapticEventType.NormalKey
        });
    }

    [Fact]
    public void Haptic_publisher_writes_the_shared_event_code()
    {
        string pipeName = "sedh" + Guid.NewGuid().ToString("N");

        using var server = new NamedPipeServerStream(
            pipeName,
            PipeDirection.In,
            1,
            PipeTransmissionMode.Byte);

        int received = -1;
        Exception? readerError = null;

        var reader = new Thread(() =>
        {
            try
            {
                server.WaitForConnection();
                received = server.ReadByte();
            }
            catch (Exception ex)
            {
                readerError = ex;
            }
        })
        {
            IsBackground = true,
            Name = "Haptic pipe test reader"
        };

        reader.Start();

        HapticPipePublisher? publisher = null;
        try
        {
            publisher = new HapticPipePublisher(
                Options.Create(new DispatcherOptions
                {
                    HapticPipeName = pipeName,
                    HapticPipeConnectTimeoutMs = 2000
                }),
                NullLogger<HapticPipePublisher>.Instance);

            // Reserved touchpad events must not reach the pipe or occupy the
            // queue ahead of a supported keyboard event.
            publisher.Publish(new SemanticEvent
            {
                EventType = SemanticEventType.Haptic,
                HapticEvent = HapticEventType.TouchPad
            });
            publisher.Publish(new SemanticEvent
            {
                EventType = SemanticEventType.Haptic,
                Source = InputSource.LowLevelKeyboardHook,
                HapticEvent = HapticEventType.SpecialKey
            });

            Assert.True(
                reader.Join(TimeSpan.FromSeconds(3)),
                "Timed out waiting for the haptic pipe byte.");
            Assert.Null(readerError?.ToString());
            Assert.Equal((byte)HapticEventType.SpecialKey, received);
        }
        finally
        {
            publisher?.Dispose();

            if (reader.IsAlive)
            {
                try
                {
                    using var unblock = new NamedPipeClientStream(
                        ".",
                        pipeName,
                        PipeDirection.Out);
                    unblock.Connect(200);
                }
                catch (Exception)
                {
                    // The reader may already have finished.
                }

                reader.Join(TimeSpan.FromSeconds(2));
            }
        }
    }

    [Theory]
    [InlineData(HapticEventType.NormalKey, 1)]
    [InlineData(HapticEventType.ModifierKey, 2)]
    [InlineData(HapticEventType.SpecialKey, 3)]
    [InlineData(HapticEventType.TouchPad, 4)]
    public void Haptic_wire_codes_stay_stable(HapticEventType type, byte code)
    {
        Assert.Equal(code, (byte)type);
    }

    [Fact]
    public void Voice_wmi_modes_stay_stable()
    {
        Assert.Equal(0u, VoiceWmiProtocol.RecordingStartMode);
        Assert.Equal(1u, VoiceWmiProtocol.RecordingStopMode);
        Assert.Equal(@"root\wmi", VoiceWmiProtocol.NamespacePath);
        Assert.Equal("VoiceKeyModeSwitchEvent", VoiceWmiProtocol.ClassName);
        Assert.Equal("HybridSedHaptic", HapticPipeProtocol.PipeName);
    }

    private sealed class RecordingPublisher : ISemanticEventPublisher
    {
        public List<SemanticEvent> Events { get; } = [];

        public void Publish(SemanticEvent semanticEvent)
        {
            Events.Add(semanticEvent);
        }
    }

    private sealed class ThrowingPublisher : ISemanticEventPublisher
    {
        public void Publish(SemanticEvent semanticEvent)
        {
            throw new InvalidOperationException("publisher failed");
        }
    }
}
