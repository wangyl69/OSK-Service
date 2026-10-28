"""Isolated real Named Pipe + native mock Service timing, without keyboard injection.

Client WriteFile entry -> Service submit/dequeue/trigger/mock completion uses one QPC.
Does not measure OSK, SED producer scheduling, physical HID/I2C, or vibration.
"""
import ctypes as c
import argparse
from ctypes import wintypes as w
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import check_service_features as features
from analyze_latency import summarize


K = c.WinDLL('kernel32', use_last_error=True)
class Overlapped(c.Structure):
    _fields_ = [('internal', c.c_size_t), ('internal_high', c.c_size_t),
                ('offset', w.DWORD), ('offset_high', w.DWORD), ('event', w.HANDLE)]

K.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
K.CreateFileW.restype = w.HANDLE
K.CreateEventW.argtypes = [c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
K.CreateEventW.restype = w.HANDLE
K.WriteFile.argtypes = [w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.POINTER(Overlapped)]
K.GetOverlappedResult.argtypes = [w.HANDLE, c.POINTER(Overlapped), c.POINTER(w.DWORD), w.BOOL]
K.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
K.ResetEvent.argtypes = [w.HANDLE]
K.CancelIoEx.argtypes = [w.HANDLE, c.POINTER(Overlapped)]
K.CloseHandle.argtypes = [w.HANDLE]
K.QueryPerformanceCounter.argtypes = [c.POINTER(c.c_longlong)]
K.QueryPerformanceFrequency.argtypes = [c.POINTER(c.c_longlong)]
frequency = c.c_longlong()
K.QueryPerformanceFrequency(c.byref(frequency))


def micros():
    ticks = c.c_longlong()
    K.QueryPerformanceCounter(c.byref(ticks))
    return ticks.value * 1_000_000 // frequency.value


class Writer:
    def __init__(self, pipe):
        self.handle = K.CreateFileW('\\\\.\\pipe\\' + pipe, 0x40000000, 0, None, 3, 0x40000000, None)
        if self.handle == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        self.event = K.CreateEventW(None, True, False, None)
        if not self.event:
            K.CloseHandle(self.handle)
            raise c.WinError(c.get_last_error())
        self.buffers = {n: c.create_string_buffer(bytes([1] * n)) for n in (1, 4)}

    def send(self, count=1):
        K.ResetEvent(self.event)
        operation = Overlapped(event=self.event)
        done = w.DWORD()
        started = micros()
        immediate = K.WriteFile(self.handle, self.buffers[count], count, c.byref(done), c.byref(operation))
        error = c.get_last_error() if not immediate else 0
        returned = micros()
        if not immediate and error != 997:
            raise c.WinError(error)
        if not immediate and K.WaitForSingleObject(self.event, 1000) != 0:
            K.CancelIoEx(self.handle, c.byref(operation))
            K.GetOverlappedResult(self.handle, c.byref(operation), c.byref(done), True)
            raise TimeoutError('Client overlapped write failed/timed out')
        if not K.GetOverlappedResult(self.handle, c.byref(operation), c.byref(done), False) or done.value != count:
            raise c.WinError(c.get_last_error())
        return started, returned

    def close(self):
        K.CloseHandle(self.handle)
        K.CloseHandle(self.event)


def profile(workers, label='20261023', extra_scenarios=False):
    directory = features.ROOT / f'logs/pipe-stages-load{workers}-{label}'
    directory.mkdir(parents=True, exist_ok=True)
    features.REPORT = directory
    backend = features.Backend('native', '--mock-quiet', '--mock-trace', '--status-mode', 'interrupt',
                               '--mock-status-delay-ms', '200')
    writer = None
    contenders = []
    stop_queries = threading.Event()
    query_thread = None
    result = {'scope': __doc__, 'workers': workers, 'source': 'direct_overlapped_pipe_client',
              'overload_scenarios_enabled': extra_scenarios, 'trials': {}, 'events': []}
    try:
        backend.wait(lambda s: s['ready'])
        priority = backend.control('status')
        result['verified_service_priorities'] = {key: priority[key] for key in
            ('input_thread_priority', 'hid_thread_priority', 'query_thread_priority', 'process_priority_class')}
        assert priority['input_thread_priority'] == priority['hid_thread_priority'] == 2
        assert priority['query_thread_priority'] == 0 and priority['process_priority_class'] == 32
        writer = Writer(backend.pipe)
        writer.send()
        backend.wait(lambda s: s['play_sent'] == 1)
        for _ in range(workers):
            contenders.append(subprocess.Popen([sys.executable, str(Path(__file__).with_name('cpu_load.py')), 'unrestricted'],
                                               creationflags=subprocess.CREATE_NO_WINDOW))

        def trial(name, interval, burst=1, queries=False):
            nonlocal query_thread
            backend.control('metrics reset')
            before = backend.control('status')
            entries = []
            failures = []
            stop_queries.clear()
            def query():
                try:
                    while not stop_queries.is_set():
                        backend.control('diagnose')
                        backend.control('status')
                        stop_queries.wait(0.03)
                except Exception as error:
                    failures.append(repr(error))
            query_thread = threading.Thread(target=query) if queries else None
            if query_thread: query_thread.start()
            try:
                for _ in range(100 // burst):
                    entered, returned = writer.send(burst)
                    entries.extend([(entered, returned)] * burst)
                    time.sleep(interval)
            finally:
                stop_queries.set()
                if query_thread: query_thread.join(timeout=6)
            if query_thread and query_thread.is_alive(): raise RuntimeError('Query worker did not stop')
            if failures: raise RuntimeError(failures)
            state = backend.wait(lambda s: s['play_sent'] >= before['play_sent'] + 100)
            assert state['play_sent'] == before['play_sent'] + 100 and state['dropped'] == before['dropped']
            samples = backend.control('trace')['stage_samples']
            assert len(samples) == 100
            rows = []
            for (entered, returned), (sequence, submitted, dequeued, trigger, completed) in zip(entries, samples):
                assert entered <= submitted <= dequeued <= trigger <= completed
                rows.append({'write_enter': entered, 'write_return': returned, 'submit': submitted,
                             'dequeue': dequeued, 'trigger': trigger, 'complete': completed,
                             'sequence': sequence, 'trial': name})
            result['events'].extend(rows)
            result['trials'][name] = {'events': 100, 'write_batch_size': burst, 'queries': queries,
                                      'state': state, 'stages_us': stages(rows)}
        trial('20hz', 0.05)
        trial('100hz', 0.01)
        trial('200hz', 0.005)
        if extra_scenarios:
            trial('500hz', 0.002)
            trial('four_byte_bursts', 0.02, burst=4)
        trial('diagnostics_200hz', 0.005, queries=True)
        for index in range(10): trial(f'soak_{index}', 0.01, queries=index % 2 == 0)
        result['stages_us'] = stages(result['events'])
        result['final_status'] = backend.control('status')
        assert result['final_status']['generation'] == 1 and result['final_status']['dropped'] == 0
        (directory / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'workers': workers, 'samples': len(result['events']), 'total': result['stages_us']['write_to_mock_complete']}), flush=True)
        return result
    except Exception as error:
        result['failure'] = repr(error)
        (directory / 'failure.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        raise
    finally:
        stop_queries.set()
        if query_thread and query_thread.is_alive(): query_thread.join(timeout=6)
        if writer: writer.close()
        for process in contenders:
            if process.poll() is None: process.terminate()
            process.wait(timeout=5)
        backend.close()


def stages(rows):
    boundaries = {'named_pipe_delivery': ('write_enter', 'submit'),
                  'service_queue': ('submit', 'dequeue'),
                  'service_prepare': ('dequeue', 'trigger'),
                  'service_build_mock_send': ('trigger', 'complete'),
                  'service_total': ('submit', 'complete'),
                  'write_to_mock_complete': ('write_enter', 'complete'),
                  'write_api_call': ('write_enter', 'write_return')}
    return {name: summarize([r[end] - r[begin] for r in rows]) for name, (begin, end) in boundaries.items()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-label', default='20261023')
    parser.add_argument('--workers', nargs='+', type=int, choices=range(9), default=[0,4,8])
    parser.add_argument('--extra-scenarios', action='store_true', help='Opt-in 500Hz/four-byte overload; excluded by default')
    args = parser.parse_args()
    results = [profile(workers, args.run_label, args.extra_scenarios) for workers in args.workers]
    output = features.ROOT / f'logs/pipe-stages-summary-{args.run_label}.json'
    output.write_text(json.dumps({'scope': __doc__, 'profiles': results}, indent=2), encoding='utf-8')
    print(f'PASS: {sum(len(r["events"]) for r in results)} direct-pipe requests; mock HID.', flush=True)
