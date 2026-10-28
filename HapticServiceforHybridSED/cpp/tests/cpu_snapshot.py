"""Read-only Windows affinity/CPU-set snapshots outside measured key intervals."""
import ctypes as c
from ctypes import wintypes as w
import time

k = c.WinDLL('kernel32', use_last_error=True)
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenProcess.restype = w.HANDLE
k.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenThread.restype = w.HANDLE
k.CloseHandle.argtypes = [w.HANDLE]
k.GetProcessAffinityMask.argtypes = [w.HANDLE, c.POINTER(c.c_size_t), c.POINTER(c.c_size_t)]
k.GetProcessDefaultCpuSets.argtypes = [w.HANDLE, c.POINTER(w.DWORD), w.DWORD, c.POINTER(w.DWORD)]
k.GetThreadSelectedCpuSets.argtypes = k.GetProcessDefaultCpuSets.argtypes
k.GetSystemCpuSetInformation.argtypes = [c.c_void_p, w.DWORD, c.POINTER(w.DWORD), w.HANDLE, w.DWORD]


class GroupAffinity(c.Structure):
    _fields_ = [('mask', c.c_size_t), ('group', w.WORD), ('reserved', w.WORD * 3)]


k.GetThreadGroupAffinity.argtypes = [w.HANDLE, c.POINTER(GroupAffinity)]


def ids(api, handle):
    count = w.DWORD()
    ok = api(handle, None, 0, c.byref(count))
    if not ok and c.get_last_error() != 122:
        return dict(error=c.get_last_error())
    if not count.value:
        return []  # Inheritance/default, not an empty allowed-CPU set.
    buf = (w.DWORD * count.value)()
    if not api(handle, buf, count.value, c.byref(count)):
        return dict(error=c.get_last_error())
    return list(buf)[:count.value]


def capture(pid, tids):
    result = dict(pid=pid, qpc_ns=time.perf_counter_ns(), threads={})
    process = k.OpenProcess(0x1000, False, pid)
    if not process:
        return dict(pid=pid, error=c.get_last_error())
    try:
        mask, system = c.c_size_t(), c.c_size_t()
        if k.GetProcessAffinityMask(process, c.byref(mask), c.byref(system)):
            result.update(process_mask=mask.value, system_mask=system.value)
        else:
            result['affinity_error'] = c.get_last_error()
        result['default_cpu_sets'] = ids(k.GetProcessDefaultCpuSets, process)
        size = w.DWORD()
        k.GetSystemCpuSetInformation(None, 0, c.byref(size), process, 0)
        raw = c.create_string_buffer(size.value)
        if k.GetSystemCpuSetInformation(raw, size.value, c.byref(size), process, 0):
            data = raw.raw[:size.value]
            sets, offset = [], 0
            while offset + 8 <= len(data):
                length = int.from_bytes(data[offset:offset+4], 'little')
                if length < 8 or offset + length > len(data):
                    raise ValueError('Invalid CPU-set record size')
                row = data[offset:offset+length]
                if int.from_bytes(row[4:8], 'little') == 0 and length >= 24:
                    sets.append(dict(id=int.from_bytes(row[8:12], 'little'), group=int.from_bytes(row[12:14], 'little'),
                        logical=row[14], core=row[15], efficiency=row[18], flags=row[19],
                        parked=bool(row[19] & 1), allocated=bool(row[19] & 2), allocated_to_process=bool(row[19] & 4)))
                offset += length
            result['system_cpu_sets'] = sets
        else:
            result['cpu_information_error'] = c.get_last_error()
        for tid in tids:
            h = k.OpenThread(0x40, False, tid)
            if not h:
                result['threads'][str(tid)] = dict(error=c.get_last_error())
                continue
            try:
                group = GroupAffinity()
                item = dict(selected_cpu_sets=ids(k.GetThreadSelectedCpuSets, h))
                if k.GetThreadGroupAffinity(h, c.byref(group)):
                    item.update(group=group.group, mask=group.mask)
                else:
                    item['affinity_error'] = c.get_last_error()
                result['threads'][str(tid)] = item
            finally:
                k.CloseHandle(h)
    finally:
        k.CloseHandle(process)
    return result
