> Current integration (2026-10-22): marked OSK KeyDown only; independent asynchronous haptic-pipe and WMI publishers; independent C++ Service, no Agent. Per-key logs require Debug. See [current guide](docs/cpp-haptic-integration.md). The remainder describes the earlier baseline.

# Hybrid SED

面向首次使用者的当前步骤见 [Panel 与完整链路操作指南](docs/panel-first-run.md)：包含 OSK 配置框、显示缩放、触摸坐标映射、C++ Service 启动及诊断。下文旧版 Consumer 和双边沿示例请按顶部历史基线说明理解。

Hybrid Semantic Event Dispatcher is a Windows prototype. It watches a low-level keyboard hook, classifies key transitions into semantic events, and publishes those events to the two outputs that exist today:

- Voice events go to WMI (`VoiceKeyModeSwitchEvent`). Every down and up of the configured voice key is published, whether that key is physical or injected.
- Haptic events go to the named pipe `HybridSedHaptic` only when `KBDLLHOOKSTRUCT.dwExtraInfo` equals `0x5345445F4F534B`. That value is the ASCII bytes of `SED_OSK` packed into an integer, high byte first. `Worker` drops every other haptic event before the router runs.

The running voice key and the code fallback are different numbers. Keep them separate when you edit:

| Layer | Voice key | Where |
| --- | --- | --- |
| Checked-in `appsettings.json` | **F24**, virtual-key 135 (`0x87`) | Loaded by `dotnet run` |
| `DispatcherOptions` property initializer | **F12**, virtual-key 123 (`0x7B`) | Used when the JSON section or key is missing |

The comments in `appsettings.json` still say to leave 123 in place. The property in that file is 135. Trust the number, and update the comment in the same edit. Details are in [docs/configuration.md](docs/configuration.md) and [docs/osk-voice-integration.md](docs/osk-voice-integration.md).

This is a prototype for handoff. It is intentionally small. Add an output by registering another publisher. Add a second input only when that input exists. The OSK haptic gate lives in `Worker`, ahead of the router, so a new publisher does not receive haptic events from keys that lack the marker.

## Requirements

- Windows
- .NET 8 SDK, for the dispatcher, the contracts, and the tests
- .NET 10 SDK, for `HapticPipeConsumer`
- A 64-bit dispatcher process. The OSK marker does not fit in a 32-bit `ULONG_PTR`
- The WMI class `VoiceKeyModeSwitchEvent` in `root\wmi`, compiled with `mofcomp` before voice publishing will succeed
- Python 3 with `wmi` and `pywin32`, only if you run the WMI listener

## Build and test

From the repository root:

```text
dotnet build HybridSedPrototype.slnx
dotnet test HybridSedPrototype.slnx
```

`HybridSedPrototype.slnx` is the solution to build. Two smaller solution files open a subset of the same projects: `src/SemanticEventDispatcher/SemanticEventDispatcher.slnx` and `src/HapticPipeConsumer/HapticPipeConsumer.slnx`.

| Project | Role | Target |
| --- | --- | --- |
| `src/HybridSed.Contracts` | Pipe name, haptic byte codes, WMI mode numbers | net8.0 |
| `src/SemanticEventDispatcher` | Hook, classifier, router, publishers | net8.0 |
| `src/HapticPipeConsumer` | Named-pipe reader | net10.0 |
| `src/SemanticEventDispatcher.Tests` | Classifier, router, and pipe tests | net8.0 |

The tests do not install the global hook, do not call WMI `Put`, and do not exercise the OSK `dwExtraInfo` gate. That gate is in `Worker`. See [docs/known-limits.md](docs/known-limits.md).

## Run

Start the haptic reader first, then the dispatcher. Use two terminals from the repository root.

```text
dotnet run --project src/HapticPipeConsumer/HapticPipeConsumer.csproj
dotnet run --project src/SemanticEventDispatcher/SemanticEventDispatcher.csproj
```

`dotnet run` uses the dispatcher project directory as the content root, so it loads `src/SemanticEventDispatcher/appsettings.json`. Startup then logs `Voice key VK=135`, the WMI class, the pipe name, and `Keyboard hook thread started`.

Each handled key edge logs:

```text
VK=<virtual-key> Down=<true|false> Injected=<true|false> Osk=<true|false>
Classify VK=<virtual-key> Down=<true|false> Source=LowLevelKeyboardHook Injected=<true|false>
```

`Osk=true` means `dwExtraInfo` matched `0x5345445F4F534B`. The consumer prints a line only for those keys. A physical key still produces the hook and classify logs. Its haptic event is skipped at debug level, so the default Information console does not print the skip.

