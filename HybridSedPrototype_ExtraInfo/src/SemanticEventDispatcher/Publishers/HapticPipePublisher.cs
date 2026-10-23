using System.IO.Pipes;
using System.Diagnostics;
using System.Collections.Concurrent;
using HybridSedPrototype.Contracts;
using HybridSedPrototype.SemanticEventDispatcher.Configuration;
using HybridSedPrototype.SemanticEventDispatcher.Inputs;
using HybridSedPrototype.SemanticEventDispatcher.Models;
using Microsoft.Extensions.Options;

namespace HybridSedPrototype.SemanticEventDispatcher.Publishers;

/// <summary>
/// Writes one byte per haptic event to the named pipe.
/// The byte is the HapticEventType value. A missing consumer is not fatal:
/// the event is dropped and the keyboard hook continues.
/// </summary>
public sealed class HapticPipePublisher : ISemanticEventPublisher, IDisposable
{
    private readonly string _pipeName;
    private readonly int _connectTimeoutMs;
    private readonly ILogger<HapticPipePublisher> _logger;
    private readonly Func<CancellationToken, Task<NamedPipeClientStream>> _connect;
    private NamedPipeClientStream? _pipe;
    private readonly CancellationTokenSource _stop = new();
    private readonly BlockingCollection<(byte Code, long Created, long Trace)> _queue = new(8);
    private readonly HapticLatencyTrace? _trace;
    private readonly AutoResetEvent? _senderAvailable;
    private readonly Thread _sender;
    private readonly Task _reporter;
    private int _disposed;
    private long _dropped;
    private long _lastAccessWarning;
    private long _published;
    private long _enqueued;
    private long _sent;
    private long _queueFull;
    private long _expired;
    private long _connectFailed;
    private long _writeFailed;
    private long _timedOut;
    private long _accessDenied;
    private long _shutdownDropped;
    private long _expiredAtDequeue;
    private long _expiredBeforeWrite;
    private long _maxQueueWaitUs;
    private long _maxExpiredAgeUs;
    private long _maxConnectUs;
    private long _maxWriteUs;
    private HapticPipeStatistics? _lastReported;

    public HapticPipePublisher(
        IOptions<DispatcherOptions> options,
        ILogger<HapticPipePublisher> logger)
        : this(options, logger, null)
    {
    }

    internal HapticPipePublisher(
        IOptions<DispatcherOptions> options,
        ILogger<HapticPipePublisher> logger,
        Func<CancellationToken, Task<NamedPipeClientStream>>? connect)
    {
        DispatcherOptions value = options.Value;
        _pipeName = string.IsNullOrWhiteSpace(value.HapticPipeName)
            ? HapticPipeProtocol.PipeName
            : value.HapticPipeName;
        _connectTimeoutMs = value.HapticPipeConnectTimeoutMs > 0
            ? value.HapticPipeConnectTimeoutMs
            : 20;
        _logger = logger;
        _connect = connect ?? ConnectPipe;
        _trace = HapticLatencyTrace.TryCreate(value.HapticLatencyTraceName, logger);
        _senderAvailable = value.HapticSenderNativeWake ? new AutoResetEvent(false) : null;
        _logger.LogInformation("Haptic sender wake Mode={WakeMode}",
            _senderAvailable is null ? "BlockingCollection" : "NativeEvent");

        // A hot-path consumer must not compete with WMI/GC continuations for
        // ThreadPool scheduling on every key. Waiting here never blocks hooks.
        _sender = new Thread(() =>
        {
            LatencyThreadScheduling.Apply(value.LatencyThreadPriorityBoost, _logger, "haptic-sender");
            Send();
        })
        {
            IsBackground = true,
            Name = "Hybrid SED Haptic Pipe Sender"
        };
        _sender.Start();
        _reporter = Task.Run(ReportAsync);
    }

