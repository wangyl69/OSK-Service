using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using HybridSedPrototype.SemanticEventDispatcher.Configuration;
using HybridSedPrototype.SemanticEventDispatcher.Inputs;
using HybridSedPrototype.SemanticEventDispatcher.Models;
using HybridSedPrototype.SemanticEventDispatcher.Routing;
using Microsoft.Extensions.Options;

namespace HybridSedPrototype.SemanticEventDispatcher;

public sealed class Worker : BackgroundService
{
    private const int WhKeyboardLl = 13;

    private const int WmKeyDown = 0x0100;
    private const int WmKeyUp = 0x0101;
    private const int WmSysKeyDown = 0x0104;
    private const int WmSysKeyUp = 0x0105;

    private const uint WmQuit = 0x0012;

    // Identifier OSK sets in KBDLLHOOKSTRUCT.ExtraInfo. Haptic (named pipe)
    // output is OSK-only; Voice (WMI) still fires for every key.
    private const ulong OskExtraInfoMarker = HapticInputGate.OskExtraInfoMarker;

    private readonly ILogger<Worker> _logger;
    private readonly IOptions<DispatcherOptions> _options;
    private readonly InputEventClassifier _classifier;
    private readonly SemanticEventRouter _router;
    private readonly bool _traceHapticLatency;

    private readonly LowLevelKeyboardProc _keyboardProc;

    private IntPtr _keyboardHook = IntPtr.Zero;

    private Thread? _hookThread;
    private uint _hookThreadId;
    private readonly object _hookLifecycleGate = new();

    public Worker(
        ILogger<Worker> logger,
        IOptions<DispatcherOptions> options,
        InputEventClassifier classifier,
        SemanticEventRouter router)
    {
        _logger = logger;
        _options = options;
        _classifier = classifier;
        _router = router;
        _traceHapticLatency = !string.IsNullOrWhiteSpace(options.Value.HapticLatencyTraceName);

        // Keep the delegate alive while the native hook is installed.
        _keyboardProc = KeyboardHookCallback;
    }

    protected override async Task ExecuteAsync(
        CancellationToken stoppingToken)
    {
        _logger.LogInformation(
            "Hybrid Semantic Event Dispatcher started.");

        _logger.LogInformation(
            "Voice key VK={VoiceKey}. F12 is 123. Production OSK target F24 is 135.",
            _options.Value.VoiceKeyVirtualKey);

        _logger.LogInformation(
            "WMI {WmiNamespace} {WmiClass}. Named pipe {PipeName}.",
            _options.Value.WmiNamespace,
            _options.Value.WmiClassName,
            _options.Value.HapticPipeName);

        try
        {
            StartKeyboardHookThread();

            _logger.LogInformation(
                "Keyboard hook thread started.");

            await Task.Delay(
                Timeout.Infinite,
                stoppingToken);
        }
        catch (OperationCanceledException)
        {
            // Normal shutdown path.
        }
        catch (Exception ex)
        {
            _logger.LogError(
                ex,
                "Hybrid Semantic Event Dispatcher failed.");

            throw;
        }
        finally
        {
            StopKeyboardHookThread();

            if (_hookThread?.IsAlive == true)
                _logger.LogWarning("Hybrid Semantic Event Dispatcher shutdown has a pending hook thread.");
            else
                _logger.LogInformation("Hybrid Semantic Event Dispatcher stopped.");
        }
    }

    private void StartKeyboardHookThread()
    {
        lock (_hookLifecycleGate)
        {
            if (_hookThread != null)
            {
                return;
            }

            using var hookReady =
                new ManualResetEventSlim(false);

            Exception? hookStartException = null;
            bool startupComplete = false;

            _hookThread = new Thread(
                () =>
                {
                    try
                    {
                        LatencyThreadScheduling.Apply(_options.Value.LatencyThreadPriorityBoost, _logger, "keyboard-hook", _options.Value.LatencyThreadPriority);
                        _hookThreadId =
                            GetCurrentThreadId();

                        // PostThreadMessage requires the target thread's message
                        // queue to exist. Create it before announcing readiness.
                        PeekMessage(out _, IntPtr.Zero, 0, 0, 0);

                        _keyboardHook =
                            SetKeyboardHook(
                                _keyboardProc);

                        startupComplete = true;
                        hookReady.Set();

                        MessageLoop();
                    }
                    catch (Exception ex)
                    {
                        if (!startupComplete)
                        {
                            hookStartException = ex;
                            hookReady.Set();
                        }
                        else
                            _logger.LogError(ex, "Keyboard hook thread failed after startup.");
                    }
                    finally
                    {
                        UninstallKeyboardHook();
                    }
                });

            _hookThread.IsBackground = true;
            _hookThread.Name =
                "Hybrid SED Keyboard Hook Thread";

            _hookThread.Start();

            hookReady.Wait();

            if (hookStartException != null)
            {
                throw new InvalidOperationException(
                    "Failed to start keyboard hook thread.",
                    hookStartException);
            }

            if (_keyboardHook == IntPtr.Zero)
            {
                throw new InvalidOperationException(
                    "Keyboard hook was not installed.");
            }
        }
    }

