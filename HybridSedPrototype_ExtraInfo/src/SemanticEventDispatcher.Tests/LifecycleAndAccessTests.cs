using System.Collections.Concurrent;
using System.IO.Pipes;
using HybridSedPrototype.Contracts;
using HybridSedPrototype.SemanticEventDispatcher.Configuration;
using HybridSedPrototype.SemanticEventDispatcher.Inputs;
using HybridSedPrototype.SemanticEventDispatcher.Models;
using HybridSedPrototype.SemanticEventDispatcher.Publishers;
using HybridSedPrototype.SemanticEventDispatcher.Routing;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace HybridSedPrototype.SemanticEventDispatcher.Tests;

public sealed class LifecycleAndAccessTests
{
    [Fact]
    public void Haptic_sender_runs_on_dedicated_thread_instead_of_thread_pool()
    {
        using var entered = new ManualResetEventSlim();
        bool? threadPool = null;
        string? threadName = null;
        using var publisher = new HapticPipePublisher(Options.Create(new DispatcherOptions()),
            NullLogger<HapticPipePublisher>.Instance, _ =>
            {
                threadPool = Thread.CurrentThread.IsThreadPoolThread;
                threadName = Thread.CurrentThread.Name;
                entered.Set();
                return Task.FromException<NamedPipeClientStream>(new IOException("simulated absent endpoint"));
            });
        publisher.Publish(Haptic(HapticEventType.NormalKey));
        Assert.True(entered.Wait(TimeSpan.FromSeconds(3)));
        Assert.False(threadPool);
        Assert.Equal("Hybrid SED Haptic Pipe Sender", threadName);
    }

    [Fact]
    public async Task Access_denial_is_logged_and_next_request_recovers_without_replay()
    {
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        string name = "sed-access-" + Guid.NewGuid().ToString("N");
        using var server = new NamedPipeServerStream(name, PipeDirection.In, 1,
            PipeTransmissionMode.Byte, PipeOptions.Asynchronous);
        var connected = server.WaitForConnectionAsync(deadline.Token);
        var logger = new RecordingLogger<HapticPipePublisher>();
        var denied = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        logger.OnWarning = () => denied.TrySetResult();
        int attempts = 0;
        using var publisher = new HapticPipePublisher(
            Options.Create(new DispatcherOptions { HapticPipeName = name, HapticPipeConnectTimeoutMs = 1000 }),
            logger, async token =>
            {
                if (Interlocked.Increment(ref attempts) == 1)
                    throw new UnauthorizedAccessException("simulated pipe ACL denial");
                var client = new NamedPipeClientStream(".", name, PipeDirection.Out, PipeOptions.Asynchronous);
                try { await client.ConnectAsync(token); return client; }
                catch { client.Dispose(); throw; }
            });

        publisher.Publish(Haptic(HapticEventType.NormalKey));
        await denied.Task.WaitAsync(deadline.Token);
        publisher.Publish(Haptic(HapticEventType.SpecialKey));
        await connected;
        byte[] received = new byte[1];
        Assert.Equal(1, await server.ReadAsync(received, deadline.Token));
        Assert.Equal((byte)HapticEventType.SpecialKey, received[0]);
        Assert.Equal(2, attempts);
        Assert.Equal(1, publisher.GetStatistics().AccessDenied);
        Assert.Equal(1, publisher.GetStatistics().ConnectFailed);
        Assert.Contains(logger.Messages, entry => entry.Level == LogLevel.Warning &&
            entry.Message.Contains("access denied") && entry.Message.Contains(name));
    }

