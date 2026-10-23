namespace HybridSedPrototype.Contracts;

/// <summary>
/// WMI contract shared with the Python listener and the MOF class
/// VoiceKeyModeSwitchEvent.
/// Mode 0 is recording start. Mode 1 is recording stop.
/// Those mode numbers are not the VoiceEventType enum values.
/// </summary>
public static class VoiceWmiProtocol
{
    public const string NamespacePath = @"root\wmi";

    public const string ClassName = "VoiceKeyModeSwitchEvent";

    public const string DefaultSource = "HybridSED";

    public const uint RecordingStartMode = 0;

    public const uint RecordingStopMode = 1;
}
