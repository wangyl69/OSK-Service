using System.IO.Pipes;
using HybridSedPrototype.Contracts;
using HybridSedPrototype.SemanticEventDispatcher.Configuration;
using HybridSedPrototype.SemanticEventDispatcher.Models;
using HybridSedPrototype.SemanticEventDispatcher.Publishers;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace HybridSedPrototype.SemanticEventDispatcher.Tests;

public sealed class NativeSenderWakeTests
{
    [Fact]
    public async Task Fixed_ring_preserves_each_producers_order_across_wraparound()
    {
        var queue = new HapticSendRing();
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        var received = new List<(byte Code, long Created, long Trace)>();
        var consumer = Task.Run(() =>
        {
            while (!queue.IsCompleted)
            {
                deadline.Token.ThrowIfCancellationRequested();
                if (queue.TryTake(out var item)) received.Add(item);
                else Thread.Yield();
            }
        });
        var producers = Enumerable.Range(1, 4).Select(producer => Task.Run(() =>
        {
            for (int i = 0; i < 1000; ++i)
                while (!queue.TryAdd(((byte)producer, i, producer * 1000 + i)))
                { deadline.Token.ThrowIfCancellationRequested(); Thread.Yield(); }
        })).ToArray();
        try { await Task.WhenAll(producers).WaitAsync(deadline.Token); }
        finally { queue.CompleteAdding(); }
        await consumer.WaitAsync(deadline.Token);
        Assert.Equal(4000, received.Count);
        Assert.Equal(4000, received.Select(item => item.Trace).Distinct().Count());
        foreach (int producer in Enumerable.Range(1, 4))
            Assert.Equal(Enumerable.Range(0, 1000).Select(i => (long)i),
                received.Where(item => item.Code == producer).Select(item => item.Created));
        Assert.Throws<InvalidOperationException>(() => queue.TryAdd((1, 0, 0)));
    }

    [Fact]
    public async Task Native_event_drains_burst_and_shutdown_wakes_an_idle_sender()
    {
        string pipe = "NativeWake-" + Guid.NewGuid().ToString("N");
        using var stop = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        using var server = new NamedPipeServerStream(pipe, PipeDirection.In, 1,
            PipeTransmissionMode.Byte, PipeOptions.Asynchronous);
        using var publisher = new HapticPipePublisher(Options.Create(new DispatcherOptions
        { HapticPipeName = pipe, HapticSenderNativeWake = true }), NullLogger<HapticPipePublisher>.Instance);
        var connected = server.WaitForConnectionAsync(stop.Token);
        byte[] expected = [1, 2, 3, 1, 2, 3, 1, 2];
        foreach (byte code in expected) publisher.Publish(new SemanticEvent
        { EventType = SemanticEventType.Haptic, HapticEvent = (HapticEventType)code });
        await connected;
        var actual = new byte[expected.Length];
        await server.ReadExactlyAsync(actual, stop.Token);
        Assert.Equal(expected, actual);
        while (publisher.GetStatistics().Sent != expected.Length) await Task.Delay(1, stop.Token);
        Assert.Equal(0, publisher.GetStatistics().Dropped);
        await Task.Run(publisher.Dispose).WaitAsync(stop.Token);
    }
}
