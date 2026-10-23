> Earlier baseline. Current edge filtering, threading and service deployment: [integration guide](cpp-haptic-integration.md).

# WMI and named-pipe contracts

Shared C# constants for the pipe and for WMI modes are in `src/HybridSed.Contracts`. The Python listener and the MOF files repeat the WMI names in their own text. Change both sides together.

The OSK `dwExtraInfo` marker is a third contract. It is not in `HybridSed.Contracts`. It is the private constant `OskExtraInfoMarker` in `src/SemanticEventDispatcher/Worker.cs`. OSK and that constant must change together.

## Voice WMI

| Item | Value |
| --- | --- |
| Namespace | `root\wmi` (`VoiceWmiProtocol.NamespacePath`, overridable by `WmiNamespace`) |
| Class | `VoiceKeyModeSwitchEvent` (`VoiceWmiProtocol.ClassName`, overridable by `WmiClassName`) |
| `Mode` | `0` recording start, `1` recording stop |
| `TimeStamp` | DMTF datetime, UTC, set at publish (`ManagementDateTimeConverter.ToDmtfDateTime(DateTime.UtcNow)`) |
| `Source` | `HybridSED` by default (`VoiceWmiProtocol.DefaultSource`, overridable by `WmiSource`) |

Who may publish:

| Key | Publishes WMI |
| --- | --- |
| Configured voice key, key down | Mode 0. Physical, injected, marker, or no marker. |
| Configured voice key, key up | Mode 1. Same. |
| Any other key | No WMI event. |

`VoiceEventType` uses 1 (`RecordingStart`) and 2 (`RecordingStop`) inside the process. Those numbers are not the WMI mode values. The mapping is only in `WmiVoicePublisher.Publish`.

The publisher creates a class instance and calls `Put`. It does not register a WMI provider. The class must already be compiled. Each publish connects a new `ManagementScope`. A failure throws on the keyboard-hook thread. See [known-limits.md](known-limits.md).

MOF files in `mof/`:

| File | Contents |
| --- | --- |
| `voice_key.mof` | Full definition: abstract `VoiceKeyBaseMode`, extrinsic event qualifiers on `VoiceKeyModeSwitchEvent` (`Mode`, `TimeStamp`, `Source`), plus `VoiceKeyEventProvider` and `VoiceKeyEventProviderRegistration`. The publisher does not use the provider classes. |
| `voice_key_sed.mof` | Smaller extrinsic class (`[EventType(1), Dynamic]`) with the same three properties. |
| `voice_key_sed_V2.mof` | Variant without the extrinsic-event qualifiers. |
| `voice_key_sed_v3.mof` | Variant that marks `TimeStamp` as a key. |

The publisher requires a class named `VoiceKeyModeSwitchEvent` with writable `Mode`, `TimeStamp`, and `Source`. `Mode` is inherited from `VoiceKeyBaseMode` in all four files. Pick one MOF as the image standard before handoff and leave the others unused. This repository does not delete them, because an already-registered class may have been compiled from any of the four.

Compile from an elevated prompt, from the repository root, using the file you selected:

```text
mofcomp mof\voice_key.mof
```

`voice_key.mof` comments also mention `mofcomp /remove` and deleting the class in `wbemtest`. Removal is not part of the dispatcher.

## Python listener

Use `src/WmiVoiceListener/VoiceKeyWMIEvent_SED.py` with this dispatcher.

It watches `root\wmi` / `VoiceKeyModeSwitchEvent` and reads `Mode`. Mode 0 is recording (`VOICE_RECORDING`). Mode 1 is recorded (`VOICE_RECORDED`). Install `wmi` and `pywin32` first.

| | `VoiceKeyWMIEvent_SED.py` | `VoiceKeyWMIEvent.py` |
| --- | --- | --- |
| Role | Listener for this dispatcher | Earlier copy |
| `watch_for` argument | `wmi_class=...` | `class_name=...` |
| How events are read | Calls the watcher: `raw_event = self._watcher()` | Iterates the watcher: `for raw_event in self._watcher` |
| Namespace and class | `root\wmi`, `VoiceKeyModeSwitchEvent` | Same strings |
| Mode property names it will accept | `Mode`, `BaseMode`, `ModeValue`, `mode` | Same list |