| Action | Dispatcher | WMI | Haptic consumer |
| --- | --- | --- | --- |
| Physical F24 down, then up | Voice event, `Osk=false` | Mode 0, then Mode 1 | No byte |
| Physical F12, or any other physical key | Haptic classification only, `Osk=false` | No event | No byte |
| F24 with `dwExtraInfo = 0x5345445F4F534B` | Voice, then haptic `SpecialKey`, `Osk=true` | Mode 0, then Mode 1 | `SpecialKey` (byte 3) on each edge |
| Any other key with that same `dwExtraInfo` | The matching haptic type, `Osk=true` | No event | `NormalKey`, `ModifierKey`, or `SpecialKey` |

`Injected=true` is the Win32 injected flag (`LLKHF_INJECTED` or `LLKHF_LOWER_IL_INJECTED`). It is recorded on the semantic event and is not the haptic gate. A key can be injected and still have `Osk=false`.

To run the F12 fallback without editing the file:

```text
dotnet run --project src/SemanticEventDispatcher/SemanticEventDispatcher.csproj -- --Dispatcher:VoiceKeyVirtualKey=123
```

The startup log then shows `Voice key VK=123`. F12 becomes the voice key. F24 becomes an ordinary key.

Listen for the WMI event with `src/WmiVoiceListener/VoiceKeyWMIEvent_SED.py`. `src/WmiVoiceListener/VoiceKeyWMIEvent.py` is an earlier listener and is not interchangeable. See [docs/wire-contracts.md](docs/wire-contracts.md).

## Current paths

```text
Any hooked key down or key up
  (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP)
  -> WH_KEYBOARD_LL on the hook thread (Worker.KeyboardHookCallback)
  -> read virtual-key, injected flags, and dwExtraInfo
  -> InputEventClassifier
       voice key: Voice event, then haptic SpecialKey
       modifier:  haptic ModifierKey
       special:   haptic SpecialKey
       otherwise: haptic NormalKey

Voice event (every voice-key edge, marker or not)
  -> SemanticEventRouter
  -> WmiVoicePublisher
  -> root\wmi VoiceKeyModeSwitchEvent
       Mode 0 = recording start (key down)
       Mode 1 = recording stop  (key up)
  -> VoiceKeyWMIEvent_SED.py

Haptic event
  -> kept only when dwExtraInfo == 0x5345445F4F534B
  -> SemanticEventRouter
  -> HapticPipePublisher (one byte, then flush)
  -> named pipe HybridSedHaptic
  -> HapticPipeConsumer
```

The hook does not swallow keys. `KeyboardHookCallback` always returns `CallNextHookEx`, including when classification or publishing throws. The focused application still receives the key.

## Where to change behavior

| Change | Where |
| --- | --- |
| Voice key, modifier keys, special keys, pipe name, connect timeout, WMI class name, WMI source string | `src/SemanticEventDispatcher/appsettings.json` |
| Fallback values when that file is not loaded | `src/SemanticEventDispatcher/Configuration/DispatcherOptions.cs` |
| OSK `dwExtraInfo` marker, and the decision to skip other haptics | `Worker` in `src/SemanticEventDispatcher/Worker.cs` (`OskExtraInfoMarker`, filter in `KeyboardHookCallback`) |
| Stable pipe name, haptic byte codes, WMI mode numbers | `src/HybridSed.Contracts` |
| Which virtual-key becomes voice, modifier, special, or normal | `src/SemanticEventDispatcher/Inputs/InputEventClassifier.cs` |
| Another output | A new `ISemanticEventPublisher`, registered in `src/SemanticEventDispatcher/Program.cs` |
| WMI class on the machine | One MOF file under `mof/`, compiled with `mofcomp`. The publisher does not pick a file. |
| Sample WMI listener | `src/WmiVoiceListener/VoiceKeyWMIEvent_SED.py` |

A publisher registered in `Program.cs` receives only events `Worker` routes. Haptic events that fail the marker check never reach it. Voice events do.

## Documentation

- [Architecture](docs/architecture.md) — projects, call order, classification, editing checklist
- [Configuration](docs/configuration.md) — JSON values, code fallbacks, what is deliberately not a setting
- [OSK voice integration](docs/osk-voice-integration.md) — F24 plus the `dwExtraInfo` marker
- [WMI and named-pipe contracts](docs/wire-contracts.md) — modes, bytes, MOF files, the two Python listeners
- [Known limits](docs/known-limits.md) — hook-thread cost, what the tests skip

## Operational limit

Classification runs on the keyboard-hook thread for every key. WMI `Connect` and `Put` run there for the voice key. A pipe connect or write runs there only for a haptic event that passed the OSK marker check. A stuck WMI `Put` can delay keyboard input for the whole session, and Windows can remove a low-level hook that does not return in time. Details and the recommended follow-up are in [docs/known-limits.md](docs/known-limits.md).
