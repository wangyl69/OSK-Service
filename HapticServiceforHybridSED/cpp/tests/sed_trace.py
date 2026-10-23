"""Read the opt-in SED diagnostic mapping; no pipe protocol changes."""
import ctypes as c
from ctypes import wintypes as w
import struct
import time


class SedTrace:
    MAGIC = 0x5345445452433032
    HEADER = 64
    STRIDE = 112

    def __init__(self, name, native_wake=False):
        self.native_wake = native_wake
        self.kernel = c.WinDLL('kernel32', use_last_error=True)
        self.kernel.OpenFileMappingW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
        self.kernel.OpenFileMappingW.restype = w.HANDLE
        self.kernel.MapViewOfFile.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, c.c_size_t]
        self.kernel.MapViewOfFile.restype = c.c_void_p
        self.kernel.UnmapViewOfFile.argtypes = [c.c_void_p]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.kernel.OpenFileMappingW(4, False, name)
        self.address = None
        if not self.handle:
            raise c.WinError(c.get_last_error())
        self.address = self.kernel.MapViewOfFile(self.handle, 4, 0, 0, 0)
        if not self.address:
            error = c.get_last_error()
            self.close()
            raise c.WinError(error)
        magic, self.frequency, self.capacity, stride = struct.unpack('<4q', c.string_at(self.address, 32))
        if magic != self.MAGIC or stride != self.STRIDE or self.frequency <= 0 or self.capacity != 4096:
            self.close()
            raise ValueError('Unsupported SED diagnostic mapping header')

    @property
    def count(self):
        return struct.unpack('<q', c.string_at(self.address + 32, 8))[0]

    def records(self, first, count, timeout=5):
        if first <= 0 or first + count - 1 > self.capacity:
            raise ValueError('SED trace capacity exceeded; start a new diagnostic process')
        deadline = time.monotonic() + timeout
        result = []
        for sequence in range(first, first + count):
            address = self.address + self.HEADER + (sequence - 1) * self.STRIDE
            while True:
                record = struct.unpack('<14q', c.string_at(address, self.STRIDE))
                if record[0] == sequence and record[9] != 0 and record[12] != 0 and (not self.native_wake or record[13] != 0):
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError('SED trace record did not complete: ' + str(sequence))
                time.sleep(0.001)
            if record[9] != 1:
                raise AssertionError({'sed_sequence': sequence, 'terminal_state': record[9]})
            fields = ('sequence', 'hook', 'publish', 'dequeue', 'write_enter',
                      'native_enter', 'native_return', 'write_finish', 'code', 'state', 'native_result',
                      'enqueue_enter', 'enqueue_return', 'wake_signal_return')
            row = dict(zip(fields, record))
            for field in (*fields[1:8], 'enqueue_enter', 'enqueue_return'):
                row[field] = row[field] * 1_000_000 // self.frequency
            row['wake_signal_return'] = row['wake_signal_return'] * 1_000_000 // self.frequency
            if not (0 < row['hook'] <= row['publish'] <= row['dequeue'] <= row['write_enter']
                    <= row['native_enter'] <= row['native_return'] <= row['write_finish']):
                raise AssertionError({'sed_stage_order': row})
            if not row['publish'] <= row['enqueue_enter'] <= row['dequeue'] or row['enqueue_return'] < row['enqueue_enter']:
                raise AssertionError({'sed_enqueue_stage_order': row})
            result.append(row)
        return result

    def snapshot_raw(self):
        # On a failed trial retain even dropped/incomplete records, without
        # requiring a Sent state or replacing missing events with successes.
        total = self.count
        return {'frequency': self.frequency, 'total_started': total,
                'records_ticks': [list(struct.unpack('<14q', c.string_at(
                    self.address + self.HEADER + index * self.STRIDE, self.STRIDE)))
                    for index in range(min(total, self.capacity))]}

    def gc_summary(self):
        initial, final, pause_ticks = struct.unpack('<3q', c.string_at(self.address + 40, 24))
        return {'initial_generation_count_sum': initial, 'final_generation_count_sum': final,
                'reported_pause_during_trace_us': pause_ticks // 10}

    def close(self):
        if self.address:
            self.kernel.UnmapViewOfFile(self.address)
            self.address = None
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
