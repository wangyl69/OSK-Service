#!/usr/bin/env python3
"""
Haptic HID Device Manager & Protocol Encoder/Decoder
=====================================================
Communicates with STM32 I2C HID haptic device (VID:0x06CB, PID:0xCF00)
via Windows HID API (hidapi).

Protocol: HID-over-I2C Vendor-defined haptic protocol v2
  - Output Report ID 0x01 (Event: PLAY + waveform/intensity)
  - Output Report ID 0x02 (Control: Session/Heartbeat/Defaults)
  - Input  Report ID 0x81 (Status: ACK/ERROR)
  - Feature Report ID 0x10 (Capabilities)

All reports are fixed 32 bytes. CRC8 polynomial = 0x07 over bytes 0-30,
stored at byte 31.
"""

import struct
import logging
import time
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass, field

import hid

logger = logging.getLogger(__name__)

# ── hidapi backend compatibility ───────────────────────────────────
# Two different PyPI packages both provide a module called ``hid``:
#   * ``hidapi``  (Cython wheel, pinned by requirements.txt)
#         hid.device()  -> open_path(bytes) / read(size, timeout_ms=...)
#   * ``hid``     (ctypes wrapper)
#         hid.Device(path=...) -> read(size, timeout=...)
# Bring-up PCs have one or the other, so both are supported here instead of
# crashing with AttributeError: module 'hid' has no attribute 'Device'.
_BACKEND_LEGACY = hasattr(hid, "device")    # hidapi (Cython)
_BACKEND_MODERN = hasattr(hid, "Device")    # hid (ctypes)


def backend_name() -> str:
    """Return which hidapi flavour is in use (for logs/diagnostics)."""
    if _BACKEND_LEGACY:
        return f"hidapi.device (v{getattr(hid, '__version__', '?')})"
    if _BACKEND_MODERN:
        return f"hid.Device (v{getattr(hid, '__version__', '?')})"
    return "unknown"


def _open_backend_device(path):
    """Open a HID path with whichever hidapi flavour is installed."""
    if _BACKEND_LEGACY:
        device = hid.device()
        device.open_path(path if isinstance(path, bytes) else str(path).encode())
        return device
    if _BACKEND_MODERN:
        return hid.Device(path=path)
    raise HapticDeviceError(
        "Installed 'hid' module exposes neither hid.device nor hid.Device. "
        "Install the pinned backend: pip install -r requirements.txt"
    )


def _read_backend_device(device, size: int, timeout_ms: int):
    """Read an Input Report with the installed hidapi flavour."""
    if _BACKEND_LEGACY:
        return device.read(size, timeout_ms=timeout_ms)
    return device.read(size, timeout=timeout_ms)


def _get_feature_backend_device(device, report_id: int, size: int):
    """Issue a synchronous HID GetFeature request on either hid wrapper."""
    return device.get_feature_report(report_id, size)


# ── Device identity ────────────────────────────────────────────────
VENDOR_ID  = 0x06CB  # Synaptics (TouchPad mimic)
PRODUCT_ID = 0xCF00  # SYNA2BA6 compatible

# ── Report constants ───────────────────────────────────────────────
REPORT_BYTES            = 32
PROTOCOL_VERSION        = 0x02
DEVICE_READ_TIMEOUT_MS  = 200
# Minimum delay between unconfirmed SESSION_OPEN retries, so a device whose
# Status/Input path is broken does not get a Control report before every play.
SESSION_OPEN_RETRY_SEC  = 2.0
# Consecutive heartbeats without any Status report before we stop trusting the
# cached "session open" flag and re-establish the session.
SILENT_HEARTBEATS_BEFORE_REOPEN = 3
FEATURE_OPEN_TIMEOUT_MS = 1000
FEATURE_POLL_INTERVAL_MS = 20
WRITE_ONLY_PREWARM_MS = 80

# Report IDs
REPORT_ID_EVENT         = 0x01  # Host → MCU: play vibration
REPORT_ID_CONTROL       = 0x02  # Host → MCU: session/heartbeat/config
REPORT_ID_STATUS        = 0x81  # MCU  → Host: ACK/ERROR response
REPORT_ID_CAPABILITIES  = 0x10  # Feature: capabilities query
REPORT_ID_CONFIG        = 0x11  # Feature: config/runtime snapshot

# Common header offsets
OFFSET_REPORT_ID        = 0
OFFSET_VERSION          = 1
OFFSET_OPCODE           = 2
OFFSET_FLAGS            = 3
OFFSET_SEQUENCE         = 4
OFFSET_PAYLOAD          = 6
OFFSET_CRC8             = 31

# ── Opcodes ────────────────────────────────────────────────────────
# Event opcodes
OPCODE_PLAY             = 0x01

# Control opcodes
OPCODE_SESSION_OPEN             = 0x20
OPCODE_SESSION_CLOSE            = 0x21
OPCODE_HEARTBEAT                = 0x22
OPCODE_SET_DEFAULT_INTENSITY    = 0x30
OPCODE_SET_DEFAULT_WAVEFORM     = 0x31
OPCODE_STATUS_QUERY             = 0x32
OPCODE_CAPABILITIES_QUERY       = 0x33

# Status opcodes
OPCODE_STATUS_ACK        = 0x01
OPCODE_STATUS_ERROR      = 0x02
OPCODE_STATUS_SNAPSHOT   = 0x03

# Production keyboard profile.  The current 0916C LRA has four qualified
# 48 kHz click waveforms; the larger firmware waveform bank remains available
# only to MCU-side diagnostic tools.
HOST_WAVEFORM_COUNT      = 4

# ── Flags ──────────────────────────────────────────────────────────
FLAG_CRC8               = 0x01
FLAG_ACK_REQUIRED       = 0x02
FLAG_LATENCY_TRACE      = 0x20  # opt-in MCU RAM trace; no per-key ACK/logging

# Capability bits advertised by Feature Report 0x10 / Control 0x33. Keep
# synchronized with Athena_Haptic/Core/Inc/app_i2chid_protocol.h.
CAP_STATUS_INPUT          = 0x0001
CAP_CONTROL_OUTPUT        = 0x0002
CAP_FEATURE_REPORTS       = 0x0004
CAP_POSITION_FIELDS       = 0x0008
CAP_KEY_ID                = 0x0010
CAP_ZONE_ID               = 0x0020
CAP_SEQUENCE              = 0x0040
CAP_CRC8                  = 0x0080
CAP_POSITION_COMPENSATION = 0x0100
CAP_INDEPENDENT_TDM       = 0x0200
CAP_PER_MOTOR_GAIN_DELAY  = 0x0400
CAP_LATENCY_TRACE         = 0x0800

# ── Result codes ───────────────────────────────────────────────────
RESULT_OK               = 0x01
RESULT_BUSY             = 0x02
RESULT_INVALID_REPORT   = 0x03
RESULT_CRC_ERROR        = 0x04
RESULT_QUEUE_FULL       = 0x05
RESULT_NOT_OPEN         = 0x06
RESULT_UNSUPPORTED      = 0x07
RESULT_HW_ERROR         = 0x08
RESULT_DUPLICATE        = 0x09
RESULT_POWERED_OFF      = 0x0A
RESULT_INVALID_VALUE    = 0x0B