    public void Publish(SemanticEvent semanticEvent)
    {
        if (semanticEvent.EventType != SemanticEventType.Haptic)
        {
            return;
        }

        if (semanticEvent.HapticEvent is not HapticEventType hapticEvent)
        {
            throw new InvalidOperationException(
                "Haptic semantic event is missing HapticEvent.");
        }

        if (!Enum.IsDefined(hapticEvent))
        {
            throw new ArgumentOutOfRangeException(
                nameof(semanticEvent),
                hapticEvent,
                "Unknown haptic event.");
        }

        // Keyboard haptics currently allow only 1/2/3. TouchPad (4) remains
        // a reserved wire value, not an enabled playback source. Filter before
        // enqueue so future producers cannot consume the keyboard latency budget.
        if (hapticEvent is not (HapticEventType.NormalKey or HapticEventType.ModifierKey or HapticEventType.SpecialKey))
            return;

        Interlocked.Increment(ref _published);
        bool queued = false;
        long created = Stopwatch.GetTimestamp();
        long trace = _trace?.Begin((byte)hapticEvent, semanticEvent.HookReceivedTicks, created) ?? 0;
        if (Volatile.Read(ref _disposed) == 0)
        {
            try
            {
                _trace?.EnqueueStarting(trace);
                queued = _queue.TryAdd(((byte)hapticEvent, created, trace));
            }
            catch (InvalidOperationException) { /* CompleteAdding raced shutdown. */ }
        }
        _trace?.EnqueueReturned(trace);
        if (queued)
        {
            Interlocked.Increment(ref _enqueued);
            try
            {
                if (_senderAvailable is not null)
                {
                    _senderAvailable.Set();
                    _trace?.WakeSignalReturned(trace);
                }
            }
            catch (ObjectDisposedException) { /* Publisher raced shutdown after successful enqueue. */ }
        }
        else if (Volatile.Read(ref _disposed) != 0)
        { CountDrop(ref _shutdownDropped); _trace?.Complete(trace, HapticLatencyTrace.Shutdown); }
        else { CountDrop(ref _queueFull); _trace?.Complete(trace, HapticLatencyTrace.QueueFull); }
    }

