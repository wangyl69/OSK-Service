using System.IO.Pipes;
using HybridSedPrototype.SemanticEventDispatcher.Publishers;
using Xunit;

namespace HybridSedPrototype.SemanticEventDispatcher.Tests;

public sealed class NativePipeWriterTests
{
    [Fact]
    public async Task Native_event_writes_work_with_managed_iocp_bound_handle()
    {
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        string name = "sed-native-" + Guid.NewGuid().ToString("N");
        using var server = new NamedPipeServerStream(name, PipeDirection.In, 1,
            PipeTransmissionMode.Byte, PipeOptions.Asynchronous);
        var accepting = server.WaitForConnectionAsync(deadline.Token);
        using var client = new NamedPipeClientStream(".", name, PipeDirection.Out, PipeOptions.Asynchronous);
        client.Connect(1000);
        await accepting;
        using var writer = new NativePipeWriter();
        byte[] received = new byte[6];
        var reading = server.ReadExactlyAsync(received, deadline.Token).AsTask();
        foreach (byte code in new byte[] { 1, 2, 3, 1, 3, 2 })
            writer.Write(client.SafePipeHandle, code, 20, deadline.Token);
        await reading;
        Assert.Equal(new byte[] { 1, 2, 3, 1, 3, 2 }, received);
    }

    [Fact]
    public async Task Pending_write_timeout_and_stop_cancel_are_drained_before_buffer_reuse()
    {
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        string name = "sed-cancel-" + Guid.NewGuid().ToString("N");
        using var server = new NamedPipeServerStream(name, PipeDirection.In, 1,
            PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 1, 1);
        var accepting = server.WaitForConnectionAsync(deadline.Token);
        using var client = new NamedPipeClientStream(".", name, PipeDirection.Out, PipeOptions.Asynchronous);
        client.Connect(1000);
        await accepting;
        using var writer = new NativePipeWriter();

        // Leave the real server unread until its native pipe quota is full.
        // The first pending one-byte write must time out and be cancelled.
        int committed = 0;
        bool timedOut = false;
        for (; committed < 65536; committed++)
        {
            try { writer.Write(client.SafePipeHandle, 0, 2, deadline.Token); }
            catch (TimeoutException) { timedOut = true; break; }
        }
        Assert.True(timedOut, "Did not reach a real pending native pipe write.");
        Assert.True(committed > 0);

        using var stop = new CancellationTokenSource();
        using var entered = new ManualResetEventSlim();
        Exception? cancelled = null;
        var thread = new Thread(() =>
        {
            entered.Set();
            try { writer.Write(client.SafePipeHandle, 0x55, 5000, stop.Token); }
            catch (Exception ex) { cancelled = ex; }
        }) { IsBackground = true };
        thread.Start();
        Assert.True(entered.Wait(TimeSpan.FromSeconds(2)));
        Thread.Sleep(20);
        stop.Cancel();
        Assert.True(thread.Join(2000), "Native pending write did not react to stop.");
        Assert.IsType<OperationCanceledException>(cancelled);

        // Neither the timed-out zero nor cancelled 0x55 may leak later when
        // reads resume. Reuse the same OVERLAPPED and pin for fresh writes.
        byte[] oldBytes = new byte[committed];
        await server.ReadExactlyAsync(oldBytes, deadline.Token);
        Assert.All(oldBytes, code => Assert.Equal((byte)0, code));
        writer.Write(client.SafePipeHandle, 0x7F, 20, deadline.Token);
        byte[] marker = new byte[1];
        await server.ReadExactlyAsync(marker, deadline.Token);
        Assert.Equal(0x7F, marker[0]);
        writer.Write(client.SafePipeHandle, 3, 20, deadline.Token);
        await server.ReadExactlyAsync(marker, deadline.Token);
        Assert.Equal(3, marker[0]);
    }
}