RESULT_NAMES = {
    0x01: "OK", 0x02: "BUSY", 0x03: "INVALID_REPORT",
    0x04: "CRC_ERROR", 0x05: "QUEUE_FULL", 0x06: "NOT_OPEN",
    0x07: "UNSUPPORTED", 0x08: "HW_ERROR", 0x09: "DUPLICATE",
    0x0A: "POWERED_OFF", 0x0B: "INVALID_VALUE",
}

# ── Intensity mapping ──────────────────────────────────────────────
INTENSITY_STRONG = 1
INTENSITY_MEDIUM = 2
INTENSITY_WEAK   = 3

# ── Event field offsets ────────────────────────────────────────────
EVENT_OFFSET_LAYOUT      = 6
EVENT_OFFSET_KEY_ID      = 7
EVENT_OFFSET_ZONE        = 9
EVENT_OFFSET_X           = 10
EVENT_OFFSET_Y           = 12
EVENT_OFFSET_WAVEFORM    = 14
EVENT_OFFSET_INTENSITY   = 15
EVENT_OFFSET_TOUCH_PHASE = 16
EVENT_OFFSET_PRESSURE    = 17
EVENT_OFFSET_AGE_US      = 18

# ── Status field offsets ───────────────────────────────────────────
STATUS_OFFSET_RESULT          = 6
STATUS_OFFSET_ORIGINAL_ID     = 7
STATUS_OFFSET_ORIGINAL_OPCODE = 8
STATUS_OFFSET_STATE_FLAGS     = 9
STATUS_OFFSET_HAPTIC_QUEUE    = 10
STATUS_OFFSET_HID_RAW_QUEUE   = 11
STATUS_OFFSET_MOTOR_MASK      = 12
STATUS_OFFSET_WAVEFORM        = 13
STATUS_OFFSET_INTENSITY       = 14
STATUS_OFFSET_LAYOUT          = 15
STATUS_OFFSET_ZONE            = 16
STATUS_OFFSET_KEY_ID          = 17
STATUS_OFFSET_X               = 19
STATUS_OFFSET_Y               = 21
STATUS_OFFSET_LAST_LATENCY_MS = 23
STATUS_OFFSET_DETAIL          = 25
STATUS_OFFSET_INPUT_QUEUE     = 26
STATUS_OFFSET_RESPONSE_QUEUE  = 27
STATUS_OFFSET_PLAY_ACCEPTED   = 28
STATUS_OFFSET_I2C_ERRORS      = 30

# ── State flags ────────────────────────────────────────────────────
STATE_SESSION_OPEN  = 0x01
STATE_SESSION_WARM  = 0x02
STATE_RUNTIME_BUSY  = 0x04
STATE_POWERED_ON    = 0x08
STATE_HAPTIC_QUEUED = 0x10

# Capabilities snapshot payload in the Status report returned for Control 0x33.
CAP_STATUS_OFFSET_CAP_FLAGS         = 10
CAP_STATUS_OFFSET_MOTOR_COUNT       = 12
CAP_STATUS_OFFSET_ACTIVE_MOTORS     = 13
CAP_STATUS_OFFSET_MOTOR_MASK        = 14
CAP_STATUS_OFFSET_WAVEFORM_COUNT    = 15
CAP_STATUS_OFFSET_RAW_QUEUE_DEPTH   = 16
CAP_STATUS_OFFSET_INPUT_QUEUE_DEPTH = 17
CAP_STATUS_OFFSET_REPORT_BYTES      = 18
CAP_STATUS_OFFSET_EVENT_ID          = 19
CAP_STATUS_OFFSET_CONTROL_ID        = 20
CAP_STATUS_OFFSET_STATUS_ID         = 21
CAP_STATUS_OFFSET_CAPS_ID           = 22
CAP_STATUS_OFFSET_CONFIG_ID         = 23
CAP_STATUS_OFFSET_HEARTBEAT_SEC     = 24
CAP_STATUS_OFFSET_FAILSAFE_SEC      = 25
CAP_STATUS_OFFSET_SLEEP_ACCESS      = 26
CAP_STATUS_OFFSET_DEVICE_VERSION    = 27

# Capabilities Feature offsets (Report ID 0x10).
CAP_OFFSET_CAP_FLAGS         = 6
CAP_OFFSET_MOTOR_COUNT       = 8
CAP_OFFSET_ACTIVE_MOTORS     = 9
CAP_OFFSET_MOTOR_MASK        = 10
CAP_OFFSET_WAVEFORM_COUNT    = 11
CAP_OFFSET_RAW_QUEUE_DEPTH   = 12
CAP_OFFSET_INPUT_QUEUE_DEPTH = 13
CAP_OFFSET_REPORT_BYTES      = 14
CAP_OFFSET_EVENT_ID          = 15
CAP_OFFSET_CONTROL_ID        = 16
CAP_OFFSET_STATUS_ID         = 17
CAP_OFFSET_CAPS_ID           = 18
CAP_OFFSET_CONFIG_ID         = 19
CAP_OFFSET_HEARTBEAT_SEC     = 20
CAP_OFFSET_FAILSAFE_SEC      = 21
CAP_OFFSET_SLEEP_ACCESS      = 22
CAP_OFFSET_DEVICE_VERSION    = 23

# Config/runtime Feature offsets (Report ID 0x11).
CONFIG_OFFSET_INTENSITY       = 6
CONFIG_OFFSET_WAVEFORM        = 7
CONFIG_OFFSET_LAYOUT          = 8
CONFIG_OFFSET_CONFIG_FLAGS    = 9
CONFIG_OFFSET_HEARTBEAT_SEC   = 10
CONFIG_OFFSET_FAILSAFE_SEC    = 11
CONFIG_OFFSET_MOTOR_MASK      = 12
CONFIG_OFFSET_SLEEP_ACCESS    = 13
CONFIG_OFFSET_STATE_FLAGS     = 14
CONFIG_OFFSET_RAW_QUEUE       = 15
CONFIG_OFFSET_INPUT_QUEUE     = 16
CONFIG_OFFSET_PLAY_ACCEPTED   = 17
CONFIG_OFFSET_PLAY_REJECTED   = 19
CONFIG_OFFSET_I2C_ERRORS      = 21
CONFIG_OFFSET_STATUS_QUEUED   = 23
CONFIG_OFFSET_STATUS_SENT     = 25
CONFIG_OFFSET_NOTIFY_REASSERT = 27
CONFIG_OFFSET_NOTIFY_EXHAUST  = 29
CONFIG_OFFSET_TRANSPORT_REV   = 30


# ================================================================
# CRC8 (polynomial 0x07, compatible with CRC-8-CCITT)
# ================================================================
def _crc8_table():
    """Pre-compute CRC8 lookup table for polynomial 0x07."""
    tbl = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
        tbl.append(crc)
    return tbl

_CRC8_TABLE = _crc8_table()


