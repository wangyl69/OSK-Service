# OSK / MCU update coordination

This workspace contains independent Git repositories. Discover repository roots, branches and remotes before changing or publishing files.

The user explicitly requires every update to consider all MCU, keyboard and deployment modules, and every completion report to explain exactly what each target machine must update. Apply this requirement to fixes, new features, waveform/default changes and delivery work.

## Check the complete module map

| Module | Sources and delivery surfaces to inspect |
|---|---|
| Formal MCU | Athena_Haptic Core, config, protocol, waveforms, generators and relevant tests |
| MCU project copies | Self-contained stm32cubeide project and IAR project references |
| MCU firmware distribution | Haptic_Service/firmware_mcu and Deployment/firmware_mcu |
| Custom diagnostic keyboard | Haptic_Service/hid GUI, shared backend, input worker and settings |
| Traditional Windows OSK | Haptic_Service/windows_osk legacy OSK launcher |
| Modern Touch Keyboard | Haptic_Service/windows_osk TabTip/TextInputHost launcher |
| In-house OSK integration | OSK_Prototype release files, HybridSedPrototype_ExtraInfo SED and HapticServiceforHybridSED/cpp |
| Startup and configuration | BAT, PowerShell and VBS launchers, shortcut creators, command-line defaults, configuration files and reconnect/default restoration |
| Deployment runtime | Deployment Apps/OSK, Apps/SED, Apps/Haptic and portable Haptic_Service/Haptic_Service |
| Deployment modes | Git checkout, portable/offline packages, user-session launchers and applicable SCM service mode |
| MCU delivery packages | Athena_Haptic/delivery/Code_Sample source/CubeIDE archives, bundled firmware and current-package guide |
| Separate demos | Inspect Athena-Haptic-Japan-Demo when a shared defect or interface change affects it; preserve its distinct catalog and streaming behavior |

The three Python keyboard modes are the custom diagnostic keyboard, traditional Windows OSK and modern TabTip/TextInputHost. In-house OSK is an additional integration path and must also be considered.

## Implement and verify the applicable updates

- For each module, decide whether the change affects it. Update affected consumers, source copies and delivery files together. Record unaffected modules as checked with no update required.
- Do not mechanically rebuild or change every module for a documentation-only or otherwise unrelated change. Choose validation appropriate to the actual impact.
- Check both sides of protocol, waveform ID/count, default, report layout, session lifecycle and configuration changes. Inspect startup and reconnect paths for old values that could overwrite runtime settings.
- Build affected firmware and native programs. Copy verified outputs into their actual launch/distribution locations.
- Refresh applicable delivery archives, current-package pointers, version records and manifests. Bind shipped binaries to the source commit and build configuration.
- Preserve firmware bytes across Git checkouts and verify archive/image hashes. Do not describe a historical Release or portable snapshot as current unless it was actually refreshed.
- Distinguish compilation, mock/logic checks and physical hardware checks in the report. Never infer physical vibration from successful enqueue, transport or DMA alone.
- If rebuilding an application is impossible because only release files are available, state the exact limitation and its impact on delivery.
- Preserve unrelated local changes and logs. Commit only task-related files. Follow the user's authorization for commits/pushes and report each involved repository separately.

## Required completion handoff

Every completed update must include a concise target-machine update table. Cover the complete module map, grouping genuinely unaffected modules when useful.

For affected modules report:

1. Repository and branch, source/delivery commit, and exact download or file location.
2. Whether the target machine must pull code, copy/reinstall a runtime package, flash MCU firmware, regenerate shortcuts, restart processes or update its SCM service installation.
3. Required update order and any incompatible old/new combinations.
4. Validation actually performed and physical checks still pending.
5. Any distribution surface that remains stale or could not be synchronized, with the concrete impact.

For unaffected modules explicitly state that no target-machine action is required. Do not use a blanket "everything is updated" statement when a runtime copy, archive, launcher or deployment mode remains behind.

When changing startup defaults, account for existing desktop shortcuts and already-running backends. When changing firmware, tell the user that downloading/pulling files does not itself flash the MCU. Make the handoff specific enough that the user can update another machine without guessing which repository or package to use.
