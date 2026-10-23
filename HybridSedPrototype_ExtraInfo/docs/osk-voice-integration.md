> Earlier baseline. Current edge filtering, threading and service deployment: [integration guide](cpp-haptic-integration.md).

# OSK voice integration

Target path:

```text
OSK voice button
  -> F24 down, then F24 up
  -> dwExtraInfo = 0x5345445F4F534B on each edge that should produce haptics
  -> Hybrid SED low-level keyboard hook
  -> voice semantic event for every F24 edge
  -> haptic SpecialKey only when dwExtraInfo matches
  -> WMI VoiceKeyModeSwitchEvent
  -> named pipe HybridSedHaptic (haptic edge only)
```

Voice and haptics are separate agreements. F24 selects the voice key. The marker selects haptic publishing. A voice button that sends F24 and leaves `dwExtraInfo` at 0 still starts and stops recording. It does not produce a pipe byte.

## What to change for the voice key

The checked-in `src/SemanticEventDispatcher/appsettings.json` already sets `VoiceKeyVirtualKey` to `135`. `dotnet run` loads that file. Confirm the process bound it: the startup log contains `Voice key VK=135`.

No classifier change is required for F24. The classifier compares the hooked virtual-key with the configured value. F24 down becomes `VoiceEventType.RecordingStart`. F24 up becomes `VoiceEventType.RecordingStop`. `WmiVoicePublisher` maps those to WMI Mode 0 and Mode 1.

The code fallback in `DispatcherOptions` is still 123 (F12). That value is used when the JSON section is missing. To force F12 for one run:

```text
dotnet run --project src/SemanticEventDispatcher/SemanticEventDispatcher.csproj -- --Dispatcher:VoiceKeyVirtualKey=123
```

The comments in `appsettings.json` still describe 123 as the value to leave in place. The property is 135. If you change the property, change the comments in the same edit.

The dispatcher must be running in a session where the low-level hook can see the key OSK injects. The hook already accepts injected keys. `IsInjected` is recorded on the semantic event and is not used to accept or reject the key.

## What OSK must send

SED classifies a Win32 virtual-key from `WH_KEYBOARD_LL`. It also reads `KBDLLHOOKSTRUCT.dwExtraInfo` for the haptic gate.

For the voice button:

| Field | Value |
| --- | --- |
| Virtual-key | `0x87` (135, F24), on press and on release |
| Message | `WM_KEYDOWN` / `WM_KEYUP`, or the `WM_SYSKEY*` equivalents |
| `dwExtraInfo`, when this edge should move the haptic pipe | `0x5345445F4F534B` |

`0x5345445F4F534B` is the ASCII bytes of `SED_OSK` packed as an integer, high byte first (`53 45 44 5F 4F 53 4B`). Set that number on `KEYBDINPUT.dwExtraInfo`. The hook compares the number. A scan code alone, a Unicode character, a private window message, or the text `SED_OSK` stored as a string is not read.

Send the marker on both the down edge and the up edge if both edges should produce `SpecialKey`. The classifier emits a haptic event for each edge. The gate runs per edge.

The marker check does not require `LLKHF_INJECTED`. Sending the marker is what admits the haptic event. Sending F24 without the marker still admits the voice event.

The constant is `OskExtraInfoMarker` in `src/SemanticEventDispatcher/Worker.cs`. It is not in `appsettings.json` and not in `HybridSed.Contracts`. The dispatcher process should be 64-bit: the value does not fit in a 32-bit `ULONG_PTR`.

## What stays the same after F24 is the voice key

These are existing rules. They are not extra F24 features.

- F24 is observed and still delivered to the focused application. SED always calls `CallNextHookEx`. F24 normally produces no character. Swallowing it would be a code change in `Worker`.
- F24 classifies as haptic `SpecialKey` (byte 3) on every edge. The byte is written to `HybridSedHaptic` only when `dwExtraInfo` is the marker. A physical F24 logs the voice event and does not print `SpecialKey` on the consumer.
- A physical F24 and an OSK-injected F24 both become voice. Filtering voice on `IsInjected` would be a code change. Filtering voice on `dwExtraInfo` would also be a code change. Today the marker filters haptics only.
- With voice key 135, F12 is an ordinary key. It classifies as haptic `NormalKey`, unless F12 is added to `ModifierKeys` or `SpecialKeys`. A physical F12 produces no pipe byte.
- Repeated F24 key-down messages each emit `RecordingStart`. SED does not remember that the voice key is already down. Confirm whether OSK sends one down and one up, or auto-repeat.
- Publishing still runs on the hook thread. See [known-limits.md](known-limits.md).