    [Fact]
    public async Task Pipe_statistics_distinguish_queue_overflow_expiration_and_connect_failure()
    {
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var connection = new TaskCompletionSource<NamedPipeClientStream>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var publisher = new HapticPipePublisher(
            Options.Create(new DispatcherOptions { HapticPipeConnectTimeoutMs = 1000 }),
            NullLogger<HapticPipePublisher>.Instance, _ =>
            {
                entered.TrySetResult();
                return connection.Task;
            });
        try
        {
            publisher.Publish(Haptic(HapticEventType.NormalKey));
            await entered.Task.WaitAsync(TimeSpan.FromSeconds(3));
            for (int i = 0; i < 9; i++) publisher.Publish(Haptic(HapticEventType.NormalKey));
            Assert.Equal(1, publisher.GetStatistics().QueueFull);
            await Task.Delay(75); // The original 50ms TTL remains in force.
        }
        finally { connection.TrySetException(new IOException("simulated failed connection")); }

        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        while (publisher.GetStatistics().Dropped != 10) await Task.Delay(1, deadline.Token);
        var statistics = publisher.GetStatistics();
        Assert.Equal(10, statistics.Published);
        Assert.Equal(9, statistics.Enqueued);
        Assert.Equal(8, statistics.Expired);
        Assert.Equal(8, statistics.ExpiredAtDequeue);
        Assert.Equal(0, statistics.ExpiredBeforeWrite);
        Assert.True(statistics.MaxQueueWaitUs >= 50_000);
        Assert.True(statistics.MaxExpiredAgeUs >= 50_000);
        Assert.True(statistics.MaxConnectUs >= 50_000);
        Assert.Equal(1, statistics.ConnectFailed);
        Assert.Equal(0, statistics.WriteFailed);
        Assert.Equal(0, statistics.TimedOut);
        Assert.Equal(0, statistics.Sent);
    }

    [Fact]
    public async Task Pipe_statistics_identify_connection_timeout()
    {
        using var publisher = new HapticPipePublisher(
            Options.Create(new DispatcherOptions { HapticPipeConnectTimeoutMs = 10 }),
            NullLogger<HapticPipePublisher>.Instance, async token =>
            {
                await Task.Delay(Timeout.Infinite, token);
                throw new InvalidOperationException("unreachable");
            });
        publisher.Publish(Haptic(HapticEventType.NormalKey));
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        while (publisher.GetStatistics().TimedOut != 1) await Task.Delay(1, deadline.Token);
        Assert.Equal(1, publisher.GetStatistics().ConnectFailed);
        Assert.Equal(1, publisher.GetStatistics().Dropped);
        Assert.Equal(0, publisher.GetStatistics().Expired);
    }

    [Fact]
    public async Task Real_keyboard_hook_can_start_and_stop_immediately_repeatedly()
    {
        var options = Options.Create(new DispatcherOptions());
        for (int i = 0; i < 20; i++)
        {
            var logger = new RecordingLogger<Worker>();
            using var worker = new Worker(logger, options,
                new InputEventClassifier(options, NullLogger<InputEventClassifier>.Instance),
                new SemanticEventRouter([], NullLogger<SemanticEventRouter>.Instance));
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            await worker.StartAsync(timeout.Token);
            await worker.StopAsync(timeout.Token);
            Assert.Contains(logger.Messages, entry => entry.Message == "Keyboard hook thread started.");
            Assert.DoesNotContain(logger.Messages, entry => entry.Level >= LogLevel.Warning);
        }
    }

    private static SemanticEvent Haptic(HapticEventType type) => new()
    {
        EventType = SemanticEventType.Haptic,
        HapticEvent = type
    };

    private sealed class RecordingLogger<T> : ILogger<T>
    {
        public ConcurrentQueue<(LogLevel Level, string Message)> Messages { get; } = new();
        public Action? OnWarning { get; set; }
        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
        public bool IsEnabled(LogLevel logLevel) => true;
        public void Log<TState>(LogLevel level, EventId id, TState state, Exception? exception,
            Func<TState, Exception?, string> formatter)
        {
            Messages.Enqueue((level, formatter(state, exception)));
            if (level == LogLevel.Warning) OnWarning?.Invoke();
        }
    }
}
