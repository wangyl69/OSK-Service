using System.IO.Pipes;
using HybridSedPrototype.Contracts;

// Pipe name and event codes live in HybridSed.Contracts.

Console.WriteLine("HapticPipeConsumer started.");
Console.WriteLine($"Pipe: {HapticPipeProtocol.PipeName}");

while (true)
{
    using NamedPipeServerStream pipe = new(
        HapticPipeProtocol.PipeName,
        PipeDirection.In,
        1,
        PipeTransmissionMode.Byte);

    Console.WriteLine("Waiting for connection...");

    pipe.WaitForConnection();

    Console.WriteLine("Client connected.");

    while (pipe.IsConnected)
    {
        int value = pipe.ReadByte();

        if (value < 0)
        {
            break;
        }

        Console.WriteLine($"Received: {value} ({Describe(value)})");
    }

    Console.WriteLine("Client disconnected.");
}

static string Describe(int value)
{
    if (value < 0 || value > byte.MaxValue)
    {
        return $"Unknown({value})";
    }

    var code = (HapticEventType)(byte)value;
    return Enum.IsDefined(code)
        ? code.ToString()
        : $"Unknown({value})";
}
