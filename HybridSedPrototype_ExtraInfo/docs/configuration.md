> Earlier baseline. Current edge filtering, threading and service deployment: [integration guide](cpp-haptic-integration.md).

# Configuration

Settings live in `src/SemanticEventDispatcher/appsettings.json` under the `Dispatcher` section. They bind to `DispatcherOptions` (`DispatcherOptions.SectionName` is `"Dispatcher"`).

`dotnet run --project src/SemanticEventDispatcher` sets the content root to the project directory, so that file is loaded. The Worker SDK also copies `appsettings.json` next to the built executable. A process whose content root is the repository root, such as `dotnet exec` launched from the root, does not see the project file and falls back to the property initializers in `DispatcherOptions`.

Those initializers are the previous hardcoded prototype values. They match the checked-in JSON except for the voice key:

| Setting | `DispatcherOptions` initializer | Checked-in `appsettings.json` |
| --- | --- | --- |
| `VoiceKeyVirtualKey` | `123` (F12, `0x7B`) | `135` (F24, `0x87`) |
| `ModifierKeys` | 16, 17, 18, 160–165 | Same list |
| `SpecialKeys` | 32, 13, 8 | Same list |
| `HapticPipeName` | `HybridSedHaptic` | `HybridSedHaptic` |
| `HapticPipeConnectTimeoutMs` | `20` | `20` |
| `WmiNamespace` | `root\wmi` | `root\wmi` |
| `WmiClassName` | `VoiceKeyModeSwitchEvent` | `VoiceKeyModeSwitchEvent` |
| `WmiSource` | `HybridSED` | `HybridSED` |

The test `Default_voice_key_remains_F12` constructs `new DispatcherOptions()` and expects 123. It does not read `appsettings.json`. Changing the JSON does not require a test change. Changing the initializer does.

The JSON file allows comments (`//`). The comments above `VoiceKeyVirtualKey` still say to leave 123 in place while development continues, and to change the number to 135 for OSK. The property is already 135. When you edit the number, edit those two comment lines in the same change. A later reader who follows the comment and reverts the property will put the running process back on F12.

Command-line keys override the file. The generic host binds `--Dispatcher:<Property>=<value>` onto the same section:

```text
dotnet run --project src/SemanticEventDispatcher/SemanticEventDispatcher.csproj -- --Dispatcher:VoiceKeyVirtualKey=123
```

That command selects the F12 fallback for one run. Omit it to use F24 from the checked-in file. Confirm the bound value in the startup line `Voice key VK=...`.

Array settings replace the whole array. `--Dispatcher:ModifierKeys:0=16` is the configuration binder's index syntax. Replacing the list in JSON is the straightforward edit.

## Dispatcher section

| Setting | JSON | Initializer | Meaning |
| --- | --- | --- | --- |
| `VoiceKeyVirtualKey` | `135` | `123` | Virtual-key code for voice. 123 is F12 (`VK_F12`, `0x7B`). 135 is F24 (`VK_F24`, `0x87`). Key down publishes recording start. Key up publishes recording stop. The same key also classifies as haptic `SpecialKey`. |
| `ModifierKeys` | 16, 17, 18, 160–165 | Same | Shift (`0x10`), Control (`0x11`), Alt (`0x12`), and the left/right variants (`0xA0`–`0xA5`). Each emits haptic `ModifierKey`. |
| `SpecialKeys` | 32, 13, 8 | Same | Space (`VK_SPACE`), Enter (`VK_RETURN`), Backspace (`VK_BACK`). Each emits haptic `SpecialKey`. |
| `HapticPipeName` | `HybridSedHaptic` | `HapticPipeProtocol.PipeName` | Pipe the publisher connects to. The consumer reads `HapticPipeProtocol.PipeName` and does not read this file. A null, empty, or whitespace JSON value also falls back to that constant inside `HapticPipePublisher`. |
| `HapticPipeConnectTimeoutMs` | `20` | `20` | How long one connect attempt may block, in milliseconds. Values below 1 are treated as 20. The constructor attempt runs on the host startup thread. Later attempts run on the hook thread, and only when a haptic event is actually published. |
| `WmiNamespace` | `root\wmi` | `VoiceWmiProtocol.NamespacePath` | WMI namespace for the voice event. |
| `WmiClassName` | `VoiceKeyModeSwitchEvent` | `VoiceWmiProtocol.ClassName` | Event class. The class must already exist. |
| `WmiSource` | `HybridSED` | `VoiceWmiProtocol.DefaultSource` | Written to the `Source` property of each WMI event. |

`ModifierKeys` and `SpecialKeys` are copied into `HashSet<int>` once, when `InputEventClassifier` is constructed. Restart the process after editing them. A null array on the options object is treated as an empty set, which means every non-voice key classifies as `NormalKey`. Omitting the key in JSON keeps the initializer, because the binder leaves the existing array in place.

## Values that are not settings

These stay in code so a config edit cannot silently break the consumers, or so the rule stays next to the hook.

| Value | Location | Notes |
| --- | --- | --- |
| WMI Mode 0 = recording start, Mode 1 = recording stop | `VoiceWmiProtocol` | Mapped from `VoiceEventType` only in `WmiVoicePublisher`. |
| Haptic bytes 1–4 | `HapticEventType` | Byte 4 (`TouchPad`) is reserved. |
| Consumer pipe name | `HapticPipeProtocol.PipeName` | Shared with the publisher's fallback. |
| Which edge is start vs stop | `InputEventClassifier` | Down = start, up = stop. No "already down" state. |
| Voice key also emits haptic `SpecialKey` | `InputEventClassifier` | Emitted even when the haptic event will be skipped later. |
| OSK marker `0x5345445F4F534B` | `Worker.OskExtraInfoMarker` | Haptic events are routed only when `dwExtraInfo` equals this value. Voice does not consult it. |
| Which hook flags count as injected | `Worker.KeyboardHookCallback` | `LLKHF_INJECTED` (`0x10`) or `LLKHF_LOWER_IL_INJECTED` (`0x02`). |

Changing `HapticPipeName` in JSON without changing `HapticPipeProtocol.PipeName` makes the publisher connect to a pipe the consumer is not serving.

Changing `WmiClassName` or `WmiNamespace` in JSON does not change `VoiceKeyWMIEvent_SED.py` or the MOF files. The publisher will `Put` whatever class name is configured. The listener and the compiled class stay where they were.

Changing the voice key in JSON does not change the OSK marker, and changing the marker does not change the voice key. F24 and `0x5345445F4F534B` are two separate agreements with OSK. See [osk-voice-integration.md](osk-voice-integration.md).

## What a running process is using

Startup logs the bound voice key, WMI namespace, WMI class, and pipe name. It does not log the modifier list, the special-key list, the connect timeout, or the OSK marker. If a machine is misbehaving, compare that line with `appsettings.json` and with any `--Dispatcher:` arguments on the command line.

The dispatcher csproj sets `UserSecretsId`. `Host.CreateApplicationBuilder` loads user secrets when `DOTNET_ENVIRONMENT` is `Development`. A default `dotnet run` uses Production, so those secrets stay unloaded. This repository does not commit any. In Development, a secret named `Dispatcher:VoiceKeyVirtualKey` would override the JSON file. Command-line arguments still win over both.