    private void StopKeyboardHookThread()
    {
        lock (_hookLifecycleGate)
        {
            if (_hookThread == null)
            {
                return;
            }

            if (_hookThread.IsAlive && _hookThreadId != 0)
            {
                if (!PostThreadMessage(
                    _hookThreadId,
                    WmQuit,
                    UIntPtr.Zero,
                    IntPtr.Zero))
                {
                    int error = Marshal.GetLastWin32Error();
                    _logger.LogWarning("Could not signal keyboard hook shutdown. Win32Error={Error}", error);
                }
            }

            if (_hookThread.IsAlive)
            {
                if (!_hookThread.Join(millisecondsTimeout: 2000))
                {
                    // Keep the thread and delegate rooted so Dispose can retry;
                    // never report a live hook thread as already stopped.
                    _logger.LogWarning("Keyboard hook thread is still running after shutdown timeout; retaining its reference.");
                    return;
                }
            }

            _hookThread = null;
            _hookThreadId = 0;
        }
    }

    private static IntPtr SetKeyboardHook(
        LowLevelKeyboardProc callback)
    {
        using Process currentProcess =
            Process.GetCurrentProcess();

        ProcessModule? currentModule =
            currentProcess.MainModule;

        string? moduleName =
            currentModule?.ModuleName;

        IntPtr moduleHandle =
            GetModuleHandle(moduleName);

        IntPtr hook =
            SetWindowsHookEx(
                WhKeyboardLl,
                callback,
                moduleHandle,
                0);

        if (hook == IntPtr.Zero)
        {
            int error =
                Marshal.GetLastWin32Error();

            throw new Win32Exception(
                error,
                "SetWindowsHookEx failed.");
        }

        return hook;
    }

    private void MessageLoop()
    {
        while (true)
        {
            int result =
                GetMessage(
                    out Message message,
                    IntPtr.Zero,
                    0,
                    0);

            if (result == 0)
            {
                // WM_QUIT was received.
                break;
            }

            if (result == -1)
            {
                int error =
                    Marshal.GetLastWin32Error();

                throw new Win32Exception(
                    error,
                    "GetMessage failed.");
            }

            TranslateMessage(
                ref message);

            DispatchMessage(
                ref message);
        }
    }

    private IntPtr KeyboardHookCallback(
        int nCode,
        IntPtr wParam,
        IntPtr lParam)
    {
        long hookReceivedTicks = _traceHapticLatency ? Stopwatch.GetTimestamp() : 0;
        if (nCode >= 0)
        {
            int message =
                unchecked(
                    (int)wParam.ToInt64());

            bool isKeyDown =
                message == WmKeyDown ||
                message == WmSysKeyDown;

            bool isKeyUp =
                message == WmKeyUp ||
                message == WmSysKeyUp;

            if (isKeyDown || isKeyUp)
            {
                LowLevelKeyboardInput keyboardData =
                    Marshal.PtrToStructure<LowLevelKeyboardInput>(
                        lParam);

                bool isOSK =
                    keyboardData.ExtraInfo == OskExtraInfoMarker;

                bool isInjected =
                    keyboardData.Flags.HasFlag(
                        LowLevelKeyboardFlags.Injected) ||
                    keyboardData.Flags.HasFlag(
                        LowLevelKeyboardFlags.LowerIntegrityInjected);

                if (_logger.IsEnabled(LogLevel.Debug)) _logger.LogDebug(
                    "VK={VirtualKey} Down={IsKeyDown} Injected={IsInjected} Osk={IsOsk}",
                    keyboardData.VirtualKeyCode,
                    isKeyDown,
                    isInjected,
                    isOSK);

                if (!HapticInputGate.NeedsClassification((int)keyboardData.VirtualKeyCode,
                        _options.Value.VoiceKeyVirtualKey, isKeyDown, keyboardData.ExtraInfo.ToUInt64()))
                    return CallNextHookEx(_keyboardHook, nCode, wParam, lParam);
                try
                {
                    // Classification and enqueue run on this hook thread.
                    // Pipe I/O and WMI execute in independent publisher workers.
                    // CallNextHookEx still runs when classification/enqueue throws.
                    IReadOnlyList<SemanticEvent> semanticEvents =
                        _classifier.Classify(
                            unchecked(
                                (int)keyboardData.VirtualKeyCode),
                            isKeyDown,
                            InputSource.LowLevelKeyboardHook,
                            isInjected);

                    foreach (SemanticEvent semanticEvent in semanticEvents)
                    {
                        // Only marked OSK KeyDown requests haptics; voice retains both edges.
                        if (!HapticInputGate.ShouldRoute(semanticEvent.EventType,
                                isKeyDown, keyboardData.ExtraInfo.ToUInt64()))
                        {
                            if (_logger.IsEnabled(LogLevel.Debug)) _logger.LogDebug(
                                "Skip haptic publish: requires OSK KeyDown. VK={VirtualKey}",
                                keyboardData.VirtualKeyCode);

                            continue;
                        }

                        try
                        {
                            if (_traceHapticLatency) semanticEvent.HookReceivedTicks = hookReceivedTicks;
                            _router.Route(semanticEvent);
                        }
                        catch (Exception ex)
                        {
                            // Voice/WMI failure must not suppress a separate haptic request.
                            _logger.LogError(ex, "Failed to route {EventType}", semanticEvent.EventType);
                        }
                    }
                }
                catch (Exception ex)
                {
                    _logger.LogError(
                        ex,
                        "Failed to classify or route keyboard input. VirtualKey={VirtualKey}, IsKeyDown={IsKeyDown}",
                        keyboardData.VirtualKeyCode,
                        isKeyDown);
                }
            }
        }

        return CallNextHookEx(
            _keyboardHook,
            nCode,
            wParam,
            lParam);
    }