## How to tell the two gates apart in the log

| Log | Meaning |
| --- | --- |
| `Voice key VK=135` | F24 is the voice key for this process. |
| `VK=135 ... Osk=false` then `Route Voice` | Voice will `Put`. The haptic `SpecialKey` for this edge is skipped. |
| `VK=135 ... Osk=true` then `Route Voice` then `Route Haptic` | Voice will `Put`, and byte 3 will be written if the consumer is connected. |
| `VK=135 ... Injected=true` with `Osk=false` | The key was injected and still failed the haptic gate. The marker was missing or different. |

`Skip haptic publish: not an OSK key` is a debug log. The default console level is Information, so use `Osk=` and the presence or absence of `Route Haptic` instead.

## Files on this path

| File | Role |
| --- | --- |
| `src/SemanticEventDispatcher/appsettings.json` | Voice-key value. Checked-in value is 135. Comments above it are stale relative to that number. |
| `src/SemanticEventDispatcher/Configuration/DispatcherOptions.cs` | Fallback value 123 when the JSON section is missing |
| `src/SemanticEventDispatcher/Worker.cs` | Hook, virtual-key, injected flag, `OskExtraInfoMarker`, haptic skip |
| `src/SemanticEventDispatcher/Inputs/InputEventClassifier.cs` | F24 to voice event, plus haptic `SpecialKey`. No marker logic. |
| `src/SemanticEventDispatcher/Models/SemanticEvent.cs` | Event passed to the router. No `dwExtraInfo` field. |
| `src/SemanticEventDispatcher/Models/VoiceEventType.cs` | `RecordingStart`, `RecordingStop` |
| `src/SemanticEventDispatcher/Routing/SemanticEventRouter.cs` | Fan-out for events `Worker` did not skip |
| `src/SemanticEventDispatcher/Publishers/WmiVoicePublisher.cs` | WMI `Put` |
| `src/SemanticEventDispatcher/Publishers/HapticPipePublisher.cs` | One byte, after the marker check has already passed |
| `src/SemanticEventDispatcher/Program.cs` | Registers the voice publisher, then the haptic publisher |
| `src/HybridSed.Contracts/VoiceWmiProtocol.cs` | Namespace, class name, Mode 0 and Mode 1 |
| `src/HybridSed.Contracts/HapticEventType.cs` | `SpecialKey` = 3 |
| `mof/voice_key.mof`, `mof/voice_key_sed.mof` | Class definitions to compile |
| `src/WmiVoiceListener/VoiceKeyWMIEvent_SED.py` | Sample listener |
| `src/SemanticEventDispatcher.Tests/InputEventClassifierTests.cs` | Covers F24 when the option is 135. Does not cover `dwExtraInfo`. |

## Check on a machine

1. Compile the MOF class if `root\wmi` does not already contain `VoiceKeyModeSwitchEvent`.
2. Start `VoiceKeyWMIEvent_SED.py`.
3. Start `HapticPipeConsumer`, then the dispatcher. Confirm `Voice key VK=135`.
4. Inject one F24 down and one F24 up with `dwExtraInfo = 0x5345445F4F534B` on both edges.
5. Expect Mode 0, then Mode 1. `Source` is `HybridSED` unless `WmiSource` was changed.
6. Expect the consumer to print `Received: 3 (SpecialKey)` on each edge.
7. Inject F24 again with `dwExtraInfo` left at 0. Expect Mode 0 and Mode 1, and no new consumer line. The dispatcher log shows `Osk=false` and `Route Voice` without `Route Haptic`.

`Put` emits a real WMI event. Do that check when a listener is the intended receiver.
