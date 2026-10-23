using HybridSedPrototype.SemanticEventDispatcher;
using HybridSedPrototype.SemanticEventDispatcher.Configuration;
using HybridSedPrototype.SemanticEventDispatcher.Inputs;
using HybridSedPrototype.SemanticEventDispatcher.Publishers;
using HybridSedPrototype.SemanticEventDispatcher.Routing;

var builder = Host.CreateApplicationBuilder(args);

builder.Logging.ClearProviders();
builder.Logging.AddConsole();

builder.Services.Configure<DispatcherOptions>(
    builder.Configuration.GetSection(DispatcherOptions.SectionName));

builder.Services.AddSingleton<InputEventClassifier>();
builder.Services.AddSingleton<ISemanticEventPublisher, WmiVoicePublisher>();
builder.Services.AddSingleton<ISemanticEventPublisher, HapticPipePublisher>();
builder.Services.AddSingleton<SemanticEventRouter>();

builder.Services.AddHostedService<Worker>();

var host = builder.Build();

host.Run();
