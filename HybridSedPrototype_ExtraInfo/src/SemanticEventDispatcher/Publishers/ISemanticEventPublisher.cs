using HybridSedPrototype.SemanticEventDispatcher.Models;

namespace HybridSedPrototype.SemanticEventDispatcher.Publishers;

/// <summary>
/// One output. The router forwards every semantic event to each registered
/// publisher, and the publisher ignores events it does not own.
/// A future output is a new implementation plus one DI registration.
/// </summary>
public interface ISemanticEventPublisher
{
    void Publish(SemanticEvent semanticEvent);
}