def compute_crc8(data: bytes) -> int:
    """Compute CRC8 over the given data bytes (poly 0x07)."""
    crc = 0
    for byte in data:
        crc = _CRC8_TABLE[crc ^ byte]
    return crc


# ================================================================
# Report builders
# ================================================================
def _build_base_report(report_id: int, opcode: int, flags: int,
                       sequence: int = 0) -> bytearray:
    """Create a 32-byte report with common header fields zeroed."""
    report = bytearray(REPORT_BYTES)
    report[OFFSET_REPORT_ID] = report_id
    report[OFFSET_VERSION]   = PROTOCOL_VERSION
    report[OFFSET_OPCODE]    = opcode
    report[OFFSET_FLAGS]     = flags & 0xFF
    report[OFFSET_SEQUENCE]  = sequence & 0xFF
    report[OFFSET_SEQUENCE + 1] = (sequence >> 8) & 0xFF
    return report


def _finalize_report(report: bytearray, use_crc: bool) -> bytes:
    """Compute CRC8 over bytes 0-30 if flag set, else leave as-is."""
    if use_crc:
        report[OFFSET_FLAGS] |= FLAG_CRC8
        report[OFFSET_CRC8] = compute_crc8(report[:OFFSET_CRC8])
    return bytes(report)


def build_event_report(waveform: int = 1,
                       intensity: int = INTENSITY_STRONG,
                       ack_required: bool = False,
                       sequence: int = 0,
                       zone_id: int = 0,
                       x: int = 0, y: int = 0,
                       touch_phase: int = 0,
                       pressure: int = 0,
                       layout_id: int = 0,
                       key_id: int = 0,
                       age_us: int = 0,
                       latency_trace: bool = False,
                       use_crc: bool = False) -> bytes:
    """
    Build an Event Output Report (Report ID 0x01) — PLAY command.

    Args:
        waveform:   Waveform index (1-based, 1=TS01, 2=TS02, ...). 0 = keep current.
        intensity:  1=Strong, 2=Medium, 3=Weak.
        ack_required: If True, MCU will push a Status Input Report.
        sequence:   Sequence number for dedup (0 = skip dedup).
        zone_id:    Touch zone identifier.
        x, y:       Touch coordinates (0-65535).
        touch_phase: 0=unspecified, 1=down, 2=repeat.
        pressure:   0-255 touch pressure.
        layout_id:  Keyboard layout identifier.
        key_id:     Logical key identifier (0-65535).
        age_us:     Host-side age before report encoding, saturated to 65535 us.
        use_crc:    If True, set flags.bit0 and append CRC8 over bytes 0-30.
    """
    flags = FLAG_ACK_REQUIRED if ack_required else 0
    if latency_trace:
        flags |= FLAG_LATENCY_TRACE
    report = _build_base_report(REPORT_ID_EVENT, OPCODE_PLAY,
                                flags, sequence)

    report[EVENT_OFFSET_LAYOUT]      = layout_id & 0xFF
    report[EVENT_OFFSET_KEY_ID]      = key_id & 0xFF
    report[EVENT_OFFSET_KEY_ID + 1]  = (key_id >> 8) & 0xFF
    report[EVENT_OFFSET_ZONE]        = zone_id & 0xFF
    report[EVENT_OFFSET_X]           = x & 0xFF
    report[EVENT_OFFSET_X + 1]       = (x >> 8) & 0xFF
    report[EVENT_OFFSET_Y]           = y & 0xFF
    report[EVENT_OFFSET_Y + 1]       = (y >> 8) & 0xFF
    report[EVENT_OFFSET_WAVEFORM]    = waveform & 0xFF
    report[EVENT_OFFSET_INTENSITY]   = intensity & 0xFF
    report[EVENT_OFFSET_TOUCH_PHASE] = touch_phase & 0xFF
    report[EVENT_OFFSET_PRESSURE]    = pressure & 0xFF
    age_us = max(0, min(int(age_us), 0xFFFF))
    report[EVENT_OFFSET_AGE_US]      = age_us & 0xFF
    report[EVENT_OFFSET_AGE_US + 1]  = (age_us >> 8) & 0xFF
    # bytes 20-30 are reserved, already zeroed by bytearray

    return _finalize_report(report, use_crc=use_crc)


def build_control_report(opcode: int, value0: int = 0,
                         ack_required: bool = False,
                         sequence: int = 0,
                         use_crc: bool = False) -> bytes:
    """
    Build a Control Output Report (Report ID 0x02).

    Args:
        opcode: SESSION_OPEN(0x20), SESSION_CLOSE(0x21),
                HEARTBEAT(0x22), SET_DEFAULT_INTENSITY(0x30),
                SET_DEFAULT_WAVEFORM(0x31).
        value0: Parameter value (used by SET_DEFAULT_* opcodes).
        ack_required: Request ACK status report.
        sequence: Dedup sequence number.
        use_crc: If True, set flags.bit0 and append CRC8 over bytes 0-30.
    """
    flags = FLAG_ACK_REQUIRED if ack_required else 0
    report = _build_base_report(REPORT_ID_CONTROL, opcode,
                                flags, sequence)

    report[6] = value0 & 0xFF   # value0 at byte 6
    # bytes 7-30 are reserved, already zeroed

    return _finalize_report(report, use_crc=use_crc)


# ================================================================
# Status Report decoder
# ================================================================
@dataclass
class StatusReport:
    """Parsed Status Input Report (Report ID 0x81)."""
    report_id:      int = 0
    version:        int = 0
    opcode:         int = 0      # 0x01=ACK, 0x02=ERROR, 0x03=SNAPSHOT
    result:         int = 0
    original_id:    int = 0
    original_opcode:int = 0
    state_flags:    int = 0
    haptic_queue:   int = 0
    hid_raw_queue:  int = 0
    motor_mask:     int = 0
    waveform:       int = 0
    intensity:      int = 0
    layout:         int = 0
    zone:           int = 0
    key_id:         int = 0
    x:              int = 0
    y:              int = 0
    last_latency_ms:int = 0
    detail:         int = 0
    input_queue:    int = 0
    response_queue: int = 0
    play_accepted:  int = 0
    i2c_errors:     int = 0
    raw:            bytes = field(default=b"")

    @property
    def is_ok(self) -> bool:
        return self.result == RESULT_OK

    @property
    def result_name(self) -> str:
        return RESULT_NAMES.get(self.result, f"UNKNOWN(0x{self.result:02X})")

    @property
    def session_open(self) -> bool:
        return bool(self.state_flags & STATE_SESSION_OPEN)

    @property
    def session_warm(self) -> bool:
        return bool(self.state_flags & STATE_SESSION_WARM)

    @property
    def runtime_busy(self) -> bool:
        return bool(self.state_flags & STATE_RUNTIME_BUSY)

    @property
    def powered_on(self) -> bool:
        return bool(self.state_flags & STATE_POWERED_ON)


