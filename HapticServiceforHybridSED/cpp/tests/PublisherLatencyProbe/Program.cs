using System.Diagnostics;
using System.Text.Json;
using HybridSedPrototype.Contracts;
using HybridSedPrototype.SemanticEventDispatcher.Configuration;
using HybridSedPrototype.SemanticEventDispatcher.Models;
using HybridSedPrototype.SemanticEventDispatcher.Publishers;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using System.Runtime.Versioning;

[assembly: SupportedOSPlatform("windows")]

// Test-only entry: already-classified events, no SendInput, keyboard hook or
// touchscreen. Reference the production publisher rather than copying it.
if (args.Length != 2) throw new ArgumentException("Expected PIPE TRACE_NAME");
Thread.CurrentThread.Priority = ThreadPriority.AboveNormal;
using var publisher = new HapticPipePublisher(Options.Create(new DispatcherOptions {
    HapticPipeName = args[0], HapticLatencyTraceName = args[1],
    HapticSenderNativeWake = true, LatencyThreadPriorityBoost = true
}), NullLogger<HapticPipePublisher>.Instance);
Console.WriteLine(JsonSerializer.Serialize(new { ready=true, frequency=Stopwatch.Frequency }));
while (Console.ReadLine() is string line) {
    using var request = JsonDocument.Parse(line);
    int count = request.RootElement.GetProperty("count").GetInt32();
    var pattern = request.RootElement.GetProperty("pattern_ms").EnumerateArray().Select(v=>v.GetInt32()).ToArray();
    if (count < 1 || count > 100 || pattern.Length == 0 || pattern.Any(v=>v<1||v>5000))
        throw new ArgumentException("Invalid bounded probe request");
    var entries = new long[count];
    for (int i=0; i<count; ++i) {
        var value = new SemanticEvent { EventType=SemanticEventType.Haptic, HapticEvent=(HapticEventType)(1+i%3) };
        entries[i]=Stopwatch.GetTimestamp();
        publisher.Publish(value);
        Thread.Sleep(pattern[i%pattern.Length]);
    }
    Console.WriteLine(JsonSerializer.Serialize(new { entries }));
}
