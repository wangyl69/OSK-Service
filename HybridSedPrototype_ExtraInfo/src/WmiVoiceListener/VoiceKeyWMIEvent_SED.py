"""
SED listener for VoiceKeyModeSwitchEvent published by WmiVoicePublisher.

Contract (keep aligned with HybridSed.Contracts.VoiceWmiProtocol):
    namespace root\\wmi
    class     VoiceKeyModeSwitchEvent
    Mode 0    recording start
    Mode 1    recording stop

Use this file with the C# dispatcher.
VoiceKeyWMIEvent.py is an earlier listener with a different wmi.watch_for call.

Listen for Windows Voice Key WMI events.

Base mode switch event:
    Mode 0 -> Voice recording
    Mode 1 -> Voice recorded

Dependencies:
    pip install wmi pywin32
"""

import threading
from enum import IntEnum
from typing import Callable, Optional

import pythoncom
import wmi


# ---------------------------------------------------------------------------
# Base mode enum
# ---------------------------------------------------------------------------
class BaseMode(IntEnum):
    """Base modes of the Voice Key."""
    VOICE_RECORDING = 0   # Voice recording
    VOICE_RECORDED = 1    # Voice recorded

    @classmethod
    def from_value(cls, value: int) -> "BaseMode":
        try:
            return cls(value)
        except ValueError:
            raise ValueError(f"Unknown Voice Key base mode: {value}")


# ---------------------------------------------------------------------------
# WMI event class
# ---------------------------------------------------------------------------
class VoiceKeyWMIEvent:
    """
    Wraps a single Voice Key mode switch WMI event.

    Attributes:
        mode (BaseMode): Current base mode (0 = recording, 1 = recorded).
        raw  (object)  : Original WMI event object for extra attributes.
    """

    # WMI event name (adjust to match your driver/firmware).
    EVENT_NAME = "VoiceKeyModeSwitchEvent"

    # WMI namespace (usually root\\wmi or root\\cimv2).
    NAMESPACE = r"root\wmi"

    def __init__(self, mode: BaseMode, raw=None):
        self.mode = mode
        self.raw = raw

    @property
    def is_recording(self) -> bool:
        """True if the current mode is Voice recording (mode 0)."""
        return self.mode == BaseMode.VOICE_RECORDING

    @property
    def is_recorded(self) -> bool:
        """True if the current mode is Voice recorded (mode 1)."""
        return self.mode == BaseMode.VOICE_RECORDED

    def __repr__(self) -> str:
        return f"<VoiceKeyWMIEvent mode={int(self.mode)} ({self.mode.name})>"


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------
class VoiceKeyListener:
    """
    Listen for Voice Key WMI events and invoke a callback on mode changes.

    Usage:
        listener = VoiceKeyListener(on_mode_change=my_handler)
        listener.start()      # non-blocking
        ...
        listener.stop()
    """

    def __init__(
        self,
        on_mode_change: Optional[Callable[[VoiceKeyWMIEvent], None]] = None,
        namespace: str = VoiceKeyWMIEvent.NAMESPACE,
        event_name: str = VoiceKeyWMIEvent.EVENT_NAME,
    ):
        """
        Args:
            on_mode_change: Callback invoked with a VoiceKeyWMIEvent on mode change.
            namespace:      WMI namespace.
            event_name:     WMI event class name.
        """
        self.on_mode_change = on_mode_change
        self.namespace = namespace
        self.event_name = event_name

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._watcher = None

    # ------------------------------------------------------------------ #
    # Internal: parse raw WMI event into VoiceKeyWMIEvent
    # ------------------------------------------------------------------ #
    def _parse_event(self, raw_event) -> VoiceKeyWMIEvent:
        """
        Extract the base mode value from a raw WMI event.

        Common attribute names are 'Mode', 'BaseMode', or 'ModeValue'.
        Adjust these to match your actual WMI definition.
        """
        for attr in ("Mode", "BaseMode", "ModeValue", "mode"):
            if hasattr(raw_event, attr):
                raw_value = getattr(raw_event, attr)
                return VoiceKeyWMIEvent(BaseMode.from_value(int(raw_value)), raw_event)

        raise AttributeError(
            f"Mode attribute (Mode/BaseMode/ModeValue) not found "
            f"in WMI event {self.event_name}"
        )

    # ------------------------------------------------------------------ #
    # Internal: run the WMI event loop on a worker thread
    # ------------------------------------------------------------------ #
    def _run(self):
        # WMI events require COM initialization (must be done per thread).
        pythoncom.CoInitialize()
        try:
            print(f"namespace={self.namespace}")
            print(f"event_name={self.event_name}")

            self._watcher = wmi.WMI(namespace=self.namespace).watch_for(
                notification_type="Creation",
                wmi_class=self.event_name,
                delay_secs=0.1,
            )

            while not self._stop_event.is_set():
                raw_event = self._watcher()
                if self._stop_event.is_set():
                    break
                try:
                    event = self._parse_event(raw_event)
                except Exception as exc:  # Parsing failure should not kill the loop.
                    print(f"[VoiceKeyListener] Failed to parse event: {exc}")
                    continue

                # Dispatch callback.
                if self.on_mode_change is not None:
                    try:
                        self.on_mode_change(event)
                    except Exception as exc:
                        print(f"[VoiceKeyListener] Callback error: {exc}")
        finally:
            pythoncom.CoUninitialize()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Start listening in a background thread (non-blocking)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Stop listening."""
        self._stop_event.set()
        if self._watcher is not None:
            try:
                self._watcher.stop()  # Supported by some versions of `wmi`.
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------
def _demo_handler(event: VoiceKeyWMIEvent) -> None:
    if event.is_recording:
        print("Voice recording (mode 0) ...")
    elif event.is_recorded:
        print("Voice recorded  (mode 1).")
    else:
        print(f"Unknown mode: {event}")


if __name__ == "__main__":
    listener = VoiceKeyListener(on_mode_change=_demo_handler)
    listener.start()
    print("Listening for Voice Key WMI events. Press Ctrl+C to exit...")

    try:
        while listener.is_running():
            threading.Event().wait(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        listener.stop()