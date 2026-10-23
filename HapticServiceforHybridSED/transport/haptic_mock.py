#!/usr/bin/env python3
"""In-memory stand-in for the STM32 I2C HID haptic device (no hardware needed).

Used by:
  * hid_selftest.py           - phase 3, validates the pipe/service/encoder chain
  * hid_launcher.py --mock    - preview the GUI on a machine without the MCU

Only the HID transport (hidapi write/read) is replaced. The real protocol v2
encoder in haptic_hid.py still runs, and every Control report (plus every Event
report that asks for an ACK) is answered with a Result=OK Status report, the
same way Core/Src/app_i2chid.c does.

Nothing here ever touches real hardware, so a passing mock run only proves the
host-side chain: GUI -> Named Pipe -> CommandHandler -> 32-byte encoder.
"""

import haptic_hid as protocol

from haptic_hid import (HapticHID, REPORT_BYTES, PROTOCOL_VERSION,
                        REPORT_ID_EVENT, REPORT_ID_CONTROL, REPORT_ID_STATUS,
                        FLAG_ACK_REQUIRED, OPCODE_PLAY, OPCODE_STATUS_ACK,
                        OPCODE_STATUS_SNAPSHOT, OPCODE_CAPABILITIES_QUERY,
                        OPCODE_SET_DEFAULT_INTENSITY,
                        OPCODE_SET_DEFAULT_WAVEFORM,
                        RESULT_OK, STATE_SESSION_OPEN, STATE_SESSION_WARM,
                        STATE_POWERED_ON,
                        HOST_WAVEFORM_COUNT,
                        DEVICE_READ_TIMEOUT_MS,
                        EVENT_OFFSET_ZONE, EVENT_OFFSET_WAVEFORM,
                        EVENT_OFFSET_INTENSITY,
                        STATUS_OFFSET_RESULT, STATUS_OFFSET_ORIGINAL_ID,
                        STATUS_OFFSET_ORIGINAL_OPCODE,
                        STATUS_OFFSET_STATE_FLAGS,
                        STATUS_OFFSET_LAST_LATENCY_MS,
                        STATUS_OFFSET_PLAY_ACCEPTED,
                        STATUS_OFFSET_MOTOR_MASK, STATUS_OFFSET_WAVEFORM,
                        STATUS_OFFSET_INTENSITY,
                        CAP_STATUS_OFFSET_CAP_FLAGS,
                        CAP_STATUS_OFFSET_MOTOR_COUNT,
                        CAP_STATUS_OFFSET_ACTIVE_MOTORS,
                        CAP_STATUS_OFFSET_MOTOR_MASK,
                        CAP_STATUS_OFFSET_WAVEFORM_COUNT,
                        CAP_STATUS_OFFSET_RAW_QUEUE_DEPTH,
                        CAP_STATUS_OFFSET_INPUT_QUEUE_DEPTH,
                        CAP_STATUS_OFFSET_REPORT_BYTES,
                        CAP_STATUS_OFFSET_EVENT_ID,
                        CAP_STATUS_OFFSET_CONTROL_ID,
                        CAP_STATUS_OFFSET_STATUS_ID,
                        CAP_STATUS_OFFSET_CAPS_ID,
                        CAP_STATUS_OFFSET_CONFIG_ID,
                        CAP_STATUS_OFFSET_HEARTBEAT_SEC,
                        CAP_STATUS_OFFSET_FAILSAFE_SEC,
                        CAP_STATUS_OFFSET_SLEEP_ACCESS,
                        CAP_STATUS_OFFSET_DEVICE_VERSION,
                        parse_status_report, compute_crc8)

_OPCODE_NAMES = {
    0x20: "SESSION_OPEN", 0x21: "SESSION_CLOSE", 0x22: "HEARTBEAT",
    0x30: "SET_DEFAULT_INTENSITY", 0x31: "SET_DEFAULT_WAVEFORM",
    0x32: "STATUS_QUERY", 0x33: "CAPABILITIES_QUERY",
}


