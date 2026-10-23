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