@dataclass(frozen=True)
class CapabilitiesReport:
    """Runtime limits advertised by the connected MCU firmware."""
    capability_flags: int
    motor_count: int
    active_motors: int
    motor_mask: int
    waveform_count: int
    raw_queue_depth: int
    input_queue_depth: int
    report_bytes: int
    event_report_id: int
    control_report_id: int
    status_report_id: int
    capabilities_report_id: int
    config_report_id: int
    heartbeat_seconds: int
    failsafe_seconds: int
    sleep_accessible: bool
    device_version: int

    def supports(self, capability: int) -> bool:
        """Return whether the firmware explicitly advertises a feature."""
        return bool(self.capability_flags & capability)

    @property
    def supports_latency_trace(self) -> bool:
        return self.supports(CAP_LATENCY_TRACE)

    def as_dict(self) -> dict:
        return {
            "capability_flags": self.capability_flags,
            "motor_count": self.motor_count,
            "active_motors": self.active_motors,
            "motor_mask": self.motor_mask,
            "waveform_count": self.waveform_count,
            "raw_queue_depth": self.raw_queue_depth,
            "input_queue_depth": self.input_queue_depth,
            "report_bytes": self.report_bytes,
            "event_report_id": self.event_report_id,
            "control_report_id": self.control_report_id,
            "status_report_id": self.status_report_id,
            "capabilities_report_id": self.capabilities_report_id,
            "config_report_id": self.config_report_id,
            "heartbeat_seconds": self.heartbeat_seconds,
            "failsafe_seconds": self.failsafe_seconds,
            "sleep_accessible": self.sleep_accessible,
            "device_version": self.device_version,
            "supports_latency_trace": self.supports_latency_trace,
        }


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Config plus live health counters returned by Feature Report 0x11."""
    sequence: int
    intensity: int
    waveform: int
    layout: int
    config_flags: int
    heartbeat_seconds: int
    failsafe_seconds: int
    motor_mask: int
    sleep_accessible: bool
    state_flags: int
    raw_queue: int
    input_queue: int
    play_accepted: int
    play_rejected: int
    i2c_errors: int
    status_queued: int
    status_sent: int
    notify_reasserts: int
    notify_exhausted: int
    transport_revision: int
    raw: bytes = field(default=b"")

    @property
    def session_open(self) -> bool:
        return bool(self.state_flags & STATE_SESSION_OPEN)

    @property
    def session_warm(self) -> bool:
        return bool(self.state_flags & STATE_SESSION_WARM)

    @property
    def runtime_busy(self) -> bool:
        return bool(self.state_flags & STATE_RUNTIME_BUSY)

    @property
    def powered_on(self) -> bool:
        return bool(self.state_flags & STATE_POWERED_ON)


def _feature_report_is_valid(data: bytes, report_id: int) -> bool:
    if len(data) < REPORT_BYTES or data[0] != report_id:
        return False
    if data[1] != PROTOCOL_VERSION:
        return False
    if data[OFFSET_FLAGS] & FLAG_CRC8:
        return compute_crc8(data[:OFFSET_CRC8]) == data[OFFSET_CRC8]
    return True


def parse_capabilities_feature(data: bytes) -> Optional[CapabilitiesReport]:
    """Decode synchronous Feature Report 0x10."""
    if not _feature_report_is_valid(data, REPORT_ID_CAPABILITIES):
        return None
    return CapabilitiesReport(
        capability_flags=data[CAP_OFFSET_CAP_FLAGS] |
                         (data[CAP_OFFSET_CAP_FLAGS + 1] << 8),
        motor_count=data[CAP_OFFSET_MOTOR_COUNT],
        active_motors=data[CAP_OFFSET_ACTIVE_MOTORS],
        motor_mask=data[CAP_OFFSET_MOTOR_MASK],
        waveform_count=data[CAP_OFFSET_WAVEFORM_COUNT],
        raw_queue_depth=data[CAP_OFFSET_RAW_QUEUE_DEPTH],
        input_queue_depth=data[CAP_OFFSET_INPUT_QUEUE_DEPTH],
        report_bytes=data[CAP_OFFSET_REPORT_BYTES],
        event_report_id=data[CAP_OFFSET_EVENT_ID],
        control_report_id=data[CAP_OFFSET_CONTROL_ID],
        status_report_id=data[CAP_OFFSET_STATUS_ID],
        capabilities_report_id=data[CAP_OFFSET_CAPS_ID],
        config_report_id=data[CAP_OFFSET_CONFIG_ID],
        heartbeat_seconds=data[CAP_OFFSET_HEARTBEAT_SEC],
        failsafe_seconds=data[CAP_OFFSET_FAILSAFE_SEC],
        sleep_accessible=bool(data[CAP_OFFSET_SLEEP_ACCESS]),
        device_version=data[CAP_OFFSET_DEVICE_VERSION],
    )


def parse_runtime_feature(data: bytes) -> Optional[RuntimeSnapshot]:
    """Decode synchronous Config/runtime Feature Report 0x11."""
    if not _feature_report_is_valid(data, REPORT_ID_CONFIG):
        return None
    u16 = lambda offset: data[offset] | (data[offset + 1] << 8)
    return RuntimeSnapshot(
        sequence=u16(OFFSET_SEQUENCE),
        intensity=data[CONFIG_OFFSET_INTENSITY],
        waveform=data[CONFIG_OFFSET_WAVEFORM],
        layout=data[CONFIG_OFFSET_LAYOUT],
        config_flags=data[CONFIG_OFFSET_CONFIG_FLAGS],
        heartbeat_seconds=data[CONFIG_OFFSET_HEARTBEAT_SEC],
        failsafe_seconds=data[CONFIG_OFFSET_FAILSAFE_SEC],
        motor_mask=data[CONFIG_OFFSET_MOTOR_MASK],
        sleep_accessible=bool(data[CONFIG_OFFSET_SLEEP_ACCESS]),
        state_flags=data[CONFIG_OFFSET_STATE_FLAGS],
        raw_queue=data[CONFIG_OFFSET_RAW_QUEUE],
        input_queue=data[CONFIG_OFFSET_INPUT_QUEUE],
        play_accepted=u16(CONFIG_OFFSET_PLAY_ACCEPTED),
        play_rejected=u16(CONFIG_OFFSET_PLAY_REJECTED),
        i2c_errors=u16(CONFIG_OFFSET_I2C_ERRORS),
        status_queued=u16(CONFIG_OFFSET_STATUS_QUEUED),
        status_sent=u16(CONFIG_OFFSET_STATUS_SENT),
        notify_reasserts=u16(CONFIG_OFFSET_NOTIFY_REASSERT),
        notify_exhausted=data[CONFIG_OFFSET_NOTIFY_EXHAUST],
        transport_revision=data[CONFIG_OFFSET_TRANSPORT_REV],
        raw=bytes(data[:REPORT_BYTES]),
    )


def parse_capabilities_status(status: StatusReport) -> Optional[CapabilitiesReport]:
    """Decode the Status/SNAPSHOT response to CAPABILITIES_QUERY."""
    if (not status or not status.is_ok or
            status.opcode != OPCODE_STATUS_SNAPSHOT or
            status.original_id != REPORT_ID_CONTROL or
            status.original_opcode != OPCODE_CAPABILITIES_QUERY or
            len(status.raw) < REPORT_BYTES):
        return None
    data = status.raw
    return CapabilitiesReport(
        capability_flags=(data[CAP_STATUS_OFFSET_CAP_FLAGS] |
                          (data[CAP_STATUS_OFFSET_CAP_FLAGS + 1] << 8)),
        motor_count=data[CAP_STATUS_OFFSET_MOTOR_COUNT],
        active_motors=data[CAP_STATUS_OFFSET_ACTIVE_MOTORS],
        motor_mask=data[CAP_STATUS_OFFSET_MOTOR_MASK],
        waveform_count=data[CAP_STATUS_OFFSET_WAVEFORM_COUNT],
        raw_queue_depth=data[CAP_STATUS_OFFSET_RAW_QUEUE_DEPTH],
        input_queue_depth=data[CAP_STATUS_OFFSET_INPUT_QUEUE_DEPTH],
        report_bytes=data[CAP_STATUS_OFFSET_REPORT_BYTES],
        event_report_id=data[CAP_STATUS_OFFSET_EVENT_ID],
        control_report_id=data[CAP_STATUS_OFFSET_CONTROL_ID],
        status_report_id=data[CAP_STATUS_OFFSET_STATUS_ID],
        capabilities_report_id=data[CAP_STATUS_OFFSET_CAPS_ID],
        config_report_id=data[CAP_STATUS_OFFSET_CONFIG_ID],
        heartbeat_seconds=data[CAP_STATUS_OFFSET_HEARTBEAT_SEC],
        failsafe_seconds=data[CAP_STATUS_OFFSET_FAILSAFE_SEC],
        sleep_accessible=bool(data[CAP_STATUS_OFFSET_SLEEP_ACCESS]),
        device_version=data[CAP_STATUS_OFFSET_DEVICE_VERSION],
    )


def parse_status_report(data: bytes) -> Optional[StatusReport]:
    """Parse a 32-byte Status Input Report. Returns None if invalid."""
    if len(data) < REPORT_BYTES:
        return None
    if data[OFFSET_REPORT_ID] != REPORT_ID_STATUS:
        return None

    s = StatusReport()
    s.report_id       = data[0]
    s.version         = data[1]
    s.opcode          = data[2]
    s.result          = data[STATUS_OFFSET_RESULT]
    s.original_id     = data[STATUS_OFFSET_ORIGINAL_ID]
    s.original_opcode = data[STATUS_OFFSET_ORIGINAL_OPCODE]
    s.state_flags     = data[STATUS_OFFSET_STATE_FLAGS]
    s.haptic_queue    = data[STATUS_OFFSET_HAPTIC_QUEUE]
    s.hid_raw_queue   = data[STATUS_OFFSET_HID_RAW_QUEUE]
    s.motor_mask      = data[STATUS_OFFSET_MOTOR_MASK]
    s.waveform        = data[STATUS_OFFSET_WAVEFORM]
    s.intensity       = data[STATUS_OFFSET_INTENSITY]
    s.layout          = data[STATUS_OFFSET_LAYOUT]
    s.zone            = data[STATUS_OFFSET_ZONE]
    s.key_id          = data[STATUS_OFFSET_KEY_ID] | (data[STATUS_OFFSET_KEY_ID + 1] << 8)
    s.x               = data[STATUS_OFFSET_X] | (data[STATUS_OFFSET_X + 1] << 8)
    s.y               = data[STATUS_OFFSET_Y] | (data[STATUS_OFFSET_Y + 1] << 8)
    s.last_latency_ms = data[STATUS_OFFSET_LAST_LATENCY_MS] | (data[STATUS_OFFSET_LAST_LATENCY_MS + 1] << 8)
    s.detail          = data[STATUS_OFFSET_DETAIL]
    s.input_queue     = data[STATUS_OFFSET_INPUT_QUEUE]
    s.response_queue  = data[STATUS_OFFSET_RESPONSE_QUEUE]
    s.play_accepted   = data[STATUS_OFFSET_PLAY_ACCEPTED]
    s.i2c_errors      = data[STATUS_OFFSET_I2C_ERRORS]
    s.raw             = bytes(data)
    return s


# ================================================================
# Haptic USB / HID Device Manager
# ================================================================
class HapticDeviceError(Exception):
    """Raised when device communication fails."""
    pass


class HapticDeviceNotFound(HapticDeviceError):
    """Raised when the haptic HID device is not found."""
    pass


class HapticHID:
    """
    Manages the haptic HID device connection and protocol.

    Usage:
        with HapticHID() as dev:
            dev.open_session()
            dev.play(waveform=2, intensity=1)
            dev.close_session()
    """

    def __init__(self, vid: int = VENDOR_ID, pid: int = PRODUCT_ID,
                 use_crc: bool = False, status_mode: str = "auto"):
        if status_mode not in ("auto", "feature", "interrupt", "write-only"):
            raise ValueError(
                "status_mode must be auto, feature, interrupt, or write-only"
            )
        self._vid = vid
        self._pid = pid
        self._use_crc = use_crc
        self._device = None
        self._path: Optional[bytes] = None
        self._sequence: int = 0
        self.last_event_sequence: int = 0
        self._session_open: bool = False
        self._session_open_last_attempt: float = 0.0
        self._silent_heartbeats: int = 0
        self._requested_status_mode = status_mode
        self._active_status_mode = (
            "feature" if status_mode == "auto" else status_mode
        )
        self._feature_fallback_logged = False
        self.capabilities: Optional[CapabilitiesReport] = None
        self.runtime_snapshot: Optional[RuntimeSnapshot] = None

    @property
    def status_mode(self) -> str:
        """Currently active device-to-host feedback path."""
        return self._active_status_mode

    @property
    def requested_status_mode(self) -> str:
        """Feedback mode requested before any automatic fallback."""
        return self._requested_status_mode

    @property
    def session_open(self) -> bool:
        """Whether this host instance currently considers the session open."""
        return self._session_open

    @property
    def feedback_confirmed(self) -> bool:
        """Whether MCU state/settings can be read back and confirmed."""
        return self._active_status_mode != "write-only"

    @property
    def degraded_reason(self) -> Optional[str]:
        if self._active_status_mode == "write-only":
            return (
                "MCU feedback unavailable; PLAY remains enabled but session "
                "and configuration cannot be confirmed"
            )
        return None

    # ── Device discovery ────────────────────────────────────────
    @staticmethod
    def enumerate_devices(vid: int = VENDOR_ID, pid: int = PRODUCT_ID) -> list:
        """List all matching haptic HID devices."""
        results = []
        for d in hid.enumerate(vid, pid):
            results.append({
                "path":          d["path"],
                "vendor_id":     d["vendor_id"],
                "product_id":    d["product_id"],
                "serial_number": d.get("serial_number", ""),
                "manufacturer":  d.get("manufacturer_string", ""),
                "product":       d.get("product_string", ""),
                "interface":     d.get("interface_number", -1),
                "usage_page":    d.get("usage_page", 0),
                "usage":         d.get("usage", 0),
            })
        return results

    def find_device(self) -> Optional[str]:
        """Find the first matching haptic HID device path."""
        devices = self.enumerate_devices(self._vid, self._pid)
        if devices:
            return devices[0]["path"]
        return None

    # ── Connection management ───────────────────────────────────
    @property
    def is_connected(self) -> bool:
        return self._device is not None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def open(self, path: Optional[str] = None) -> bool:
        """Open the HID device. Returns True on success."""
        if self._device is not None:
            return True

        if path is None:
            path = self.find_device()
            if path is None:
                raise HapticDeviceNotFound(
                    f"Haptic HID device not found "
                    f"(VID=0x{self._vid:04X}, PID=0x{self._pid:04X}). "
                    f"Check USB/I2C connection and driver."
                )

        try:
            self._device = _open_backend_device(path)
            self._path = path if isinstance(path, bytes) else str(path).encode()
            logger.info(
                f"Haptic HID device opened via {backend_name()}: {path}"
            )
            return True
        except (OSError, ValueError) as e:
            raise HapticDeviceError(
                f"Failed to open HID device at {path}: {e}"
            ) from e

    def close(self):
        """Close the HID device."""
        if self._session_open:
            try:
                self._send_control_report(OPCODE_SESSION_CLOSE)
            except Exception:
                pass
            self._session_open = False

        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
            self._path = None
            logger.info("Haptic HID device closed.")

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) & 0xFFFF
        if self._sequence == 0:
            self._sequence = 1
        return self._sequence

    # ── Low-level write ─────────────────────────────────────────
    def _write_report(self, report: bytes) -> int:
        """Write an Output Report to the device. Returns bytes written.

        The return value is checked: hidapi's write() reports failures by
        returning 0 or -1, NOT by raising. Silently ignoring that made Control
        reports (Report ID 0x02) vanish between the host and the bus while
        Event reports (0x01) went through - the host believed it had sent
        SESSION_OPEN/HEARTBEAT, the MCU never saw them, and the keyboard
        session never opened.
        """
        if self._device is None:
            raise HapticDeviceError("Device not connected.")
        if len(report) != REPORT_BYTES:
            raise ValueError(
                f"Report must be {REPORT_BYTES} bytes, got {len(report)}"
            )
        try:
            written = self._device.write(report)
        except (OSError, ValueError) as e:
            raise HapticDeviceError(f"HID write failed: {e}") from e
        if written != len(report):
            raise HapticDeviceError(
                f"HID write short: {written}/{len(report)} "
                f"(report id=0x{report[0]:02X}) - the write was rejected by "
                f"the HID stack"
            )
        return written

    def write_report(self, report: bytes) -> int:
        """Write a pre-built 32-byte Output Report (public wrapper).

        Used by tools/hidi2c_output_test.py so latency timing can wrap the
        HID write only, without re-implementing the report layout.
        """
        return self._write_report(report)

    def _send_event_report(self, **kwargs) -> bytes:
        """Build and send an Event Report (PLAY). Returns the report bytes."""
        seq = kwargs.pop("sequence", self._next_sequence())
        kwargs.setdefault("use_crc", self._use_crc)
        report = build_event_report(sequence=seq, **kwargs)
        self._write_report(report)
        logger.debug(
            f"Event sent: waveform={kwargs.get('waveform')}, "
            f"intensity={kwargs.get('intensity')}, seq={seq}"
        )
        return report

    def _send_control_report(self, opcode: int, value0: int = 0,
                             ack_required: bool = False) -> bytes:
        """Build and send a Control Report."""
        seq = self._next_sequence()
        report = build_control_report(
            opcode=opcode, value0=value0,
            ack_required=ack_required, sequence=seq,
            use_crc=self._use_crc,
        )
        self._write_report(report)
        logger.debug(f"Control sent: opcode=0x{opcode:02X}, seq={seq}")
        return report

    # ── Read Input Report ───────────────────────────────────────
    def read_status(self,
                    timeout_ms: int = DEVICE_READ_TIMEOUT_MS
                    ) -> Optional[StatusReport]:
        """
        Attempt to read a Status Input Report from the device.

        Returns a StatusReport if available, None if timeout.
        """
        if self._device is None:
            return None
        try:
            data = _read_backend_device(self._device, REPORT_BYTES, timeout_ms)
            if data and len(data) >= REPORT_BYTES:
                return parse_status_report(bytes(data))
        except Exception as e:      # hidapi raises OSError, hid raises HIDException
            logger.debug(f"Status read failed: {e}")
        return None

    def drain_status(self, max_count: int = 10) -> list:
        """Read and drain all pending Status Reports."""
        reports = []
        for _ in range(max_count):
            r = self.read_status(timeout_ms=10)
            if r is None:
                break
            reports.append(r)
        return reports

    def _get_feature_report(self, report_id: int) -> Optional[bytes]:
        """Issue one synchronous Feature read; never poll this per key."""
        if self._device is None:
            return None
        try:
            data = _get_feature_backend_device(
                self._device, report_id, REPORT_BYTES
            )
            if data and len(data) >= REPORT_BYTES:
                return bytes(data[:REPORT_BYTES])
        except Exception as e:
            logger.debug("Feature 0x%02X read failed: %s", report_id, e)
        return None

    def read_runtime_snapshot(self) -> Optional[RuntimeSnapshot]:
        snapshot = parse_runtime_feature(
            self._get_feature_report(REPORT_ID_CONFIG) or b""
        )
        if snapshot is not None:
            self.runtime_snapshot = snapshot
        return snapshot

    def _wait_runtime(self, predicate, timeout_ms: int = 500,
                      poll_interval_ms: int = FEATURE_POLL_INTERVAL_MS
                      ) -> Optional[RuntimeSnapshot]:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            snapshot = self.read_runtime_snapshot()
            if snapshot is not None and predicate(snapshot):
                return snapshot
            time.sleep(poll_interval_ms / 1000.0)
        return None

    def _fallback_from_feature(self, context: str) -> bool:
        if self._requested_status_mode != "auto":
            return False
        self._active_status_mode = "write-only"
        if not self._feature_fallback_logged:
            logger.warning(
                "Feature status unavailable during %s; continuing in "
                "write-only mode. Haptics remain active, but settings/session "
                "cannot be confirmed until firmware GET_REPORT is available.",
                context,
            )
            self._feature_fallback_logged = True
        return True

    def _read_matching_status(self, expected_id: int, expected_opcode: int,
                              timeout_ms: int) -> Optional[StatusReport]:
        """Read the response belonging to one Output report.

        Old queued Status reports must never acknowledge a newer operation.
        ``read_status`` blocks for the supplied timeout on the real backend, so
        a timeout returns immediately instead of spinning until the deadline.
        """
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while True:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            status = self.read_status(timeout_ms=remaining_ms)
            if status is None:
                return None
            if (status.original_id == expected_id and
                    status.original_opcode == expected_opcode):
                return status
            logger.debug(
                "Discarding stale Status for id=0x%02X opcode=0x%02X "
                "while waiting for id=0x%02X opcode=0x%02X",
                status.original_id, status.original_opcode,
                expected_id, expected_opcode,
            )
            if time.monotonic() >= deadline:
                return None

    # ── Session management ──────────────────────────────────────
    def open_session(self, force: bool = False) -> bool:
        """
        Open a keyboard haptic session (Control report SESSION_OPEN).

        Only reports success when the device actually ACKs with
        result=OK *and* the SESSION_OPEN state flag set. Assuming success
        without an ACK used to latch ``_session_open = True`` forever, so
        ``play()`` never retried and the MCU stayed in the cold-start path
        (observed on hardware: ``kb session open/warm=0/0``,
        ``start cold/hot=31/0``, ``keep enter/timeout=31/31``, and
        ``trigger->DMA`` stuck at 22 ms).

        The very first Control write right after enumeration can also be
        lost while the HID stack is still settling, so retry once here and
        let ``play()`` retry later (rate limited) when still unconfirmed.

        Returns:
            True only if the session is confirmed open.
        """
        if self._session_open and not force:
            return True

        now = time.monotonic()
        if (not force) and (self._session_open_last_attempt != 0.0) and \
                ((now - self._session_open_last_attempt) < SESSION_OPEN_RETRY_SEC):
            return False        # back off, play() will try again later
        self._session_open_last_attempt = now

        for attempt in (1, 2):
            interrupt_mode = self._active_status_mode == "interrupt"
            self._send_control_report(
                OPCODE_SESSION_OPEN, ack_required=interrupt_mode
            )

            if self._active_status_mode == "write-only":
                self._session_open = True
                time.sleep(WRITE_ONLY_PREWARM_MS / 1000.0)
                logger.info("Haptic session opened in write-only mode.")
                return True

            if self._active_status_mode == "feature":
                self._session_open = True
                self.runtime_snapshot = None
                if self.wait_until_warm(timeout_ms=FEATURE_OPEN_TIMEOUT_MS):
                    logger.info(
                        "Haptic session warm confirmed by Feature GET_REPORT."
                    )
                    return True
                if (self.runtime_snapshot is None and
                        self._fallback_from_feature("SESSION_OPEN")):
                    time.sleep(WRITE_ONLY_PREWARM_MS / 1000.0)
                    self._session_open = True
                    return True
                self._session_open = False
                continue

            status = self._read_matching_status(
                REPORT_ID_CONTROL, OPCODE_SESSION_OPEN, timeout_ms=500
            )
            if status and status.is_ok and status.session_open:
                self._session_open = True
                logger.info("Haptic session opened via interrupt Status.")
                if self.wait_until_warm(timeout_ms=FEATURE_OPEN_TIMEOUT_MS):
                    return True
                logger.warning("Interrupt Status never confirmed warm state.")
                self._session_open = False
            logger.warning(
                "Session open attempt %d: no valid interrupt Status report.",
                attempt,
            )

        # Unconfirmed: keep the flag clear so the next play() retries instead of
        # silently running every key press through the MCU cold-start path.
        self._session_open = False
        return False

    def query_status(self, timeout_ms: int = 200) -> Optional[StatusReport]:
        """Request and read one current MCU status snapshot."""
        self._send_control_report(OPCODE_STATUS_QUERY, ack_required=True)
        return self._read_matching_status(
            REPORT_ID_CONTROL, OPCODE_STATUS_QUERY, timeout_ms=timeout_ms
        )

    def query_capabilities(self, timeout_ms: int = 500
                           ) -> Optional[CapabilitiesReport]:
        """Read firmware-defined limits, preferring synchronous Feature I/O."""
        if self._active_status_mode == "feature":
            capabilities = parse_capabilities_feature(
                self._get_feature_report(REPORT_ID_CAPABILITIES) or b""
            )
            if capabilities is not None:
                self.capabilities = capabilities
                return capabilities
            self._fallback_from_feature("CAPABILITIES_QUERY")
            return None
        if self._active_status_mode == "write-only":
            return None
        self._send_control_report(OPCODE_CAPABILITIES_QUERY, ack_required=True)
        status = self._read_matching_status(
            REPORT_ID_CONTROL, OPCODE_CAPABILITIES_QUERY,
            timeout_ms=timeout_ms,
        )
        capabilities = parse_capabilities_status(status) if status else None
        if capabilities is not None:
            self.capabilities = capabilities
        return capabilities

    def wait_until_warm(self, timeout_ms: int = 1000,
                        poll_interval_ms: int = 10) -> bool:
        """Poll explicit MCU warm state; no fixed host-side delay is assumed."""
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            if self._active_status_mode == "feature":
                state = self.read_runtime_snapshot()
            else:
                state = self.query_status(timeout_ms=min(200, timeout_ms))
            if state:
                if not state.session_open:
                    time.sleep(poll_interval_ms / 1000.0)
                    continue
                if state.session_warm:
                    return True
            time.sleep(poll_interval_ms / 1000.0)
        return False

    def close_session(self) -> bool:
        """
        Close the keyboard haptic session.
        Sends SESSION_CLOSE control report.
        """
        if self._device is None:
            logger.warning("SESSION_CLOSE skipped: HID device is not connected.")
            return False

        if not self._session_open:
            logger.info("Sending SESSION_CLOSE as cleanup for an unconfirmed session.")

        interrupt_mode = self._active_status_mode == "interrupt"
        self._send_control_report(
            OPCODE_SESSION_CLOSE, ack_required=interrupt_mode
        )
        if not interrupt_mode:
            self._session_open = False
            logger.info("Haptic session close sent (non-blocking).")
            return True
        status = self._read_matching_status(
            REPORT_ID_CONTROL, OPCODE_SESSION_CLOSE, timeout_ms=500
        )
        self._session_open = False
        if status and status.is_ok:
            logger.info("Haptic session closed and acknowledged.")
            return True
        logger.warning("SESSION_CLOSE was sent but not acknowledged.")
        return False

    def heartbeat(self) -> bool:
        """
        Send a HEARTBEAT control report to keep the session alive.

        Returns True if the session is still believed valid.
        """
        interrupt_mode = self._active_status_mode == "interrupt"
        self._send_control_report(OPCODE_HEARTBEAT, ack_required=interrupt_mode)
        if self._active_status_mode == "write-only":
            return self._session_open
        if self._active_status_mode == "feature":
            snapshot = self.read_runtime_snapshot()
            if snapshot is None:
                if self._fallback_from_feature("HEARTBEAT"):
                    return self._session_open
                self._session_open = False
                return False
            self._session_open = snapshot.session_open
            if not snapshot.session_open:
                logger.warning("Heartbeat snapshot says session is closed.")
            return snapshot.session_open
        status = self._read_matching_status(
            REPORT_ID_CONTROL, OPCODE_HEARTBEAT, timeout_ms=200
        )
        if status:
            self._silent_heartbeats = 0
            if status.result == RESULT_NOT_OPEN:
                self._session_open = False
                logger.warning("Heartbeat: session not open, will re-open.")
                return False
            self._session_open = status.session_open
            return status.is_ok

        # No Status came back. That alone is not fatal, but it also means a
        # stale session can never be detected - and the MCU drops the session
        # on its own after APP_KB_SESSION_FAILSAFE_MS, or loses it entirely on
        # a reset / re-flash / re-enumeration. Silently trusting the cached
        # flag is what kept every key press on the cold-start path.
        self._silent_heartbeats += 1
        if self._silent_heartbeats >= SILENT_HEARTBEATS_BEFORE_REOPEN:
            self._silent_heartbeats = 0
            if self._session_open:
                logger.warning(
                    f"{SILENT_HEARTBEATS_BEFORE_REOPEN} heartbeats without a "
                    f"Status report: assuming the session is stale and "
                    f"re-opening it."
                )
            self._session_open = False
            return False
        return True   # no news, keep going for now

    def transport_keepalive(self) -> bool:
        """Keep the Windows HID/I2C transport active without a Feature read.

        The normal 10-second heartbeat verifies the MCU session.  This lighter
        write-only heartbeat is used between those checks while an OSK session
        is open, preventing long idle gaps from repeatedly paying the Windows
        HID/I2C runtime-resume cost.  It never requests an interrupt Status and
        never triggers haptic playback.
        """
        if not self._session_open:
            return False
        self._send_control_report(OPCODE_HEARTBEAT, ack_required=False)
        return True

    # ── High-level API ──────────────────────────────────────────
    def play(self,
             waveform: int = 1,
             intensity: int = INTENSITY_STRONG,
             ack_required: bool = False,
             age_us: int = 0,
             key_id: int = 0,
             latency_trace: bool = False) -> bool:
        """
        Trigger haptic vibration playback.

        Args:
            waveform:  Waveform index (1=TS01, 2=TS02, ...). 0 = keep current.
            intensity: 1=Strong, 2=Medium, 3=Weak.
            ack_required: If True, wait for status ACK.
            age_us: Host queue age before HID encoding (0..65535 us).
            key_id: Windows virtual-key/logical key identifier.
            latency_trace: Ask MCU to buffer this diagnostic timing sample.

        Returns:
            True if command was accepted.
        """
        if waveform < 0 or waveform > HOST_WAVEFORM_COUNT:
            raise ValueError(
                f"waveform must be 0 (confirmed default) or 1..{HOST_WAVEFORM_COUNT}"
            )
        if intensity not in (1, 2, 3):
            raise ValueError("intensity must be 1 (strong), 2 (medium), or 3 (weak)")
        if not self._session_open:
            logger.debug("Auto-opening session before play.")
            self.open_session()

        report = self._send_event_report(
            waveform=waveform,
            intensity=intensity,
            ack_required=ack_required,
            age_us=age_us,
            key_id=key_id,
            latency_trace=latency_trace,
        )
        self.last_event_sequence = (
            report[OFFSET_SEQUENCE] | (report[OFFSET_SEQUENCE + 1] << 8)
        )

        if ack_required:
            status = self._read_matching_status(
                REPORT_ID_EVENT, OPCODE_PLAY, timeout_ms=200
            )
            if status:
                if status.is_ok:
                    logger.debug(
                        f"Play ACK: ok, latency={status.last_latency_ms}ms, "
                        f"accepted={status.play_accepted}"
                    )
                    return True
                else:
                    logger.warning(
                        f"Play NAK: {status.result_name} "
                        f"(detail=0x{status.detail:02X})"
                    )
                    return False
            logger.warning("Play ACK timeout: no matching Status report.")
            return False

        return True  # fire-and-forget assumed ok

    def set_intensity(self, intensity: int) -> bool:
        """
        Set default intensity via Control Report.

        Args:
            intensity: 1=Strong, 2=Medium, 3=Weak.
        """
        if intensity not in (1, 2, 3):
            raise ValueError("intensity must be 1 (strong), 2 (medium), or 3 (weak)")
        interrupt_mode = self._active_status_mode == "interrupt"
        self._send_control_report(
            OPCODE_SET_DEFAULT_INTENSITY, value0=intensity,
            ack_required=interrupt_mode,
        )
        if self._active_status_mode == "write-only":
            logger.warning("Intensity write sent but cannot be confirmed in write-only mode.")
            return False
        if self._active_status_mode == "feature":
            snapshot = self._wait_runtime(
                lambda item: item.intensity == intensity
            )
            if snapshot is not None:
                return snapshot.intensity == intensity
            self._fallback_from_feature("SET_DEFAULT_INTENSITY")
            return False
        status = self._read_matching_status(
            REPORT_ID_CONTROL, OPCODE_SET_DEFAULT_INTENSITY, timeout_ms=500
        )
        return bool(
            status and status.is_ok and
            status.original_id == REPORT_ID_CONTROL and
            status.original_opcode == OPCODE_SET_DEFAULT_INTENSITY and
            status.intensity == intensity
        )

    def set_waveform(self, waveform: int) -> bool:
        """
        Set default waveform via Control Report.

        Args:
            waveform: Production waveform index (1-4, TS01-TS04 at 48 kHz).
        """
        if waveform < 1 or waveform > HOST_WAVEFORM_COUNT:
            raise ValueError(f"waveform must be in the 1..{HOST_WAVEFORM_COUNT} range")
        interrupt_mode = self._active_status_mode == "interrupt"
        self._send_control_report(
            OPCODE_SET_DEFAULT_WAVEFORM, value0=waveform,
            ack_required=interrupt_mode,
        )
        if self._active_status_mode == "write-only":
            logger.warning("Waveform write sent but cannot be confirmed in write-only mode.")
            return False
        if self._active_status_mode == "feature":
            snapshot = self._wait_runtime(
                lambda item: item.waveform == waveform
            )
            if snapshot is not None:
                return snapshot.waveform == waveform
            self._fallback_from_feature("SET_DEFAULT_WAVEFORM")
            return False
        status = self._read_matching_status(
            REPORT_ID_CONTROL, OPCODE_SET_DEFAULT_WAVEFORM, timeout_ms=500
        )
        return bool(
            status and status.is_ok and
            status.original_id == REPORT_ID_CONTROL and
            status.original_opcode == OPCODE_SET_DEFAULT_WAVEFORM and
            status.waveform == waveform
        )


# ================================================================
# Quick test (run directly)
# ================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    print("=== Haptic HID Device Test ===\n")
    print("Enumerating devices...")
    devices = HapticHID.enumerate_devices()
    if not devices:
        print("No haptic HID devices found!")
    else:
        for d in devices:
            print(f"  Path: {d['path']}")
            print(f"  VID/PID: 0x{d['vendor_id']:04X}/0x{d['product_id']:04X}")
            print(f"  Product: {d['product']}")

    print("\nAttempting to open device and play...")

    try:
        with HapticHID() as dev:
            print(f"  Device opened: {dev.is_connected}")

            print("  Opening session...")
            dev.open_session()

            print("  Playing waveform 2 at medium intensity...")
            dev.play(waveform=2, intensity=INTENSITY_MEDIUM)

            print("  Draining status reports...")
            for s in dev.drain_status():
                print(f"    Status: result={s.result_name}, "
                      f"latency={s.last_latency_ms}ms, "
                      f"queues(raw={s.hid_raw_queue}, input={s.input_queue})")

            print("  Closing session...")
            dev.close_session()

        print("Done.")
    except HapticDeviceNotFound:
        print("Device not found. Is the STM32 connected and powered?")
    except HapticDeviceError as e:
        print(f"Error: {e}")