def describe(report: bytes) -> str:
    """One-line human summary of an Output Report, for logs."""
    report_id, opcode = report[0], report[2]
    sequence = report[4] | (report[5] << 8)
    if report_id == REPORT_ID_EVENT and opcode == OPCODE_PLAY:
        return (f"Event PLAY   seq={sequence} "
                f"zone={report[EVENT_OFFSET_ZONE]} "
                f"waveform={report[EVENT_OFFSET_WAVEFORM]} "
                f"intensity={report[EVENT_OFFSET_INTENSITY]} "
                f"flags=0x{report[3]:02X}")
    if report_id == REPORT_ID_CONTROL:
        name = _OPCODE_NAMES.get(opcode, f"0x{opcode:02X}")
        return f"Control {name} seq={sequence} value0={report[6]}"
    return f"Report id=0x{report_id:02X} opcode=0x{opcode:02X} seq={sequence}"


def hexdump(report: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in report)


class MockHapticHID(HapticHID):
    """HapticHID with the HID transport replaced by an in-memory recorder."""

    def __init__(self, on_report=None, status_mode="auto"):
        """on_report: optional callback invoked with each 32-byte report."""
        super().__init__(status_mode=status_mode)
        self.sent = []
        self._queued = []
        self._on_report = on_report
        self._waveform = 1
        self._intensity = 2

    # ── Transport stubs ─────────────────────────────────────────
    def open(self, path=None) -> bool:
        self._device = self          # is_connected -> True
        return True

    def close(self):
        self._device = None

    # ── Firmware-like ACK generation ────────────────────────────
    def _make_status(self, original_id: int, opcode: int,
                     result: int = RESULT_OK):
        report = bytearray(REPORT_BYTES)
        report[0] = REPORT_ID_STATUS
        report[1] = PROTOCOL_VERSION
        report[2] = OPCODE_STATUS_ACK
        report[STATUS_OFFSET_RESULT] = result
        report[STATUS_OFFSET_ORIGINAL_ID] = original_id
        report[STATUS_OFFSET_ORIGINAL_OPCODE] = opcode
        report[STATUS_OFFSET_STATE_FLAGS] = (
            STATE_SESSION_OPEN | STATE_SESSION_WARM | STATE_POWERED_ON
        )
        report[STATUS_OFFSET_LAST_LATENCY_MS] = 3
        report[STATUS_OFFSET_PLAY_ACCEPTED] = min(255, len(
            [r for r in self.sent if r[0] == REPORT_ID_EVENT]))
        report[STATUS_OFFSET_MOTOR_MASK] = 0x03
        report[STATUS_OFFSET_WAVEFORM] = self._waveform
        report[STATUS_OFFSET_INTENSITY] = self._intensity
        if opcode == OPCODE_CAPABILITIES_QUERY:
            report[2] = OPCODE_STATUS_SNAPSHOT
            report[CAP_STATUS_OFFSET_CAP_FLAGS] = 0xFF
            report[CAP_STATUS_OFFSET_CAP_FLAGS + 1] = 0x08
            report[CAP_STATUS_OFFSET_MOTOR_COUNT] = 2
            report[CAP_STATUS_OFFSET_ACTIVE_MOTORS] = 2
            report[CAP_STATUS_OFFSET_MOTOR_MASK] = 0x03
            report[CAP_STATUS_OFFSET_WAVEFORM_COUNT] = HOST_WAVEFORM_COUNT
            report[CAP_STATUS_OFFSET_RAW_QUEUE_DEPTH] = 4
            report[CAP_STATUS_OFFSET_INPUT_QUEUE_DEPTH] = 4
            report[CAP_STATUS_OFFSET_REPORT_BYTES] = REPORT_BYTES
            report[CAP_STATUS_OFFSET_EVENT_ID] = REPORT_ID_EVENT
            report[CAP_STATUS_OFFSET_CONTROL_ID] = REPORT_ID_CONTROL
            report[CAP_STATUS_OFFSET_STATUS_ID] = REPORT_ID_STATUS
            report[CAP_STATUS_OFFSET_CAPS_ID] = 0x10
            report[CAP_STATUS_OFFSET_CONFIG_ID] = 0x11
            report[CAP_STATUS_OFFSET_HEARTBEAT_SEC] = 10
            report[CAP_STATUS_OFFSET_FAILSAFE_SEC] = 30
            report[CAP_STATUS_OFFSET_SLEEP_ACCESS] = 1
            report[CAP_STATUS_OFFSET_DEVICE_VERSION] = PROTOCOL_VERSION
        report[31] = compute_crc8(bytes(report[:31]))
        return parse_status_report(bytes(report))

    def _write_report(self, report: bytes) -> int:
        if len(report) != REPORT_BYTES:
            raise AssertionError(f"report is {len(report)} bytes, not "
                                 f"{REPORT_BYTES}")
        self.sent.append(bytes(report))
        if self._on_report is not None:
            self._on_report(bytes(report))

        report_id, opcode, flags = report[0], report[2], report[3]
        if report_id == REPORT_ID_CONTROL:
            if opcode == OPCODE_SET_DEFAULT_WAVEFORM:
                self._waveform = report[6]
            elif opcode == OPCODE_SET_DEFAULT_INTENSITY:
                self._intensity = report[6]
            if flags & FLAG_ACK_REQUIRED:
                self._queued.append(self._make_status(report_id, opcode))
        elif report_id == REPORT_ID_EVENT and (flags & FLAG_ACK_REQUIRED):
            self._queued.append(self._make_status(report_id, opcode))
        return len(report)

    def read_status(self, timeout_ms: int = DEVICE_READ_TIMEOUT_MS):
        return self._queued.pop(0) if self._queued else None

    def _get_feature_report(self, report_id: int):
        report = bytearray(REPORT_BYTES)
        report[0] = report_id
        report[1] = PROTOCOL_VERSION
        report[3] = protocol.FLAG_CRC8
        if report_id == protocol.REPORT_ID_CAPABILITIES:
            report[2] = 1
            report[protocol.CAP_OFFSET_CAP_FLAGS] = 0xFF
            report[protocol.CAP_OFFSET_CAP_FLAGS + 1] = 0x08
            report[protocol.CAP_OFFSET_MOTOR_COUNT] = 2
            report[protocol.CAP_OFFSET_ACTIVE_MOTORS] = 2
            report[protocol.CAP_OFFSET_MOTOR_MASK] = 0x03
            report[protocol.CAP_OFFSET_WAVEFORM_COUNT] = HOST_WAVEFORM_COUNT
            report[protocol.CAP_OFFSET_RAW_QUEUE_DEPTH] = 4
            report[protocol.CAP_OFFSET_INPUT_QUEUE_DEPTH] = 4
            report[protocol.CAP_OFFSET_REPORT_BYTES] = REPORT_BYTES
            report[protocol.CAP_OFFSET_EVENT_ID] = REPORT_ID_EVENT
            report[protocol.CAP_OFFSET_CONTROL_ID] = REPORT_ID_CONTROL
            report[protocol.CAP_OFFSET_STATUS_ID] = REPORT_ID_STATUS
            report[protocol.CAP_OFFSET_CAPS_ID] = protocol.REPORT_ID_CAPABILITIES
            report[protocol.CAP_OFFSET_CONFIG_ID] = protocol.REPORT_ID_CONFIG
            report[protocol.CAP_OFFSET_HEARTBEAT_SEC] = 10
            report[protocol.CAP_OFFSET_FAILSAFE_SEC] = 30
            report[protocol.CAP_OFFSET_SLEEP_ACCESS] = 1
            report[protocol.CAP_OFFSET_DEVICE_VERSION] = PROTOCOL_VERSION
        elif report_id == protocol.REPORT_ID_CONFIG:
            report[2] = 2
            report[protocol.CONFIG_OFFSET_INTENSITY] = self._intensity
            report[protocol.CONFIG_OFFSET_WAVEFORM] = self._waveform
            report[protocol.CONFIG_OFFSET_HEARTBEAT_SEC] = 10
            report[protocol.CONFIG_OFFSET_FAILSAFE_SEC] = 30
            report[protocol.CONFIG_OFFSET_MOTOR_MASK] = 0x03
            report[protocol.CONFIG_OFFSET_SLEEP_ACCESS] = 1
            report[protocol.CONFIG_OFFSET_STATE_FLAGS] = (
                STATE_SESSION_OPEN | STATE_SESSION_WARM | STATE_POWERED_ON
            )
            report[protocol.CONFIG_OFFSET_PLAY_ACCEPTED] = len(
                self.event_reports
            ) & 0xFF
            report[protocol.CONFIG_OFFSET_TRANSPORT_REV] = 1
        else:
            return None
        report[31] = compute_crc8(bytes(report[:31]))
        return bytes(report)

    # ── Convenience for tests ───────────────────────────────────
    @property
    def event_reports(self):
        return [r for r in self.sent if r[0] == REPORT_ID_EVENT]

    @property
    def control_reports(self):
        return [r for r in self.sent if r[0] == REPORT_ID_CONTROL]
