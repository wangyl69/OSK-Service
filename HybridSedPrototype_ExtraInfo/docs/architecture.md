> Earlier baseline. Current edge filtering, threading and service deployment: [integration guide](cpp-haptic-integration.md).

# Architecture

Hybrid SED is one process that observes keys, plus two external consumers.

```text
WH_KEYBOARD_LL
  Worker.KeyboardHookCallback
    read virtual-key, LLKHF_* injected flags, dwExtraInfo
    InputEventClassifier
      SemanticEvent (one key can produce more than one)
    for each semantic event:
      haptic AND dwExtraInfo != 0x5345445F4F534B -> skip (debug log)
      otherwise SemanticEventRouter.Route
        WmiVoicePublisher      -> root\wmi VoiceKeyModeSwitchEvent
        HapticPipePublisher    -> named pipe HybridSedHaptic
                                   HapticPipeConsumer
VoiceKeyWMIEvent_SED.py       -> reads the WMI event
```

Voice and haptic are split in two different places.

| Question | Answer | File |
| --- | --- | --- |
| Is this the voice key, a modifier, a special key, or a normal key? | `InputEventClassifier` | `Inputs/InputEventClassifier.cs` |
| May this haptic event leave the process? | `Worker`, by comparing `dwExtraInfo` | `Worker.cs` |
| Which publisher accepts it? | `WmiVoicePublisher` accepts voice. `HapticPipePublisher` accepts haptic. Each ignores the other type. | `Publishers/` |

`SemanticEvent` does not carry `dwExtraInfo`. The marker comparison happens in the hook callback and is not repeated by the router or the publishers. `IsInjected` is a different field. Routing does not read it.

## Projects

`HybridSed.Contracts` (`RootNamespace` `HybridSedPrototype.Contracts`) holds values that cross a process boundary: the pipe name, the haptic byte codes, and the WMI mode numbers. The dispatcher and the haptic consumer both reference it. The Python listener does not. `VoiceKeyWMIEvent_SED.py` repeats the class name and mode numbers, and those copies must be kept aligned by hand. The OSK marker is not in this project. It lives only in `Worker`.

`SemanticEventDispatcher` is a .NET generic host (`Microsoft.Extensions.Hosting` 8.0.1). `Program.cs` clears other logging providers and adds the console logger. There is no `Logging` section in `appsettings.json`, so the default level is Information. Debug lines, including the non-OSK haptic skip, stay off unless a filter is added.

`Program.cs` registers the two publishers, then the router, then the worker:

1. `WmiVoicePublisher`
2. `HapticPipePublisher`
3. `SemanticEventRouter` (constructor receives both publishers)
4. `Worker`

Publish order is the order of the `ISemanticEventPublisher` registrations. The router and the worker are not publishers.

| Type | Responsibility |
| --- | --- |
| `Worker` | Installs `WH_KEYBOARD_LL` (13) on its own thread and pumps a Win32 message loop. That thread is required for a low-level hook. Compares `dwExtraInfo` and skips non-OSK haptic events before `Route`. |
| `InputEventClassifier` | Maps a virtual-key transition to semantic events. Key codes come from `DispatcherOptions`. It does not know about `dwExtraInfo`. |
| `SemanticEventRouter` | Forwards each semantic event it receives to every registered `ISemanticEventPublisher`. |
| `WmiVoicePublisher` | Ignores non-voice events. Publishes Mode 0 or Mode 1. |
| `HapticPipePublisher` | Ignores non-haptic events. Writes one byte. Drops the event if the consumer is absent. |

A publisher that throws stops later publishers for that event. The hook callback logs the exception and still calls `CallNextHookEx`. Events later in the same key's list are not routed, because the `catch` wraps the whole classify-and-loop, not each `Route` call.

## Hook callback

`KeyboardHookCallback` handles four messages. Anything else is ignored and passed on. `nCode < 0` is passed on and not classified.

| Message | Value | Treated as |
| --- | --- | --- |
| `WM_KEYDOWN` | `0x0100` | Key down |
| `WM_KEYUP` | `0x0101` | Key up |
| `WM_SYSKEYDOWN` | `0x0104` | Key down |
| `WM_SYSKEYUP` | `0x0105` | Key up |

From `KBDLLHOOKSTRUCT` (marshaled as `LowLevelKeyboardInput`) the callback reads:

| Field | Stored as | Used for |
| --- | --- | --- |
| `vkCode` | `SemanticEvent` classification input | Voice and haptic type |
| `flags` bit `0x10` (`LLKHF_INJECTED`) or `0x02` (`LLKHF_LOWER_IL_INJECTED`) | `SemanticEvent.IsInjected` | Logged. Not a gate. |
| `dwExtraInfo` | Compared in the callback only | Haptic publish gate |
| `scanCode` | Unread | Classification uses the virtual-key |
| `time` | Unread | WMI `TimeStamp` is set later, at `Put` |

