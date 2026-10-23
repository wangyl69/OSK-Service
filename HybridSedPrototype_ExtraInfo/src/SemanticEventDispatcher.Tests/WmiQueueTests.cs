using System.Diagnostics;
using HybridSedPrototype.SemanticEventDispatcher.Configuration;
using HybridSedPrototype.SemanticEventDispatcher.Models;
using HybridSedPrototype.SemanticEventDispatcher.Publishers;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace HybridSedPrototype.SemanticEventDispatcher.Tests;

public sealed class WmiQueueTests
{
    [Fact]
    public void Slow_wmi_does_not_block_publish_and_retains_down_up_order()
    {
        using var entered = new ManualResetEventSlim();
        using var release = new ManualResetEventSlim();
        using var finished = new ManualResetEventSlim();
        var received = new List<uint>();
        using var publisher = Create(mode =>
        {
            received.Add(mode);
            if (received.Count == 1) { entered.Set(); release.Wait(); }
            if (received.Count == 2) finished.Set();
        });

        try
        {
            publisher.Publish(Voice(VoiceEventType.RecordingStart));
            Assert.True(entered.Wait(TimeSpan.FromSeconds(3)));
            var watch = Stopwatch.StartNew();
            publisher.Publish(Voice(VoiceEventType.RecordingStop));
            Assert.True(watch.ElapsedMilliseconds < 100,
                "An occupied WMI backend must not delay the hook-side enqueue.");
            Assert.False(finished.IsSet);
        }
        finally { release.Set(); }

        Assert.True(finished.Wait(TimeSpan.FromSeconds(3)));
        Assert.Equal(new uint[] { 0, 1 }, received);
    }

    [Fact]
    public void Failing_wmi_does_not_throw_on_hook_or_disable_later_events()
    {
        using var finished = new ManualResetEventSlim();
        int calls = 0;
        using var publisher = Create(mode =>
        {
            if (Interlocked.Increment(ref calls) == 1)
                throw new InvalidOperationException("simulated WMI failure");
            Assert.Equal(1u, mode);
            finished.Set();
        });

        publisher.Publish(Voice(VoiceEventType.RecordingStart));
        publisher.Publish(Voice(VoiceEventType.RecordingStop));

        Assert.True(finished.Wait(TimeSpan.FromSeconds(3)));
        Assert.Equal(2, calls);
    }

    [Fact]
    public void Overflow_is_bounded_preserves_retained_order_and_ends_in_latest_stop()
    {
        using var entered = new ManualResetEventSlim();
        using var release = new ManualResetEventSlim();
        using var finished = new ManualResetEventSlim();
        var received = new List<uint>();
        using var publisher = Create(mode =>
        {
            received.Add(mode);
            if (received.Count == 1) { entered.Set(); release.Wait(); }
            if (received.Count == 33) finished.Set();
        });

        var sent = new List<uint>();
        try
        {
            publisher.Publish(Voice(VoiceEventType.RecordingStart));
            Assert.True(entered.Wait(TimeSpan.FromSeconds(3)));
            for (int i = 0; i < 1000; i++)
            {
                bool start = i % 2 == 0;
                publisher.Publish(Voice(start ? VoiceEventType.RecordingStart : VoiceEventType.RecordingStop));
                sent.Add(start ? 0u : 1u);
            }
            publisher.Publish(Voice(VoiceEventType.RecordingStop));
            sent.Add(1);
        }
        finally { release.Set(); }

        Assert.True(finished.Wait(TimeSpan.FromSeconds(3)));
        Assert.Equal(new uint[] { 0 }.Concat(sent.TakeLast(32)), received);
        Assert.Equal(1u, received[^1]);
    }

    [Fact]
    public void Voice_payload_errors_remain_synchronous()
    {
        using var publisher = Create(_ => throw new InvalidOperationException("must not run"));
        Assert.Throws<InvalidOperationException>(() => publisher.Publish(new SemanticEvent
        {
            EventType = SemanticEventType.Voice
        }));
        Assert.Throws<ArgumentOutOfRangeException>(() => publisher.Publish(Voice((VoiceEventType)1234)));
    }

    [Fact]
    public void Normal_shutdown_drains_queued_stop_in_order()
    {
        var received = new List<uint>();
        using var publisher = Create(received.Add);
        publisher.Publish(Voice(VoiceEventType.RecordingStart));
        publisher.Publish(Voice(VoiceEventType.RecordingStop));
        publisher.Dispose();
        Assert.Equal(new uint[] { 0, 1 }, received);
    }

    [Fact]
    public void Shutdown_does_not_wait_indefinitely_for_blocked_com_call()
    {
        using var entered = new ManualResetEventSlim();
        using var release = new ManualResetEventSlim();
        using var returned = new ManualResetEventSlim();
        using var publisher = Create(_ =>
        {
            entered.Set();
            release.Wait();
            returned.Set();
        });
        try
        {
            publisher.Publish(Voice(VoiceEventType.RecordingStart));
            Assert.True(entered.Wait(TimeSpan.FromSeconds(3)));
            var watch = Stopwatch.StartNew();
            publisher.Dispose();
            Assert.True(watch.ElapsedMilliseconds < 3500);
            Assert.False(returned.IsSet, "Dispose must not claim to cancel an in-flight COM call.");
        }
        finally { release.Set(); }
        Assert.True(returned.Wait(TimeSpan.FromSeconds(3)));
    }

    private static WmiVoicePublisher Create(Action<uint> action) => new(
        Options.Create(new DispatcherOptions()), NullLogger<WmiVoicePublisher>.Instance, action);

    private static SemanticEvent Voice(VoiceEventType type) => new()
    {
        EventType = SemanticEventType.Voice,
        VoiceEvent = type
    };
}
