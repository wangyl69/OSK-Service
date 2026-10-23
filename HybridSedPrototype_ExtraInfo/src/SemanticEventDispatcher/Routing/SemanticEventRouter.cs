using HybridSedPrototype.SemanticEventDispatcher.Models;
using HybridSedPrototype.SemanticEventDispatcher.Publishers;

namespace HybridSedPrototype.SemanticEventDispatcher.Routing;

/// <summary>
/// Forwards each semantic event to every registered publisher.
/// Voice and haptic transport details stay inside the publishers.
/// If a publisher throws, later publishers do not see that event.
/// The hook callback logs the exception and still calls CallNextHookEx.
/// </summary>
public sealed class SemanticEventRouter
{
    private readonly IReadOnlyList<ISemanticEventPublisher> _publishers;
    private readonly ILogger<SemanticEventRouter> _logger;

    public SemanticEventRouter(
        IEnumerable<ISemanticEventPublisher> publishers,
        ILogger<SemanticEventRouter> logger)
    {
        _publishers = publishers.ToArray();
        _logger = logger;
    }

    public void Route(SemanticEvent semanticEvent)
    {
        if (_logger.IsEnabled(LogLevel.Debug)) _logger.LogDebug(
            "Route {EventType} from {Source} Injected={IsInjected}",
            semanticEvent.EventType,
            semanticEvent.Source,
            semanticEvent.IsInjected);

        foreach (ISemanticEventPublisher publisher in _publishers)
        {
            publisher.Publish(semanticEvent);
        }
    }
}
