> Earlier baseline. Current edge filtering, threading and service deployment: [integration guide](cpp-haptic-integration.md).

# Known limits

These are current properties of the prototype. They are recorded so the next change can stay small.

## Publishing on the keyboard-hook thread

`Worker.KeyboardHookCallback` classifies the key and publishes before it returns to `CallNextHookEx`. The low-level hook runs on that thread for every key in the session. The thread is `Hybrid SED Keyboard Hook Thread`.

| Work | When it runs | Cost |
| --- | --- | --- |
| Classification | Every key down and key up | In memory, plus an Information log |
| OSK marker check | Every semantic event from that classification | One integer compare. Non-matching haptic events are not routed. |
| Pipe write | Each routed haptic event, while `HapticPipeConsumer` is connected | One local byte and a flush, on the hook thread. Today that is a key edge whose `dwExtraInfo` is `0x5345445F4F534B`. |
| Pipe connect | Once when `HapticPipePublisher` is constructed, then again on each routed haptic event while the consumer is absent | Up to `HapticPipeConnectTimeoutMs` (default 20) per attempt. The constructor attempt runs on the host startup thread. The retry blocks the hook thread. |
| WMI `Connect` and `Put` | Voice-key down and voice-key up only | Unbounded if the WMI service stalls. The marker is irrelevant. Each publish opens a new scope. |

A healthy voice publish is often tens of milliseconds and is felt on that key. A stuck `Put` delays keyboard input for the session until the call returns. Windows can also remove a low-level hook that does not return within the system timeout (`LowLevelHooksTimeout` on the machine). SED would then stop seeing keys until the process is started again. This code does not set that timeout.

If `Put` throws, the callback logs the error and still forwards the key. The `catch` surrounds the whole event list for that key, so later events from the same edge are not routed. For an OSK-tagged voice key, the next event is haptic `SpecialKey`, and that pipe byte is skipped. For a physical voice key, that haptic event would have been skipped by the marker check anyway.

Console log lines are queued by the logging provider. They are not the stall. The non-OSK skip is `LogDebug` and does not appear at the default Information level.

### Follow-up, not part of this prototype

Keep this shape until SED runs for long periods on machines where WMI is unreliable. The follow-up that closes the limit is one queue and one consumer thread beside the hook:

- Enqueue the semantic events the classifier already built, after the OSK gate has decided which haptic events to keep. Moving the gate later would start publishing physical-key haptics, which this prototype does not do.
- Publish on that single thread, so order stays stable.
- Do not block the hook when the queue is full.
- On shutdown, decide explicitly whether a queued voice-stop is drained or dropped.

A per-output dispatcher is a larger design and is not required to move WMI off the hook.

## One input

Only `WH_KEYBOARD_LL` is implemented. It sees physical keys and injected keys, including a typical OSK injection. `InputSource` has one value, `LowLevelKeyboardHook`. `IsInjected` is carried on the event. Nothing routes on `Source` or `IsInjected`.

The physical-versus-OSK split that does exist is the `dwExtraInfo` check in `Worker`, and it applies to haptic publishing only. Voice still fires for the configured voice key on both physical and injected edges.

## Haptic output is the OSK marker

The classifier still builds a haptic event for every hooked key, on both edges. `Worker` routes that event only when:

```text
KBDLLHOOKSTRUCT.dwExtraInfo == 0x5345445F4F534B
```

The constant is `Worker.OskExtraInfoMarker`. High byte first, the bytes are `53 45 44 5F 4F 53 4B` (`SED_OSK`). The callback compares the numeric `ULONG_PTR`. It does not look for a character string, and it does not look at `LLKHF_INJECTED`.

| Key | Voice WMI | Named-pipe byte |
| --- | --- | --- |
| Voice key, any `dwExtraInfo` | Mode 0 on down, Mode 1 on up | Only if `dwExtraInfo` is the marker (`SpecialKey`, byte 3) |
| Any other key, marker present | None | The classified haptic byte |
| Any other key, marker absent | None | None. The haptic event is built, logged by the classifier, and skipped. |

