using System.Diagnostics;
using System.IO.MemoryMappedFiles;

namespace HybridSedPrototype.SemanticEventDispatcher.Publishers;

/// <summary>
/// Opt-in local diagnostic mapping. Fixed-size records, no per-event disk I/O,
/// no wraparound, and no change to the one-byte production pipe protocol.
/// A nonzero terminal result publishes a complete record to the reader.
/// </summary>
internal sealed class HapticLatencyTrace : IDisposable
{
    internal const long Magic = 0x5345445452433032;
    internal const int Capacity = 4096, HeaderSize = 64, RecordSize = 112;
    internal const long Sent = 1, QueueFull = 2, Expired = 3, IoFailed = 4, Shutdown = 5;
    private readonly MemoryMappedFile _mapping;
    private readonly MemoryMappedViewAccessor _view;
    private readonly long _initialPauseTicks;
    private long _started;
    private int _disposed;

    internal static HapticLatencyTrace? TryCreate(string? name, ILogger logger)
    {
        if (string.IsNullOrWhiteSpace(name)) return null;
        try
        {
            var trace = new HapticLatencyTrace(name);
            logger.LogInformation("Haptic latency trace Mapping={Mapping} Capacity={Capacity} Frequency={Frequency}; diagnostic only.",
                name, Capacity, Stopwatch.Frequency);
            return trace;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException)
        {
            logger.LogWarning(ex, "Haptic diagnostic mapping unavailable; normal dispatch continues.");
            return null;
        }
    }

    internal HapticLatencyTrace(string name)
    {
        _mapping = MemoryMappedFile.CreateNew(name, HeaderSize + Capacity * RecordSize, MemoryMappedFileAccess.ReadWrite);
        try
        {
            _view = _mapping.CreateViewAccessor(0, 0, MemoryMappedFileAccess.ReadWrite);
            _view.Write(0, Magic);
            _view.Write(8, Stopwatch.Frequency);
            _view.Write(16, (long)Capacity);
            _view.Write(24, (long)RecordSize);
            long collections = CollectionSum();
            _initialPauseTicks = GC.GetTotalPauseDuration().Ticks;
            _view.Write(40, collections);
            _view.Write(48, collections);
        }
        catch { _mapping.Dispose(); throw; }
    }

    internal long Begin(byte code, long hookTicks, long createdTicks)
    {
        if (Volatile.Read(ref _disposed) != 0) return 0;
        long sequence = Interlocked.Increment(ref _started);
        // Keep the first Capacity events. Saturation only stops tracing.
        PutHeader(32, sequence);
        if (sequence > Capacity) return 0;
        Put(sequence, 0, sequence);
        Put(sequence, 8, hookTicks);
        Put(sequence, 16, createdTicks);
        Put(sequence, 64, code);
        return sequence;
    }

    internal void Dequeued(long sequence) => Put(sequence, 24, Stopwatch.GetTimestamp());
    internal void EnqueueStarting(long sequence) => Put(sequence, 88, Stopwatch.GetTimestamp());
    internal void EnqueueReturned(long sequence) => Put(sequence, 96, Stopwatch.GetTimestamp());
    internal void WakeSignalReturned(long sequence) => Put(sequence, 104, Stopwatch.GetTimestamp());
    internal void WriteStarting(long sequence, long ticks) => Put(sequence, 32, ticks);
    internal void NativeCall(long sequence, long started, long returned, bool immediate)
    {
        Put(sequence, 40, started);
        Put(sequence, 48, returned);
        Put(sequence, 80, immediate ? 1 : 2);
    }
    internal void WriteFinished(long sequence) => Put(sequence, 56, Stopwatch.GetTimestamp());
    internal void Complete(long sequence, long result)
    {
        if (Volatile.Read(ref _disposed) != 0) return;
        PutHeader(48, CollectionSum());
        PutHeader(56, GC.GetTotalPauseDuration().Ticks - _initialPauseTicks);
        Thread.MemoryBarrier();
        Put(sequence, 72, result);
    }

    private static long CollectionSum() => (long)GC.CollectionCount(0) + GC.CollectionCount(1) + GC.CollectionCount(2);

    private void Put(long sequence, int field, long value)
    {
        if (sequence <= 0 || sequence > Capacity || Volatile.Read(ref _disposed) != 0) return;
        PutHeader(HeaderSize + (sequence - 1) * RecordSize + field, value);
    }

    private void PutHeader(long offset, long value)
    {
        try { _view.Write(offset, value); }
        // A publisher racing shutdown may finish its diagnostic record late.
        // Diagnostics must never turn a normal key event into a routing error.
        catch (ObjectDisposedException) { }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
        _view.Dispose();
        _mapping.Dispose();
    }
}