The hook thread is named `Hybrid SED Keyboard Hook Thread` and is a background thread. Shutdown posts `WM_QUIT` (`0x0012`) to that thread and joins for up to 2000 ms. The hook procedure delegate is stored on `Worker` so the garbage collector cannot collect it while the native hook is installed.

`SetWindowsHookEx` is called with thread id 0 (a global hook) and the current process main-module handle.

## OSK haptic gate

```text
OskExtraInfoMarker = 0x5345445F4F534B
```

Byte by byte, from the high byte: `53 45 44 5F 4F 53 4B`, which is the ASCII text `SED_OSK`. OSK must place that numeric value in `KEYBDINPUT.dwExtraInfo` / `KBDLLHOOKSTRUCT.dwExtraInfo`. A string in memory is not what the callback compares.

For each semantic event from the classifier:

- `EventType == Haptic` and `dwExtraInfo` is not the marker: log at Debug (`Skip haptic publish: not an OSK key`) and do not call `Route`.
- Every voice event: call `Route`, marker or not.
- `EventType == Haptic` and `dwExtraInfo` is the marker: call `Route`.

The gate does not require `IsInjected`. A key with the marker is published as haptic even if the injected flag is clear. An injected key without the marker produces no pipe byte.

The marker is a `ulong` constant. It does not fit in 32 bits. Run the dispatcher as a 64-bit process so `ULONG_PTR` can hold it. There is no appsettings key for the marker.

## Semantic events

`SemanticEvent` is one classified result.

| Property | Meaning |
| --- | --- |
| `EventType` | `Voice` (1) or `Haptic` (2). |
| `VoiceEvent` | Set only for voice events. `RecordingStart` (1) on key down, `RecordingStop` (2) on key up. These numbers are not the WMI mode values. |
| `HapticEvent` | Set only for haptic events. The numeric value is the pipe byte. |
| `Source` | `LowLevelKeyboardHook` (1) today. Routing does not branch on it. |
| `IsInjected` | True when the hook reports `LLKHF_INJECTED` or `LLKHF_LOWER_IL_INJECTED`. Routing does not branch on it. OSK input often sets this bit. The haptic gate uses `dwExtraInfo` instead. |

The voice key produces two events, in this order: a voice event, then haptic `SpecialKey`. Both are created for every voice-key edge. Only the second one is subject to the OSK gate.

## Classification rules

These rules are in `InputEventClassifier`. The key lists are configuration. The rules are code. `dwExtraInfo` is not an input to this class.

1. The configured voice key emits `RecordingStart` on key down and `RecordingStop` on key up, plus haptic `SpecialKey`.
2. A modifier virtual-key emits haptic `ModifierKey`.
3. Space, Enter, and Backspace emit haptic `SpecialKey`. This check is independent of the voice-key rule. The default lists do not overlap. Putting the voice key into `SpecialKeys` emits a second `SpecialKey`.
4. Any other key emits haptic `NormalKey`. A key that already produced a voice or haptic event does not also produce `NormalKey`.
5. Key down and key up both produce haptic events. Only the voice event differs between the two edges.

If the voice key is also listed as a modifier, the classifier emits voice, haptic `SpecialKey`, and haptic `ModifierKey`, in that order. That overlap is not used by the default key lists. The OSK gate then applies to each haptic event on its own. The voice event is still routed.

`HapticEventType.TouchPad` (byte 4) is reserved. No input emits it.

Default key codes (also listed in [configuration.md](configuration.md)):

| Role | Virtual-key | Decimal | Name |
| --- | --- | --- | --- |
| Voice, code fallback | `0x7B` | 123 | F12 |
| Voice, checked-in JSON | `0x87` | 135 | F24 |
| Modifier | `0x10`, `0x11`, `0x12` | 16, 17, 18 | Shift, Control, Alt |
| Modifier | `0xA0`–`0xA5` | 160–165 | Left/right Shift, Control, Alt |
| Special | `0x20`, `0x0D`, `0x08` | 32, 13, 8 | Space, Enter, Backspace |

## Publishers

`WmiVoicePublisher` ignores non-voice events. A voice event with no `VoiceEvent` value throws. The mapping to the wire is only here:

| `VoiceEventType` | WMI `Mode` |
| --- | --- |
| `RecordingStart` | `0` (`VoiceWmiProtocol.RecordingStartMode`) |
| `RecordingStop` | `1` (`VoiceWmiProtocol.RecordingStopMode`) |

Each publish creates a new `ManagementScope`, calls `Connect`, creates a `VoiceKeyModeSwitchEvent` instance, sets `Mode`, `TimeStamp` (UTC, DMTF, at publish time), and `Source`, logs `Created WMI instance`, then calls `Put`. The class must already exist. This prototype does not register a WMI provider. Namespace, class name, and source string come from `DispatcherOptions`.

