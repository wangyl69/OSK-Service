using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace HybridSedPrototype.SemanticEventDispatcher.Publishers;

/// <summary>
/// Single-owner, one-byte overlapped writes. Completion and cancellation use
/// native events instead of managed ThreadPool callbacks or timer dispatch.
/// </summary>
internal sealed class NativePipeWriter : IDisposable
{
    private const int ErrorIoPending = 997;
    private const uint WaitObject0 = 0;
    private const uint WaitTimeout = 258;
    private const uint WaitFailed = uint.MaxValue;
    private readonly byte[] _payload = new byte[1];
    private readonly IntPtr[] _waitHandles = new IntPtr[2];
    private SafeWaitHandle? _completed;
    private GCHandle _pinnedPayload;
    private IntPtr _overlapped;
    private bool _disposed;

    public NativePipeWriter()
    {
        try
        {
            _completed = CreateEvent(IntPtr.Zero, true, false, null);
            if (_completed.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            _overlapped = Marshal.AllocHGlobal(Marshal.SizeOf<Overlapped>());
            _pinnedPayload = GCHandle.Alloc(_payload, GCHandleType.Pinned);
            _waitHandles[0] = _completed.DangerousGetHandle();
        }
        catch { Dispose(); throw; }
    }

    public void Write(SafePipeHandle pipe, byte code, int timeoutMs, CancellationToken stop,
        HapticLatencyTrace? trace = null, long traceSequence = 0)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        stop.ThrowIfCancellationRequested();
        long started = Stopwatch.GetTimestamp();
        if (!ResetEvent(_completed!)) throw Error("ResetEvent", Marshal.GetLastWin32Error());
        // The pipe handle was bound to managed IOCP by NamedPipeClientStream.
        // Its event's low bit suppresses completion packets for this operation;
        // otherwise the runtime would receive an unknown native OVERLAPPED.
        // https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-getqueuedcompletionstatus
        Marshal.StructureToPtr(new Overlapped
        {
            Event = new IntPtr(_waitHandles[0].ToInt64() | 1L)
        }, _overlapped, false);
        _payload[0] = code;
        _waitHandles[1] = stop.WaitHandle.SafeWaitHandle.DangerousGetHandle();

        bool pipeReference = false;
        bool outstanding = false;
        try
        {
            pipe.DangerousAddRef(ref pipeReference);
            long nativeStarted = trace is null ? 0 : Stopwatch.GetTimestamp();
            bool immediate = WriteFile(pipe, _pinnedPayload.AddrOfPinnedObject(), 1, IntPtr.Zero, _overlapped);
            int error = immediate ? 0 : Marshal.GetLastWin32Error();
            // Establish ownership before any optional diagnostic operation:
            // even an unexpected trace failure must cancel/join pending I/O.
            outstanding = immediate || error == ErrorIoPending;
            long nativeReturned = trace is null ? 0 : Stopwatch.GetTimestamp();
            trace?.NativeCall(traceSequence, nativeStarted, nativeReturned, immediate);
            if (!immediate)
            {
                if (error != ErrorIoPending) throw Error("WriteFile", error);
            }
            if (!immediate)
            {
                int remainingMs = timeoutMs - (int)Stopwatch.GetElapsedTime(started).TotalMilliseconds;
                if (remainingMs <= 0) throw new TimeoutException("Native haptic pipe write budget elapsed.");
                uint wait = WaitForMultipleObjects(2, _waitHandles, false, (uint)remainingMs);
                if (wait == WaitTimeout) throw new TimeoutException("Native haptic pipe write timed out.");
                if (wait == WaitObject0 + 1) throw new OperationCanceledException(stop);
                if (wait == WaitFailed) throw Error("WaitForMultipleObjects", Marshal.GetLastWin32Error());
                if (wait != WaitObject0) throw new IOException("Unexpected haptic pipe wait result: " + wait);
            }
            bool success = GetOverlappedResult(pipe, _overlapped, out uint written, false);
            int completionError = success ? 0 : Marshal.GetLastWin32Error();
            if (!success) throw Error("GetOverlappedResult", completionError);
            outstanding = false;
            if (written != 1) throw new IOException("Incomplete one-byte haptic pipe write.");
        }
        finally
        {
            if (outstanding)
            {
                // Cancel may race completion (ERROR_NOT_FOUND). In either case
                // wait for terminal completion before unpinning/reusing memory.
                // https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex
                CancelIoEx(pipe, _overlapped);
                GetOverlappedResult(pipe, _overlapped, out _, true);
            }
            if (pipeReference) pipe.DangerousRelease();
        }
    }

    private static Exception Error(string operation, int error) => error == 5
        ? new UnauthorizedAccessException(operation + " failed: access denied.", new Win32Exception(error))
        : new IOException(operation + " failed. Win32Error=" + error, new Win32Exception(error));

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        if (_pinnedPayload.IsAllocated) _pinnedPayload.Free();
        if (_overlapped != IntPtr.Zero) { Marshal.FreeHGlobal(_overlapped); _overlapped = IntPtr.Zero; }
        _completed?.Dispose();
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Overlapped
    {
        public UIntPtr Internal;
        public UIntPtr InternalHigh;
        public uint Offset;
        public uint OffsetHigh;
        public IntPtr Event;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeWaitHandle CreateEvent(IntPtr attributes, bool manualReset, bool initialState, string? name);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ResetEvent(SafeWaitHandle handle);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool WriteFile(SafePipeHandle handle, IntPtr buffer, uint count, IntPtr written, IntPtr overlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint WaitForMultipleObjects(uint count, [In] IntPtr[] handles, bool waitAll, uint milliseconds);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CancelIoEx(SafePipeHandle handle, IntPtr overlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetOverlappedResult(SafePipeHandle handle, IntPtr overlapped, out uint count, bool wait);
}