    private void Send()
    {
        try
        {
            using var writer = new NativePipeWriter();
            foreach (var item in Consume())
            {
                _trace?.Dequeued(item.Trace);
                long ageUs = AgeUs(item.Created);
                SetMax(ref _maxQueueWaitUs, ageUs);
                if (ageUs > 50_000)
                { Expire(ageUs, atDequeue: true); _trace?.Complete(item.Trace, HapticLatencyTrace.Expired); continue; }
                bool connecting = _pipe is null || !_pipe.IsConnected;
                try
                {
                    int timeoutMs = Math.Clamp(_connectTimeoutMs, 1, 1000);
                    long operationStarted = Stopwatch.GetTimestamp();
                    if (connecting)
                    {
                        _pipe?.Dispose();
                        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(_stop.Token);
                        timeout.CancelAfter(timeoutMs);
                        long connectStarted = Stopwatch.GetTimestamp();
                        try { _pipe = _connect(timeout.Token).GetAwaiter().GetResult(); }
                        finally { SetMax(ref _maxConnectUs, AgeUs(connectStarted)); }
                        _logger.LogInformation("Connected to haptic pipe {PipeName}.", _pipeName);
                    }
                    connecting = false;
                    ageUs = AgeUs(item.Created);
                    if (ageUs > 50_000)
                    { Expire(ageUs, atDequeue: false); _trace?.Complete(item.Trace, HapticLatencyTrace.Expired); continue; }
                    int remainingMs = timeoutMs - (int)(AgeUs(operationStarted) / 1000);
                    if (remainingMs <= 0) throw new TimeoutException("Haptic pipe I/O budget elapsed before write.");
                    long writeStarted = Stopwatch.GetTimestamp();
                    _trace?.WriteStarting(item.Trace, writeStarted);
                    try { writer.Write(_pipe!.SafePipeHandle, item.Code, remainingMs, _stop.Token, _trace, item.Trace); }
                    finally { _trace?.WriteFinished(item.Trace); SetMax(ref _maxWriteUs, AgeUs(writeStarted)); }
                    // PipeStream has no managed write buffer; Flush is a no-op.
                    Interlocked.Increment(ref _sent);
                    _trace?.Complete(item.Trace, HapticLatencyTrace.Sent);
                }
                catch (Exception ex) when (ex is IOException or OperationCanceledException or TimeoutException or UnauthorizedAccessException)
                {
                    _trace?.Complete(item.Trace, _stop.IsCancellationRequested ? HapticLatencyTrace.Shutdown : HapticLatencyTrace.IoFailed);
                    if (_stop.IsCancellationRequested) CountDrop(ref _shutdownDropped);
                    else
                    {
                        if (connecting) CountDrop(ref _connectFailed);
                        else CountDrop(ref _writeFailed);
                        if (ex is OperationCanceledException or TimeoutException)
                            Interlocked.Increment(ref _timedOut);
                        if (ex is UnauthorizedAccessException)
                            Interlocked.Increment(ref _accessDenied);
                    }
                    _pipe?.Dispose(); _pipe = null;
                    if (ex is UnauthorizedAccessException &&
                        (_lastAccessWarning == 0 || Stopwatch.GetElapsedTime(_lastAccessWarning).TotalSeconds >= 5))
                    {
                        _lastAccessWarning = Stopwatch.GetTimestamp();
                        _logger.LogWarning(ex,
                            "Haptic pipe {PipeName} access denied; event dropped, next request will retry. Check Service pipe permissions.",
                            _pipeName);
                    }
                    else _logger.LogDebug(ex, "Haptic event dropped; no replay.");
                }
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            // Exceptions escaping a dedicated Thread would terminate the whole
            // process. Keep normal keyboard dispatch alive and make a stopped
            // sender explicit rather than silently accumulating new requests.
            _queue.CompleteAdding();
            _logger.LogCritical(ex, "Haptic pipe sender stopped unexpectedly; restart Dispatcher after correcting the endpoint/configuration.");
        }
        finally
        {
            _pipe?.Dispose(); _pipe = null;
            while (_queue.TryTake(out var item))
            { CountDrop(ref _shutdownDropped); _trace?.Complete(item.Trace, HapticLatencyTrace.Shutdown); }
        }
    }

    private IEnumerable<(byte Code, long Created, long Trace)> Consume()
    {
        // 2026-10-23 latency investigation: one sample spent 11.52 ms after
        // TryAdd returned and before dequeue, with no observed GC pause.
        // Default to a native event wake and drain the queue before sleeping;
        // retain the old path for controlled A/B checks. This reduces the
        // observed tail, but cannot guarantee Windows thread scheduling time.
        if (_senderAvailable is null)
        {
            foreach (var item in _queue.GetConsumingEnumerable(_stop.Token)) yield return item;
            yield break;
        }
        WaitHandle[] handles = [_senderAvailable, _stop.Token.WaitHandle];
        while (true)
        {
            _stop.Token.ThrowIfCancellationRequested();
            if (_queue.TryTake(out var item)) { yield return item; continue; }
            if (_queue.IsCompleted) yield break;
            // Drain before sleeping. An auto-reset event remembers a signal
            // arriving between the empty check and WaitAny; signals may merge
            // safely because the next iteration drains every queued item.
            WaitHandle.WaitAny(handles);
        }
    }

    private static long AgeUs(long started) => Stopwatch.GetElapsedTime(started).Ticks / 10;

    private static void SetMax(ref long maximum, long value)
    {
        // Only the sender writes these maxima; snapshots use atomic reads.
        if (value > Interlocked.Read(ref maximum)) Interlocked.Exchange(ref maximum, value);
    }

    private void Expire(long ageUs, bool atDequeue)
    {
        CountDrop(ref _expired);
        if (atDequeue) Interlocked.Increment(ref _expiredAtDequeue);
        else Interlocked.Increment(ref _expiredBeforeWrite);
        SetMax(ref _maxExpiredAgeUs, ageUs);
    }

    private void CountDrop(ref long reason)
    {
        Interlocked.Increment(ref reason);
        Interlocked.Increment(ref _dropped);
    }

    internal HapticPipeStatistics GetStatistics() => new(
        Interlocked.Read(ref _published), Interlocked.Read(ref _enqueued), Interlocked.Read(ref _sent),
        Interlocked.Read(ref _queueFull), Interlocked.Read(ref _expired), Interlocked.Read(ref _connectFailed),
        Interlocked.Read(ref _writeFailed), Interlocked.Read(ref _timedOut), Interlocked.Read(ref _accessDenied),
        Interlocked.Read(ref _shutdownDropped), Interlocked.Read(ref _dropped),
        Interlocked.Read(ref _expiredAtDequeue), Interlocked.Read(ref _expiredBeforeWrite),
        Interlocked.Read(ref _maxQueueWaitUs), Interlocked.Read(ref _maxExpiredAgeUs),
        Interlocked.Read(ref _maxConnectUs), Interlocked.Read(ref _maxWriteUs));

    private async Task ReportAsync()
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(5));
        try
        {
            while (await timer.WaitForNextTickAsync(_stop.Token)) ReportStatistics();
        }
        catch (OperationCanceledException) { }
    }

    private void ReportStatistics()
    {
        HapticPipeStatistics statistics = GetStatistics();
        if (_lastReported == statistics) return;
        _lastReported = statistics;
        _logger.LogInformation(
            "Haptic pipe stats Pipe={PipeName} Published={Published} Enqueued={Enqueued} Sent={Sent} QueueFull={QueueFull} Expired={Expired} ConnectFailed={ConnectFailed} WriteFailed={WriteFailed} TimedOut={TimedOut} AccessDenied={AccessDenied} ShutdownDropped={ShutdownDropped} Dropped={Dropped} ExpiredAtDequeue={ExpiredAtDequeue} ExpiredBeforeWrite={ExpiredBeforeWrite} MaxQueueWaitUs={MaxQueueWaitUs} MaxExpiredAgeUs={MaxExpiredAgeUs} MaxConnectUs={MaxConnectUs} MaxWriteUs={MaxWriteUs}",
            _pipeName, statistics.Published, statistics.Enqueued, statistics.Sent, statistics.QueueFull,
            statistics.Expired, statistics.ConnectFailed, statistics.WriteFailed, statistics.TimedOut,
            statistics.AccessDenied, statistics.ShutdownDropped, statistics.Dropped,
            statistics.ExpiredAtDequeue, statistics.ExpiredBeforeWrite, statistics.MaxQueueWaitUs,
            statistics.MaxExpiredAgeUs, statistics.MaxConnectUs, statistics.MaxWriteUs);
    }

    private Task<NamedPipeClientStream> ConnectPipe(CancellationToken token)
    {
        var pipe = new NamedPipeClientStream(".", _pipeName, PipeDirection.Out, PipeOptions.Asynchronous);
        try
        {
            token.ThrowIfCancellationRequested();
            pipe.Connect(Math.Clamp(_connectTimeoutMs, 1, 1000));
            token.ThrowIfCancellationRequested();
            return Task.FromResult(pipe);
        }
        catch { pipe.Dispose(); throw; }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
        _queue.CompleteAdding();
        _stop.Cancel();
        _sender.Join();
        _reporter.GetAwaiter().GetResult();
        ReportStatistics();
        _stop.Dispose();
        _queue.Dispose();
        _senderAvailable?.Dispose();
        _trace?.Dispose();
        _logger.LogInformation("Haptic publisher stopped. Dropped={Dropped}", _dropped);
    }
}

internal readonly record struct HapticPipeStatistics(long Published, long Enqueued, long Sent,
    long QueueFull, long Expired, long ConnectFailed, long WriteFailed, long TimedOut,
    long AccessDenied, long ShutdownDropped, long Dropped, long ExpiredAtDequeue,
    long ExpiredBeforeWrite, long MaxQueueWaitUs, long MaxExpiredAgeUs,
    long MaxConnectUs, long MaxWriteUs);