`HapticPipePublisher` ignores non-haptic events. A haptic event with no `HapticEvent` value, or a value outside the enum, throws. The byte written is `(byte)HapticEventType`. The client connects to server `.`, pipe name from options, `PipeDirection.Out`. A blank pipe name falls back to `HapticPipeProtocol.PipeName` (`HybridSedHaptic`). A connect timeout below 1 is treated as 20 milliseconds. The constructor tries once, on the host startup thread. Later retries run inside `Publish`, which today means the hook thread, and only for events `Worker` did not skip. A missing consumer or a write failure drops that event, logs a warning, and leaves the hook running. On write failure the client is disposed so the next event can connect again.

`HapticPipeConsumer` is a separate process. It creates one `NamedPipeServerStream` (`PipeDirection.In`, one instance, byte mode) on `HapticPipeProtocol.PipeName`, reads a byte at a time, and prints `Received: <n> (<enum name>)`. An undefined byte prints `Unknown(n)`. When the client disconnects it waits for a new connection. It does not read `appsettings.json`.

## How a key shows up in the logs

Read the console from top to bottom. These lines are Information unless noted.

| Line | Who | When it appears |
| --- | --- | --- |
| `Hybrid Semantic Event Dispatcher started.` | `Worker` | Process start |
| `Voice key VK=...` | `Worker` | Process start. This is the value that was actually bound. |
| `WMI root\wmi VoiceKeyModeSwitchEvent. Named pipe HybridSedHaptic.` | `Worker` | Process start |
| `Keyboard hook thread started.` | `Worker` | Hook installed |
| `Connected to haptic pipe HybridSedHaptic.` | `HapticPipePublisher` | Consumer was listening at startup or at a later publish |
| `VK=... Osk=...` | `Worker` | Every handled key edge |
| `Classify VK=...` | `InputEventClassifier` | Every handled key edge |
| `Skip haptic publish: not an OSK key.` | `Worker` | Debug only. Non-OSK haptic event. |
| `Route Voice ...` or `Route Haptic ...` | `SemanticEventRouter` | Events that passed the gate |
| `Created WMI instance. Class=... Mode=... Source=...` | `WmiVoicePublisher` | Just before `Put` |
| `Haptic event dropped. Code=...` | `HapticPipePublisher` | Warning. Consumer absent, or the write failed. |
| `Received: 3 (SpecialKey)` | `HapticPipeConsumer` | One pipe byte |

A physical F24 with the checked-in settings produces `Osk=false`, `Route Voice`, and `Created WMI instance`. It does not produce `Route Haptic` or a consumer line.

## What is deliberately not here

There is one input implementation: `WH_KEYBOARD_LL`. `InputSource` has a single value, `LowLevelKeyboardHook`. The comment on that enum says `IsInjected` is the bit to use when physical keys and injected keys are split later. The OSK haptic gate does not use that bit. Touchscreen, touchpad, and pen are not implemented. `HapticEventType.TouchPad` reserves the byte and nothing emits it.

There is no output framework beyond `ISemanticEventPublisher`. Logging, telemetry, an AI agent, or application control would be another publisher plus one `AddSingleton<ISemanticEventPublisher, ...>()` registration in `Program.cs`, next to the existing publishers. The router calls publishers in that registration order.

The hook callback publishes synchronously. See [known-limits.md](known-limits.md).

## Editing checklist

Change the smallest file that owns the rule, then update the twin that would otherwise drift.

| You want to... | Edit | Also update |
| --- | --- | --- |
| Change which key is voice, modifier, or special | `appsettings.json` for the running process | `DispatcherOptions` only if the fallback should change. `Default_voice_key_remains_F12` only if the initializer changes. The JSON comments, so they match the number. |
| Change the OSK marker or stop filtering haptics | `Worker.OskExtraInfoMarker` and the `if` in `KeyboardHookCallback` | The OSK sender. This value is not in contracts, JSON, or the current tests. |
| Change a classification rule | `InputEventClassifier` | `InputEventClassifierTests`. The OSK gate will still drop the haptic unless the key carries the marker. |
| Add a haptic byte | Next value at the end of `HapticEventType` | Do not renumber 1–4. `Haptic_wire_codes_stay_stable` asserts those bytes. The consumer already shares the enum. |
| Change WMI Mode 0 / 1 | `VoiceWmiProtocol` and the switch in `WmiVoicePublisher` | `VoiceKeyWMIEvent_SED.py`, `VoiceKeyWMIEvent.py`, and the MOF file you compile. `Voice_wmi_modes_stay_stable` asserts the constants. |
| Rename the pipe | `HapticPipeProtocol.PipeName` and `HapticPipeName` in JSON | The consumer reads the constant, not the JSON. A JSON-only edit points the publisher at a pipe the consumer is not serving. |
| Add an output | New `ISemanticEventPublisher`, one `AddSingleton` in `Program.cs` | Remember which events `Worker` actually routes. A throwing publisher blocks publishers registered after it. |
| Add an input | `Worker` today. There is no input plugin. | `InputSource` when a second source exists. Set `Source` from that path. |
