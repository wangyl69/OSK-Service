using System.Diagnostics;
using System.IO.MemoryMappedFiles;
using System.IO.Pipes;
using HybridSedPrototype.Contracts;
using HybridSedPrototype.SemanticEventDispatcher.Configuration;
using HybridSedPrototype.SemanticEventDispatcher.Models;
using HybridSedPrototype.SemanticEventDispatcher.Publishers;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace HybridSedPrototype.SemanticEventDispatcher.Tests;

public sealed class HapticLatencyTraceTests
{
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task Shared_trace_matches_real_one_byte_pipe_completion(bool nativeWake)
    {
        string name = "Local\\SedTraceTest-" + Guid.NewGuid().ToString("N");
        string pipe = "SedTracePipe-" + Guid.NewGuid().ToString("N");
        using var stop = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        using var server = new NamedPipeServerStream(pipe, PipeDirection.In, 1,
            PipeTransmissionMode.Byte, PipeOptions.Asynchronous);
        using var publisher = new HapticPipePublisher(Options.Create(new DispatcherOptions
        { HapticPipeName = pipe, HapticLatencyTraceName = name, HapticSenderNativeWake = nativeWake }),
            NullLogger<HapticPipePublisher>.Instance);
        using var reader = MemoryMappedFile.OpenExisting(name, MemoryMappedFileRights.Read);
        using var view = reader.CreateViewAccessor(0, 0, MemoryMappedFileAccess.Read);
        Assert.Equal(HapticLatencyTrace.Magic, view.ReadInt64(0));
        Assert.Equal(Stopwatch.Frequency, view.ReadInt64(8));
        var connected = server.WaitForConnectionAsync(stop.Token);
        long hook = Stopwatch.GetTimestamp();
        publisher.Publish(new SemanticEvent { EventType = SemanticEventType.Haptic,
            HapticEvent = HapticEventType.SpecialKey, HookReceivedTicks = hook });
        await connected;
        var payload = new byte[1];
        await server.ReadExactlyAsync(payload, stop.Token);
        Assert.Equal((byte)3, payload[0]);
        long offset = HapticLatencyTrace.HeaderSize;
        while (view.ReadInt64(offset + 72) == 0 || view.ReadInt64(offset + 96) == 0)
            await Task.Delay(1, stop.Token);
        Assert.Equal(HapticLatencyTrace.Sent, view.ReadInt64(offset + 72));
        Assert.Equal(1, view.ReadInt64(offset));
        Assert.Equal(hook, view.ReadInt64(offset + 8));
        Assert.Equal(3, view.ReadInt64(offset + 64));
        long previous = hook;
        for (int field = 16; field <= 56; field += 8)
        {
            long current = view.ReadInt64(offset + field);
            Assert.True(current >= previous, $"Out-of-order timestamp at offset {field}");
            previous = current;
        }
        Assert.Contains(view.ReadInt64(offset + 80), new long[] { 1, 2 });
        Assert.True(view.ReadInt64(offset + 88) >= view.ReadInt64(offset + 16));
        Assert.True(view.ReadInt64(offset + 96) >= view.ReadInt64(offset + 88));
        Assert.True(view.ReadInt64(offset + 24) >= view.ReadInt64(offset + 88));
    }

    [Fact]
    public void Saturation_preserves_old_records_and_does_not_wrap()
    {
        string name = "Local\\SedTraceBound-" + Guid.NewGuid().ToString("N");
        using var trace = new HapticLatencyTrace(name);
        using var reader = MemoryMappedFile.OpenExisting(name, MemoryMappedFileRights.Read);
        using var view = reader.CreateViewAccessor(0, 0, MemoryMappedFileAccess.Read);
        for (int index = 1; index <= HapticLatencyTrace.Capacity; index++)
        {
            long sequence = trace.Begin(2, index, index);
            trace.Complete(sequence, HapticLatencyTrace.Expired);
        }
        Assert.Equal(0, trace.Begin(3, 99, 99));
        Assert.Equal(HapticLatencyTrace.Capacity + 1, view.ReadInt64(32));
        Assert.Equal(1, view.ReadInt64(HapticLatencyTrace.HeaderSize));
        Assert.Equal(2, view.ReadInt64(HapticLatencyTrace.HeaderSize + 64));
        Assert.Equal(HapticLatencyTrace.Expired, view.ReadInt64(HapticLatencyTrace.HeaderSize + 72));
        long last = HapticLatencyTrace.HeaderSize + (HapticLatencyTrace.Capacity - 1) * HapticLatencyTrace.RecordSize;
        Assert.Equal(HapticLatencyTrace.Capacity, view.ReadInt64(last));
        trace.Dispose();
        trace.Complete(1, HapticLatencyTrace.Shutdown); // A late diagnostic callback must be harmless.
        Assert.Equal(HapticLatencyTrace.Expired, view.ReadInt64(HapticLatencyTrace.HeaderSize + 72));
    }
}