    private void UninstallKeyboardHook()
    {
        if (_keyboardHook == IntPtr.Zero)
        {
            return;
        }

        bool result =
            UnhookWindowsHookEx(
                _keyboardHook);

        if (!result)
        {
            int error =
                Marshal.GetLastWin32Error();

            _logger.LogWarning(
                "UnhookWindowsHookEx failed: {Error}",
                error);
        }

        _keyboardHook = IntPtr.Zero;
    }

    public override void Dispose()
    {
        StopKeyboardHookThread();

        base.Dispose();
    }

    private delegate IntPtr LowLevelKeyboardProc(
        int nCode,
        IntPtr wParam,
        IntPtr lParam);

    [Flags]
    private enum LowLevelKeyboardFlags : uint
    {
        Extended = 0x01,
        LowerIntegrityInjected = 0x02,
        Injected = 0x10,
        AltDown = 0x20,
        Up = 0x80
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct LowLevelKeyboardInput
    {
        public uint VirtualKeyCode;
        public uint ScanCode;
        public LowLevelKeyboardFlags Flags;
        public uint Time;
        public UIntPtr ExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Message
    {
        public IntPtr WindowHandle;
        public uint MessageId;
        public UIntPtr WParam;
        public IntPtr LParam;
        public uint Time;
        public Point Point;
        public uint Private;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Point
    {
        public int X;
        public int Y;
    }

    [DllImport(
        "user32.dll",
        SetLastError = true)]
    private static extern IntPtr SetWindowsHookEx(
        int idHook,
        LowLevelKeyboardProc callback,
        IntPtr moduleHandle,
        uint threadId);

    [DllImport(
        "user32.dll",
        SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool UnhookWindowsHookEx(
        IntPtr hook);

    [DllImport("user32.dll")]
    private static extern IntPtr CallNextHookEx(
        IntPtr hook,
        int nCode,
        IntPtr wParam,
        IntPtr lParam);

    [DllImport(
        "user32.dll",
        SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PeekMessage(
        out Message message,
        IntPtr windowHandle,
        uint minimumMessage,
        uint maximumMessage,
        uint removeMessage);

    [DllImport(
        "user32.dll",
        SetLastError = true)]
    private static extern int GetMessage(
        out Message message,
        IntPtr windowHandle,
        uint minimumMessage,
        uint maximumMessage);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool TranslateMessage(
        ref Message message);

    [DllImport("user32.dll")]
    private static extern IntPtr DispatchMessage(
        ref Message message);

    [DllImport(
        "user32.dll",
        SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PostThreadMessage(
        uint threadId,
        uint message,
        UIntPtr wParam,
        IntPtr lParam);

    [DllImport(
        "kernel32.dll",
        CharSet = CharSet.Auto,
        SetLastError = true)]
    private static extern IntPtr GetModuleHandle(
        string? moduleName);

    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();
}