Injected keys that do not carry this value are handled like a physical key: voice if they are the voice key, no pipe byte. Keys that carry the value publish haptics even when the injected flag is clear.

The marker is wider than 32 bits. A 32-bit dispatcher process cannot hold it in `ULONG_PTR`, so haptic publishing would not see a match. Run the dispatcher as a 64-bit process. The csproj does not set `PlatformTarget`. On 64-bit Windows, .NET 8 runs this project as 64-bit.

`SemanticEvent` has no field for the marker. A publisher cannot re-check it. The decision is finished before `SemanticEventRouter.Route`.

## Framework targets

The dispatcher, the contracts, and the tests target `net8.0`. The haptic consumer targets `net10.0`. A machine that runs both needs both runtimes. The dispatcher also references `System.Management` 10.0.12, which runs on the net8.0 host. Aligning the target frameworks is an environment choice and is left unchanged.

## WMI class ownership

Four MOF files describe `VoiceKeyModeSwitchEvent`. The publisher does not choose among them at runtime. Standardize on one compiled definition in the OSK image. See [wire-contracts.md](wire-contracts.md).

`WmiVoicePublisher` calls `Put` on an instance. It does not register the provider class that `mof/voice_key.mof` also defines. A missing class fails the voice key on the hook thread (see the first section).

## Tests that are not a full OSK pass

`dotnet test` covers classification, router fan-out, a dropped pipe write, and one real pipe byte. It does not install the global hook, does not call WMI `Put` (that emits a live event), and does not pass `dwExtraInfo` through `Worker`.

| Test | What it locks |
| --- | --- |
| `Default_voice_key_remains_F12` | `DispatcherOptions.VoiceKeyVirtualKey` initializer is `0x7B` (123). It does not read `appsettings.json`, where the value is 135. |
| `F12_down_emits_recording_start_and_special_haptic` | Default options: F12 down is voice start, then haptic `SpecialKey`. |
| `F12_up_emits_recording_stop_and_special_haptic` | F12 up is voice stop, and `IsInjected` is copied onto both events. |
| `Modifier_keys_emit_only_modifier_haptic` | `0x10`, `0x11`, `0x12`, `0xA0`, `0xA5` are modifier haptics. |
| `Space_enter_and_backspace_emit_special_haptic` | `0x20`, `0x0D`, `0x08` are special haptics, including on key up. |
| `Other_keys_emit_normal_haptic_on_down_and_up` | An unlisted key (`0x41`) is `NormalKey` on both edges. |
| `Configured_F24_becomes_the_voice_key_and_F12_becomes_normal` | Options with voice key `0x87`: F24 is voice plus `SpecialKey`, F12 is `NormalKey`. |
| `Voice_key_that_is_also_a_modifier_emits_both_haptic_types` | Overlap emits three events: voice, `SpecialKey`, `ModifierKey`. |
| `Router_forwards_one_event_to_every_publisher_in_order` | Fan-out follows registration order. |
| `Router_stops_after_a_publisher_throws` | Later publishers miss that event. |
| `Voice_publisher_ignores_haptic_events_without_calling_wmi` | A haptic event returns without a WMI call. |
| `Voice_publisher_rejects_a_voice_event_without_a_payload` | Voice with a null `VoiceEvent` throws. |
| `Haptic_publisher_drops_events_when_no_consumer_is_listening` | A missing pipe does not throw. |
| `Haptic_publisher_writes_the_shared_event_code` | A live pipe receives byte 3 for `SpecialKey`. |
| `Haptic_wire_codes_stay_stable` | Bytes 1, 2, 3, 4 stay on the four enum names. |
| `Voice_wmi_modes_stay_stable` | Mode 0, Mode 1, `root\wmi`, `VoiceKeyModeSwitchEvent`, `HybridSedHaptic`. |

The manual F24 and marker check is in [osk-voice-integration.md](osk-voice-integration.md).
