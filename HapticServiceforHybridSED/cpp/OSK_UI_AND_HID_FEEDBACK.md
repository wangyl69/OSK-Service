# OSK UI and HID feedback audit (2026-10-23)

## Scope and evidence

The supplied `OSK_Prototype` contains a published WPF application, not its original
project sources. ILSpy decompiled `OskDemoApp.dll` and its BAML into
`logs/osk-ui-audit-20261023/decompiled/`. These are derived audit files.
No OSK binaries or configuration were changed. No actual mouse click was made:
the computer-use skill requires the `node_repl`/sky runtime, which is unavailable
in this session. Source inspection is not a substitute for a desktop test.

## What the UI supports

* `MainWindow.xaml` draws the keyboard using images, not individual WPF Buttons.
  Its C# has no keyboard mouse-down/up coordinate-to-key adapter.
* `MainWindow.WndProc` routes WM_INPUT (255) to `RawInputService` and
  `OskDllService`. The latter forwards only WM_INPUT to native OskWin processing.
* `RawInputService.RegisterForRawInput` registers usage page 13, usage 4
  (digitizer/touchscreen). Its managed parser accepts RAWINPUT type 2 (HID),
  not type 0 (mouse). An ordinary mouse click does not supply a touch HID report.
* `OskPoCApps` takes native key callbacks, maps usage IDs through `osk.bin`, then
  calls `SendKeyDown`/`SendKeyUp`. The keyboard repaint path follows those
  callbacks; repainting is not the keyboard input source.
* `ControlWindow` has normal Buttons and explicit MouseUp/TouchUp handlers for
  overlay toggles. Mouse support in the control panel does not establish mouse
  typing support in the keyboard image. OverlayViewModel.TouchDown/TouchUp are
  empty in this build.

Start `OskDemoApp.exe` with `OSK_Prototype` as the working directory because resources
use relative paths. Startup shows MainWindow and ControlWindow and initializes
the native OSK automatically. For live typing, focus the destination application
and use the compatible touch sensor. Merely having any touchscreen is not proof
that its raw reports match this prototype's native parser. The supplied layout
uses a 2880 by 1800 configuration; changing display dimensions alone does not
establish that native hit-test coordinates are calibrated.

Control panel: Overlay/FVL/MVL/H_MVL show diagnostic layers; Update applies
parameter changes, Save persists settings, Load reloads settings, the speaker
button toggles sound, and Exit shuts down the application. Avoid treating these
diagnostic controls as typing keys.

With DebugOverlay enabled, the replay panel offers Load / Load Folder and
Play / Stop. It expects the application's raw sensor log format, not arbitrary
text. Crucially, ControlViewModel.PlayFile selects Mode.LogReplay, and
OskPoCApps.KeyRepaint skips Windows SendKeyDown/SendKeyUp in that mode. Thus
replay animation and native recognition alone do not validate SED-to-Haptic
delivery. The previously run LrSendInput DLL tests exercise downstream input
injection, not keyboard-image clicking or touch recognition.

## Feature, interrupt, and PLAY

Current PLAY always uses a HID Output report through overlapped WriteFile.
Feature/interrupt select feedback mechanisms, not separate motor transports.

* Feature: HidD_GetFeature reads capabilities (0x10) or status (0x11). This API
  call is synchronous in the current service.
* Interrupt: an Output control command requests feedback; ReadFile receives
  a 0x81 Input report ACK/status. Here the name refers to HID input feedback,
  not CPU thread priority. The underlying physical device remains HID over I2C.
* PLAY does not wait for a per-PLAY ACK. However, one HID worker also performs
  management work, so an active control ACK wait or synchronous Feature read can
  delay a newly queued PLAY. Current interrupt control waits use a 250 ms
  timeout; this is an exceptional wait budget, not normal per-key latency.

Current safeguards already prioritize queued PLAY before most maintenance,
serve ordinary status queries from cache, and avoid acknowledged per-key
heartbeats. A verification deadline still forces a health check at 20 seconds.

## Subsequent implementation: asynchronous runtime interrupt status

After the initial audit, runtime interrupt status queries were changed to owned
overlapped reads with nonblocking completion checks. The HID worker continues
dispatching PLAY while an ACK is pending. One status request is in flight at a
time; other management controls wait for it to complete. Explicit diagnose
requests coalesce and start at most once per second. Normal status calls still
read the cache. Periodic verification retains its 10-second idle / 20-second
forced schedule and a 250 ms response deadline. Timeout enters normal recovery.
Cancellation joins the outstanding read before closing its handle or buffer.

Startup, configuration changes and Feature reads still use the confirmed
synchronous paths. This change removes runtime status ACK waits from PLAY
dispatch; it does not make all management traffic or physical Output writes
nonblocking. The design guidance below describes the broader direction.

## Broader architecture guidance

Keep an asynchronous Input-report read pending and process its completion without
blocking the PLAY scheduler. Track a single outstanding management command;
current ACKs have no transaction sequence ID, so do not allow arbitrary parallel
management requests. Serialize Output writes, dispatch queued PLAY before new
optional maintenance, and release scheduler locks while waiting for completion.
Correlate and time out management ACKs using a state machine. Startup/session
and configuration must still be confirmed before declaring the device ready;
known FAULT or uncertain configuration must not be bypassed to send PLAY.

Keep Feature probing off the running PLAY worker; use it during initialization
or explicitly quiescent maintenance unless concurrent driver behavior has been
validated. A second thread alone does not remove driver or I2C serialization.
Retain bounded liveness verification and session-expiry protection under
continuous typing. Do not simply delete the forced health check.

Validate with delayed/missing/stale ACK and pending-PLAY overlap tests, followed
by real-MCU Input-read plus Output-write concurrency and bus timing captures.
Software mock timings cannot establish physical I2C latency or prove driver
concurrency. Even asynchronous feedback cannot preempt a transfer already on
the shared bus, or guarantee Windows scheduling without long tails.
