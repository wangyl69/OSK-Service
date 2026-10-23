using System.Management;
using System.Runtime.Versioning;
using System.Threading.Channels;
using HybridSedPrototype.Contracts;
using HybridSedPrototype.SemanticEventDispatcher.Configuration;
using HybridSedPrototype.SemanticEventDispatcher.Models;
using Microsoft.Extensions.Options;

namespace HybridSedPrototype.SemanticEventDispatcher.Publishers;

/// <summary>
/// Fires VoiceKeyModeSwitchEvent in the WMI namespace from configuration.
/// The class must already be compiled with mofcomp. This prototype does not
/// register a WMI provider; Put on the event instance is the publication.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class WmiVoicePublisher : ISemanticEventPublisher, IDisposable
{
    private readonly DispatcherOptions _options;
    private readonly ILogger<WmiVoicePublisher> _logger;
    private readonly Action<uint> _publishMode;
    private readonly Channel<uint> _queue;
    private readonly CancellationTokenSource _stop = new();
    private readonly Task _sender;
    private int _disposed;
    private long _dropped;

    public WmiVoicePublisher(
        IOptions<DispatcherOptions> options,
        ILogger<WmiVoicePublisher> logger)
        : this(options, logger, null)
    {
    }

    // The backend seam permits slow/failing WMI tests without registering a
    // machine-wide WMI class. Production always uses PublishModeSwitch.
    internal WmiVoicePublisher(
        IOptions<DispatcherOptions> options,
        ILogger<WmiVoicePublisher> logger,
        Action<uint>? publishMode)
    {
        _options = options.Value;
        _logger = logger;
        _publishMode = publishMode ?? PublishModeSwitch;
        _queue = Channel.CreateBounded<uint>(new BoundedChannelOptions(32)
        {
            SingleReader = true,
            FullMode = BoundedChannelFullMode.DropOldest
        }, _ => Interlocked.Increment(ref _dropped));
        _sender = Task.Run(SendAsync);
    }

    public void Publish(SemanticEvent semanticEvent)
    {
        if (semanticEvent.EventType != SemanticEventType.Voice)
        {
            return;
        }

        if (semanticEvent.VoiceEvent is not VoiceEventType voiceEvent)
        {
            throw new InvalidOperationException(
                "Voice semantic event is missing VoiceEvent.");
        }

        uint mode = voiceEvent switch
        {
            VoiceEventType.RecordingStart => VoiceWmiProtocol.RecordingStartMode,
            VoiceEventType.RecordingStop => VoiceWmiProtocol.RecordingStopMode,
            _ => throw new ArgumentOutOfRangeException(
                nameof(semanticEvent),
                voiceEvent,
                "Unknown voice event.")
        };

        // Enqueue only: COM/WMI must never run on the low-level hook thread.
        // Retained transitions stay ordered. Under overload, replace the oldest
        // pending state so a final RecordingStop can never be rejected merely
        // because earlier starts filled the bounded queue.
        if (!_queue.Writer.TryWrite(mode))
            Interlocked.Increment(ref _dropped);
    }

    private async Task SendAsync()
    {
        try
        {
            await foreach (uint mode in _queue.Reader.ReadAllAsync(_stop.Token))
            {
                if (_stop.IsCancellationRequested) break;
                try { _publishMode(mode); }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Failed to publish queued voice mode {Mode}", mode);
                }
            }
        }
        catch (OperationCanceledException) { }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
        _queue.Writer.TryComplete();
        // A synchronous COM call cannot be forcibly cancelled. Bound host
        // shutdown, first allowing ordered drain (including queued Stop).
        // Defer CTS disposal until the actual sender has ended.
        if (_sender.Wait(TimeSpan.FromSeconds(2)))
            _stop.Dispose();
        else
        {
            _stop.Cancel();
            _logger.LogWarning("WMI call is still running; sender will finish after it returns.");
            _ = _sender.ContinueWith(_ => _stop.Dispose(), TaskScheduler.Default);
        }
        _logger.LogInformation("Voice publisher stopped accepting events. OverloadDropped={Dropped}", _dropped);
    }

    private void PublishModeSwitch(uint mode)
    {
        var scope = new ManagementScope(_options.WmiNamespace);
        scope.Connect();

        using var managementClass = new ManagementClass(
            scope,
            new ManagementPath(_options.WmiClassName),
            null);

        using ManagementObject? eventInstance = managementClass.CreateInstance();

        if (eventInstance == null)
        {
            throw new InvalidOperationException(
                "Failed to create VoiceKeyModeSwitchEvent instance.");
        }

        eventInstance["Mode"] = mode;
        eventInstance["TimeStamp"] =
            ManagementDateTimeConverter.ToDmtfDateTime(DateTime.UtcNow);
        eventInstance["Source"] = _options.WmiSource;

        _logger.LogDebug(
            "Created WMI instance. Class={ClassName} Mode={Mode} Source={Source}",
            eventInstance.ClassPath.ClassName,
            mode,
            _options.WmiSource);

        eventInstance.Put();
    }
}