The SED file prints `namespace=` and `event_name=` at start. The earlier file does not. Do not treat the files as interchangeable. If you change Mode 0 / Mode 1, edit `BaseMode` in both files even though only the SED file is the supported listener.

The demo handler prints `Voice recording (mode 0)` and `Voice recorded (mode 1)`. It does not print `TimeStamp` or `Source`. Those values are still on the raw WMI object (`event.raw`).

## OSK `dwExtraInfo`

| Item | Value |
| --- | --- |
| Field | `KBDLLHOOKSTRUCT.dwExtraInfo`, set from `KEYBDINPUT.dwExtraInfo` |
| Marker | `0x5345445F4F534B` |
| ASCII reading | `SED_OSK`, high byte first |
| C# constant | `Worker.OskExtraInfoMarker` (`ulong`) |
| Compared by | `Worker.KeyboardHookCallback` |
| Effect | Haptic events are routed only on a match. Voice is unaffected. |
| Config | None. Not in `appsettings.json`, not in `HybridSed.Contracts`. |

The marker is not written to the named pipe and not written to the WMI event. Consumers cannot see it. They only see the haptic byte, or the WMI properties above.

## Haptic named pipe

| Item | Value |
| --- | --- |
| Pipe name | `HybridSedHaptic` (`HapticPipeProtocol.PipeName`) |
| Server | `HapticPipeConsumer`, `PipeDirection.In`, one instance, byte mode |
| Client | `HapticPipePublisher`, `PipeDirection.Out`, server name `.` |
| Payload | One byte per routed haptic event, then flush |
| Who may send | A key edge whose `dwExtraInfo` equals the marker. Both down and up. |

| Byte | `HapticEventType` | Classified when | Written to the pipe when |
| --- | --- | --- | --- |
| 1 | `NormalKey` | The key is not the voice key, a modifier, or a special key | That edge also carries the OSK marker |
| 2 | `ModifierKey` | Shift, Control, Alt, and the left/right variants (`0x10`, `0x11`, `0x12`, `0xA0`–`0xA5`) | That edge also carries the OSK marker |
| 3 | `SpecialKey` | Space (32), Enter (13), Backspace (8), and the voice key | That edge also carries the OSK marker |
| 4 | `TouchPad` | Reserved. Nothing emits it | Never, until some input creates this event and the gate lets it through |

The voice key is byte 3 because `InputEventClassifier` adds `SpecialKey` after the voice event. It is byte 3 on a physical voice key as well, and that byte is then dropped in `Worker` before `HapticPipePublisher.Publish`.

The consumer prints the enum name for a defined byte and `Unknown(n)` for any other byte. It shares `HapticEventType`, so a new named value appears in the printout without a consumer edit. A byte that is not a defined enum value still prints `Unknown(n)`.

A missing consumer is not fatal. The publisher drops that haptic event and the hook continues. Voice publishing does not depend on the pipe. The first connect attempt happens when the publisher is constructed. Later attempts happen on publish, with a timeout of `HapticPipeConnectTimeoutMs` (default 20, and any value below 1 is treated as 20).

Do not renumber existing haptic values. Add a new code at the end of `HapticEventType`. Update a consumer only if that consumer is not using the shared enum. `HapticPipeConsumer` uses the shared enum.

## Change both sides

| Contract | Dispatcher side | Other side |
| --- | --- | --- |
| WMI namespace, class, Mode 0, Mode 1 | `VoiceWmiProtocol`, `WmiVoicePublisher` | `VoiceKeyWMIEvent_SED.py`, the MOF you compile, and the earlier Python file if it is still deployed |
| WMI `Source` text | `WmiSource` in JSON, fallback `VoiceWmiProtocol.DefaultSource` | Listeners that branch on `Source`. The sample listener does not. |
| Pipe name | `HapticPipeProtocol.PipeName` and `HapticPipeName` | `HapticPipeConsumer` reads the constant |
| Haptic bytes | `HapticEventType` | Any consumer that does not reference the enum |
| OSK marker | `Worker.OskExtraInfoMarker` | The OSK input path that sets `dwExtraInfo` |
