"""Actual OSK injector + real dispatcher + native mock service, QPC timing.

No fabricated marker on the positive path. Does not test raw touch recognition
or physical HID/LRA. Native trace is opt-in, bounded, and memory-only.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import subprocess
import threading
import time
import re
import sys
import uuid
from collections import defaultdict

import check_service_features as features
from input_observer import KeyboardObserver
from sed_trace import SedTrace
from host_latency_stages import durations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-osk', action='store_true')
    parser.add_argument('--dispatcher-dll', type=Path,
                        help='Explicit saved Dispatcher binary for controlled A/B checks')
    parser.add_argument('--input-probe', action='store_true',
                        help='Extra Python hook for diagnosing missing input; changes timing')
    parser.add_argument('--run-label', default='')
    parser.add_argument('--quiet', action='store_true')
    parser.add_argument('--default-priorities', action='store_true', help='Verify production Highest defaults without priority overrides')
    parser.add_argument('--injector-priority', choices=['normal', 'highest'], default='normal',
                        help='Diagnostic sender thread priority only; process remains Normal')
    parser.add_argument('--stop-prehook-ms', type=int, choices=range(0, 101), default=0,
                        help='Stop at cadence cycle boundary after a pre-Hook sample exceeds this threshold; 0 disables')
    parser.add_argument('--stop-any-stage-ms', type=int, choices=range(0, 101), default=0,
                        help='Stop after a completed batch if any measured stage or total exceeds threshold; 0 disables')
    parser.add_argument('--cpu-snapshots', action='store_true', help='Read-only CPU restrictions at batch boundaries')
    parser.add_argument('--sed-trace', action='store_true', help='Opt-in SED shared-memory stage timing; changes diagnostic overhead')
    parser.add_argument('--sed-native-wake', action=argparse.BooleanOptionalAction, default=True,
                        help='Native-event SED sender wake (default); --no-sed-native-wake restores the previous wait')
    parser.add_argument('--load-workers', type=int, default=0, choices=range(0, 9),
                        help='Normal-priority CPU contenders, with no CPU affinity changes')
    parser.add_argument('--thread-priority', choices=['normal', 'above-normal', 'highest'], default='above-normal')
    parser.add_argument('--status-mode', choices=['auto', 'feature', 'interrupt'], default='auto')
    parser.add_argument('--service-idle-wait', choices=['fixed', 'deadline'], default='fixed')
    parser.add_argument('--mock-status-delay-ms', type=int, choices=range(0, 250), default=0,
                        help='Inject runtime interrupt status ACK delay, not PLAY latency')
    parser.add_argument('--idle-wake-count', type=int, choices=range(0, 101), default=0,
                        help='Additional first-key-after-idle samples (0..100), same session')
    parser.add_argument('--idle-gap-ms', type=int, default=1000,
                        help='Idle gap for optional wake samples (100..30000ms)')
    parser.add_argument('--idle-only', action='store_true', help='Only optional idle-wake samples, skip the 1400-press baseline')
    parser.add_argument('--extra-scenarios', action='store_true', help='Add 500Hz and bounded four-key bursts (200 presses)')
    parser.add_argument('--scenario-set', choices=['standard', 'cadence', 'typing', 'rapid', 'rapid200', 'diagnostics', 'bursts', 'idle5s', 'idle15s'], default='standard',
                        help='Cadence targets alternating bursts/idle, query contention and first keys')
    parser.add_argument('--cadence-cycles', type=int, choices=range(1, 21), default=1,
                        help='Repeat cadence within one process/session, with unique trial labels')
    parser.add_argument('--scenario-batches', type=int, choices=range(1, 31), default=5,
                        help='100-key batches for a named input scenario; maximum fits SED trace capacity')
    parser.add_argument('--idle-samples', type=int, choices=range(1, 101),
                        help='Measured first-key count for idle5s/idle15s; each sample really waits the full gap')
    parser.add_argument('--cpu-load', action='store_true',
                        help='Disabled historical affinity mode; use --load-workers instead')
    args = parser.parse_args()
    if args.default_priorities:
        args.thread_priority = 'highest'
    if args.idle_samples is not None and args.scenario_set not in ('idle5s', 'idle15s'):
        parser.error('idle-samples requires idle5s or idle15s')
    if args.stop_any_stage_ms and (not args.sed_trace or args.scenario_set == 'standard' or args.idle_only):
        parser.error('stop-any-stage-ms requires sed-trace with cadence or a named separated scenario, without idle-only')
    if args.stop_prehook_ms and (not args.sed_trace or args.scenario_set != 'cadence' or args.idle_only):
        parser.error('stop-prehook-ms requires sed-trace and cadence, without idle-only')
    if args.cadence_cycles != 1 and (args.scenario_set != 'cadence' or args.idle_only):
        parser.error('cadence-cycles requires cadence and cannot be used with idle-only')
    # The opt-in SED mapping has 4096 fixed records. Each cadence cycle uses
    # 470 records, plus startup activity; fail before a long run can exhaust it.
    if args.sed_trace and args.scenario_set == 'cadence' and args.cadence_cycles > 8:
        parser.error('sed-trace supports at most 8 cadence cycles per process; split longer campaigns into runs')
    if not 100 <= args.idle_gap_ms <= 30000:
        parser.error('idle-gap-ms must be 100..30000')
    if args.idle_only and not args.idle_wake_count:
        parser.error('idle-only requires idle-wake-count > 0')
    if args.cpu_load:
        parser.error('CPU affinity stress is disabled; use --load-workers for unrestricted scheduling')
    root = Path(__file__).resolve().parents[3]
    if args.run_label and any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for ch in args.run_label):
        parser.error('run-label must contain only letters, digits, dash or underscore')
    report = features.ROOT / 'logs' / ('real-pipeline' + ('-' + args.run_label if args.run_label else ''))
    report.mkdir(parents=True, exist_ok=True)
    features.REPORT = report
    osk_dir = root / 'OSK_Prototype'
    dll = c.CDLL(str(osk_dir / 'Resources/LrSendInput64.dll'), use_last_error=True)
    for name in ('KeyDown', 'KeyUp'):
        getattr(dll, name).argtypes = [w.WORD]
        getattr(dll, name).restype = c.c_bool
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.QueryPerformanceCounter.argtypes = [c.POINTER(c.c_longlong)]
    kernel.QueryPerformanceFrequency.argtypes = [c.POINTER(c.c_longlong)]
    frequency = c.c_longlong()
    kernel.QueryPerformanceFrequency(c.byref(frequency))

    def micros():
        value = c.c_longlong()
        kernel.QueryPerformanceCounter(c.byref(value))
        return value.value * 1_000_000 // frequency.value

    def edge(down, trace_call=False):
        c.set_last_error(0)
        call_enter = micros() if trace_call else 0
        if not (dll.KeyDown if down else dll.KeyUp)(0x86):
            raise RuntimeError(f'OSK native {"KeyDown" if down else "KeyUp"} failed; Win32 last error={c.get_last_error()}')
        if trace_call:
            return call_enter, micros()

    def summarize(values):
        ordered = sorted(values)
        return {label: ordered[min(len(ordered)-1, (len(ordered)*pct+99)//100-1)]
                for label, pct in [('p50', 50), ('p95', 95), ('p99', 99), ('max', 100)]}

    priority_arguments = [] if args.default_priorities else ['--thread-priority', args.thread_priority]
    backend = features.Backend('native', '--mock-quiet', '--mock-trace', *priority_arguments,
                              '--status-mode', args.status_mode, '--mock-status-delay-ms', str(args.mock_status_delay_ms),
                              '--idle-wait', args.service_idle_wait)
    sed = osk = None
    log = (report / 'dispatcher.txt').open('w', encoding='utf-8')
    results = {'scope': 'OSK native injector entry -> real Windows hook/SED/pipe -> mock PLAY completion; no touch/I2C/LRA'}
    results['scenario_set'] = args.scenario_set
    results['cadence_cycles'] = args.cadence_cycles
    results['extra_python_input_probe'] = args.input_probe
    results['thread_priority'] = args.thread_priority
    results['production_priority_defaults'] = args.default_priorities
    results['requested_status_mode'] = args.status_mode
    results['service_idle_wait_mode'] = args.service_idle_wait
    results['idle_only'] = args.idle_only
    results['idle_gap_ms'] = args.idle_gap_ms
    results['mock_status_delay_ms'] = args.mock_status_delay_ms
    results['controlled_cpu_load'] = False  # Preserve the historical result-schema field.
    results['unrestricted_cpu_load_workers'] = args.load_workers
    results['sed_stage_trace_enabled'] = args.sed_trace
    results['sed_sender_native_wake'] = args.sed_native_wake
    trace_name = 'Local\\HybridSedLatency-' + uuid.uuid4().hex
    sed_trace = None
    contenders = []
    all_latencies = []
    all_service_latencies = []
    all_sed_stages = defaultdict(list)
    all_stage_records = []
    kernel.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenThread.restype = w.HANDLE
    kernel.GetThreadPriority.argtypes = [w.HANDLE]
    kernel.GetPriorityClass.argtypes = [w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.SetThreadPriority.argtypes = [w.HANDLE, c.c_int]
    if not kernel.SetThreadPriority(w.HANDLE(-2), 2 if args.injector_priority == 'highest' else 0):
        raise c.WinError(c.get_last_error())
    results['injector_thread'] = {
        'tid': kernel.GetCurrentThreadId(),
        'requested_priority': args.injector_priority,
        'relative_priority': kernel.GetThreadPriority(w.HANDLE(-2)),
        'process_priority_class': kernel.GetPriorityClass(w.HANDLE(-1)),
    }

    def verify_priorities():
        state = backend.control('status')
        expected = {'normal': 0, 'above-normal': 1, 'highest': 2}[args.thread_priority]
        assert state['input_thread_priority'] == state['hid_thread_priority'] == expected, state
        assert state['query_thread_priority'] == 0 and state['process_priority_class'] == 32, state
        sed_process_class = kernel.GetPriorityClass(w.HANDLE(int(sed._handle)))
        assert sed_process_class == 32, sed_process_class
        results['verified_sed_process_priority_class'] = sed_process_class
        content = (report / 'dispatcher.txt').read_text(encoding='utf-8', errors='replace')
        observed = {}
        expected_wake = 'NativeEvent' if args.sed_native_wake else 'BlockingCollection'
        assert 'Haptic sender wake Mode=' + expected_wake in content, 'SED wake configuration not confirmed'
        queue_mode = re.search(r'Haptic sender wake Mode=\S+ Queue=(\S+)', content)
        results['sed_queue_implementation'] = queue_mode.group(1) if queue_mode else 'BlockingCollection'
        results['service_worker_wake_mode'] = state.get('worker_wake_mode', 'condition-variable')
        for role, identifier, requested, effective in re.findall(
                r'Latency thread Role=(\S+) Win32Id=(\d+) Requested=(\S+) Effective=(\S+)', content):
            handle = kernel.OpenThread(0x40, False, int(identifier))
            if not handle:
                raise c.WinError(c.get_last_error())
            try:
                priority = kernel.GetThreadPriority(handle)
                assert priority == expected, (role, priority, requested, effective)
                observed[role] = {'win32_id': int(identifier), 'relative_priority': priority}
            finally:
                kernel.CloseHandle(handle)
        assert set(observed) == {'keyboard-hook', 'haptic-sender'}, observed
        results['verified_sed_priorities'] = observed
        results['verified_service_priorities'] = {key: state[key] for key in
            ('input_thread_priority', 'hid_thread_priority', 'query_thread_priority', 'process_priority_class')}
    class Probe:
        def __init__(self):
            self.down = self.up = self.unmarked = 0
            self.lock = threading.Lock()

        def observe(self, key, down, extra):
            if key != 0x86:
                return
            with self.lock:
                if extra != 0x5345445F4F534B:
                    self.unmarked += 1
                elif down:
                    self.down += 1
                else:
                    self.up += 1

        def snapshot(self):
            with self.lock:
                return {'down': self.down, 'up': self.up, 'unmarked': self.unmarked}

    probe = Probe()
    observer = KeyboardObserver(probe)
    observer_started = False
    try:
        backend.wait(lambda s: s['ready'])
        if args.with_osk:
            previous_logs = set((osk_dir / 'logs').glob('*/log.txt'))
            osk = subprocess.Popen([str(osk_dir / 'OskDemoApp.exe')], cwd=osk_dir,
                creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if osk.poll() is not None:
                    raise RuntimeError('OSK executable did not stay running')
                candidates = set((osk_dir / 'logs').glob('*/log.txt')) - previous_logs
                initialized = [file for file in candidates if 'OSK Initialized:true' in
                    file.read_text(encoding='utf-8', errors='replace')]
                if initialized:
                    results['osk_initialization_log'] = str(initialized[0])
                    break
                time.sleep(0.1)
            else:
                raise TimeoutError('OSK executable did not confirm initialization')
        dispatcher = args.dispatcher_dll.resolve() if args.dispatcher_dll else root / 'HybridSedPrototype_ExtraInfo/src/SemanticEventDispatcher/bin/Debug/net8.0/SemanticEventDispatcher.dll'
        results['dispatcher_binary'] = str(dispatcher)
        sed_command = ['dotnet', '--roll-forward', 'Major', str(dispatcher),
            '--Dispatcher:HapticPipeName=' + backend.pipe,
            '--Dispatcher:VoiceKeyVirtualKey=135',
            '--Dispatcher:HapticSenderNativeWake=' + str(args.sed_native_wake),
            ]
        if not args.default_priorities:
            sed_command += ['--Dispatcher:LatencyThreadPriorityBoost=' + str(args.thread_priority != 'normal'),
                            '--Dispatcher:LatencyThreadPriority=' + args.thread_priority]
        if args.sed_trace:
            sed_command.append('--Dispatcher:HapticLatencyTraceName=' + trace_name)
        sed = subprocess.Popen(sed_command, stdout=log, stderr=subprocess.STDOUT,
            cwd=dispatcher.parents[3], creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if 'Keyboard hook thread started' in (report / 'dispatcher.txt').read_text(encoding='utf-8', errors='replace'):
                break
            if sed.poll() is not None:
                raise RuntimeError('Dispatcher exited')
            time.sleep(0.02)
        else:
            raise TimeoutError('Dispatcher hook did not become ready')
        verify_priorities()
        if args.sed_trace:
            sed_trace = SedTrace(trace_name, native_wake=args.sed_native_wake)
            assert sed_trace.frequency == frequency.value, 'Stopwatch/QPC frequency mismatch'
        for _ in range(args.load_workers):
            contenders.append(subprocess.Popen([sys.executable, str(Path(__file__).with_name('cpu_load.py')), 'unrestricted'],
                creationflags=subprocess.CREATE_NO_WINDOW))
        if args.input_probe:
            observer.start()
            observer_started = True
        edge(True)
        edge(False)
        backend.wait(lambda s: s['play_sent'] == 1)
        if args.cpu_snapshots:
            import cpu_snapshot
            results['cpu_snapshots'] = []
            warm_wait = backend.control('trace')['wait_samples'][-1]
            service_tids = list(set(warm_wait[6:8]))

        def trial(label, count, interval, queries=False, pre_idle=0, burst_size=1, burst_gap=0, interval_pattern=()):
            if args.cpu_snapshots:
                results['cpu_snapshots'].append(dict(trial=label,
                    service=cpu_snapshot.capture(backend.process.pid, service_tids),
                    sed=cpu_snapshot.capture(sed.pid, [v['win32_id'] for v in results['verified_sed_priorities'].values()])))
                (report / 'cpu-snapshots.json').write_text(json.dumps(results['cpu_snapshots'], indent=2), encoding='utf-8')
            backend.control('metrics reset')
            before = backend.control('status')
            observed_before = probe.snapshot()
            query_stop = threading.Event()
            errors = []

            def query():
                try:
                    while not query_stop.is_set():
                        backend.control('diagnose')
                        backend.control('status')
                        query_stop.wait(0.03)
                except Exception as exc:
                    errors.append(repr(exc))

            thread = threading.Thread(target=query)
            if queries:
                thread.start()
            timestamps = []
            dll_calls = []
            trace_before = sed_trace.count if sed_trace else 0
            results['active_trial'] = {'label': label, 'injection_entry_us': timestamps,
                                       'sed_sequence_before': trace_before}
            trial_started = time.monotonic()
            try:
                # Idle AFTER metric/status setup, not before it: setup must not
                # wake the service immediately ahead of the first measured key.
                if pre_idle:
                    time.sleep(pre_idle)
                for index in range(count):
                    timestamps.append(micros())
                    dll_calls.append(edge(True, trace_call=True))
                    edge(False)
                    if burst_size>1:
                        time.sleep(burst_gap if (index+1)%burst_size==0 else interval)
                    else:
                        time.sleep(interval_pattern[index % len(interval_pattern)] if interval_pattern else interval)
            finally:
                query_stop.set()
                if queries:
                    thread.join(timeout=5)
                    if thread.is_alive():
                        raise RuntimeError('Diagnostic client failed to stop')
            if errors:
                raise AssertionError(errors)
            injection_elapsed_ms = (time.monotonic()-trial_started)*1000
            after = backend.wait(lambda s: s['play_sent'] >= before['play_sent'] + count)
            observed_after = probe.snapshot()
            if args.input_probe and (observed_after['down']-observed_before['down'] != count or observed_after['up']-observed_before['up'] != count):
                raise AssertionError({'input_probe_before': observed_before, 'after': observed_after})
            if after['play_sent'] != before['play_sent'] + count:
                raise AssertionError('Extra PLAY from release or duplicate input')
            if after['dropped'] != before['dropped']:
                raise AssertionError('Service dropped input during trial')
            service_trace = backend.control('trace')
            results['service_wait_schema'] = service_trace.get('wait_schema', 'unrecorded')
            records = service_trace['samples']
            detailed = service_trace['stage_samples']
            waits = service_trace.get('wait_samples', [])
            signals = {item[0]: item[1:] for item in service_trace.get('signal_samples', [])}
            for submitted, (begin, end) in signals.items():
                assert submitted <= begin <= end
            if waits:
                assert len(waits) == count
                for stage, wait in zip(detailed, waits):
                    assert stage[0] == wait[0] and stage[1] <= wait[1] <= wait[2] <= stage[2], (stage, wait)
                    assert wait[3] <= wait[4] <= stage[2] and wait[6] > 0 and wait[7] > 0, wait
                    if results['service_worker_wake_mode'] == 'native-event' and wait[5]:
                        assert wait[5] <= wait[3], 'Native wait must end before explicit queue lock request'
            if len(records) != count:
                raise AssertionError('Trace sample count mismatch')
            if len(detailed) != count:
                raise AssertionError('Detailed Service trace count mismatch')
            for legacy, stages in zip(records, detailed):
                sequence, submitted, dequeued, trigger, completed = stages
                assert legacy == [sequence, submitted, completed]
                assert submitted <= dequeued <= trigger <= completed, stages
            latencies = []
            service_latencies = []
            for started, (_, received, completed) in zip(timestamps, records):
                if not started <= received <= completed:
                    raise AssertionError('QPC clocks or ordering mismatch')
                latencies.append(completed-started)
                service_latencies.append(completed-received)
            all_latencies.extend(latencies)
            all_service_latencies.extend(service_latencies)
            trial_stages = defaultdict(list)
            if sed_trace:
                rows = sed_trace.records(trace_before + 1, count)
                assert sed_trace.count == trace_before + count, 'Unexpected extra SED haptic event'
                for record_index, (started, (_, received, completed), service_row, row) in enumerate(zip(timestamps, records, detailed, rows)):
                    assert started <= row['hook'] and row['native_enter'] <= received <= completed, row
                    row.update(inject=started, service_submit=received, mock_complete=completed, trial=label)
                    row.update(dll_call_enter=dll_calls[record_index][0],
                               dll_call_return=dll_calls[record_index][1],
                               injector_tid=results['injector_thread']['tid'])
                    row.update(service_dequeue=service_row[2], service_trigger_enter=service_row[3])
                    if waits:
                        wait = waits[record_index]
                        row.update(service_submit_locked=wait[1], service_enqueue_stamp=wait[2],
                                   service_worker_lock_request=wait[3], service_worker_locked=wait[4],
                                   service_wait_returned=wait[5], service_input_tid=wait[6], service_worker_tid=wait[7])
                        if len(wait) >= 11:
                            assert wait[8] <= wait[5] and wait[10] in (0, 1)
                            row.update(service_wait_enter=wait[8], service_wait_timeout_ms=wait[9],
                                       service_wait_signaled=wait[10])
                            assert received in signals, 'Missing completed notification trace'
                            row.update(service_notify_enter=signals[received][0],
                                       service_notify_return=signals[received][1])
                    critical = (
                        ('inject_to_hook', started, row['hook']),
                        ('hook_to_publish', row['hook'], row['publish']),
                        ('publish_to_dequeue', row['publish'], row['dequeue']),
                        ('dequeue_to_write', row['dequeue'], row['write_enter']),
                        ('write_preparation', row['write_enter'], row['native_enter']),
                        ('native_to_service_submit', row['native_enter'], received),
                        ('service_total', received, completed),
                        ('service_queue', received, service_row[2]),
                        ('service_prepare', service_row[2], service_row[3]),
                        ('service_build_and_mock_send', service_row[3], completed),
                        ('sed_hook_to_service_submit', row['hook'], received))
                    for name, begin, end in critical:
                        trial_stages[name].append(end - begin)
                    # These intervals can overlap delivery and Service work.
                    trial_stages['native_api_call'].append(row['native_return'] - row['native_enter'])
                    trial_stages['native_return_to_write_finish'].append(row['write_finish'] - row['native_return'])
                    trial_stages['publish_to_enqueue_enter'].append(row['enqueue_enter'] - row['publish'])
                    trial_stages['enqueue_api_call'].append(row['enqueue_return'] - row['enqueue_enter'])
                    trial_stages['queue_wait_after_add_return_lower_bound'].append(max(0, row['dequeue'] - row['enqueue_return']))
                    if args.sed_native_wake:
                        assert row['wake_signal_return'] >= row['enqueue_return'], row
                        trial_stages['queue_wait_after_wake_signal_return_lower_bound'].append(max(0, row['dequeue'] - row['wake_signal_return']))
                    all_stage_records.append(row)
                for name, values in trial_stages.items():
                    all_sed_stages[name].extend(values)
            results[label] = {'presses': count, 'target_interval_ms': interval*1000,
                'interval_pattern_ms': [v*1000 for v in interval_pattern], 'concurrent_queries': queries,
                'burst_size': burst_size, 'burst_gap_ms': burst_gap*1000,
                'actual_injection_elapsed_ms': injection_elapsed_ms,
                'injection_to_mock_complete_us': summarize(latencies),
                'pipe_receive_to_mock_complete_us': summarize(service_latencies),
                'service_queue': after['queue_time'],
                'service_trigger': after['hid_write_time'],
                'service_host_total': after['host_time'],
                'service_dropped_delta': after['dropped']-before['dropped']}
            if sed_trace:
                results[label]['sed_stages_us'] = {name: summarize(values) for name, values in trial_stages.items()}
            if waits:
                results[label]['service_wait_samples'] = waits
                results[label]['service_signal_samples'] = service_trace.get('signal_samples', [])
                results[label]['service_stage_samples'] = detailed
            if args.input_probe:
                results[label]['observed_marked_down_up'] = count
            if args.stop_any_stage_ms and sed_trace:
                slow = []
                for row in rows:
                    stages = {stage: value for stage, value in durations(row).items()
                              if value > args.stop_any_stage_ms * 1000}
                    if stages:
                        slow.append(dict(sequence=row['sequence'], trial=label, stages_us=stages))
                if slow:
                    results['capture_trigger'] = dict(reason='any_stage_threshold',
                        threshold_ms=args.stop_any_stage_ms, events=slow)
            results.pop('active_trial', None)
            (report / 'results-in-progress.json').write_text(json.dumps(results, indent=2), encoding='utf-8')

        if not args.idle_only and args.scenario_set in ('typing', 'rapid', 'rapid200', 'diagnostics', 'bursts'):
            for batch in range(args.scenario_batches):
                label = f'{args.scenario_set}_{batch + 1}'
                if args.scenario_set == 'typing':
                    trial(label, 100, 0.05)
                elif args.scenario_set == 'bursts':
                    trial(label, 100, 0.01, burst_size=4, burst_gap=0.1)
                else:
                    trial(label, 100, 0.005 if args.scenario_set == 'rapid200' else 0.01,
                          queries=args.scenario_set == 'diagnostics')
                if results.get('capture_trigger'):
                    break
        elif not args.idle_only and args.scenario_set in ('idle5s', 'idle15s'):
            gap = 5 if args.scenario_set == 'idle5s' else 15
            trial(args.scenario_set, args.idle_samples or (10 if gap == 5 else 6), gap, pre_idle=gap)
        elif not args.idle_only and args.scenario_set == 'cadence':
            for cycle in range(args.cadence_cycles):
                prefix = f'cycle{cycle + 1}_' if args.cadence_cycles > 1 else ''
                trial(prefix + 'alternating_fast_idle', 100, 0, interval_pattern=(0.005, 0.005, 0.005, 0.2))
                trial(prefix + 'alternating_fast_idle_with_queries', 100, 0, True, interval_pattern=(0.005, 0.005, 0.005, 0.2))
                trial(prefix + 'matched_100hz_without_queries', 100, 0.01)
                trial(prefix + 'matched_100hz_with_queries', 100, 0.01, True)
                trial(prefix + 'first_keys_after_250ms', 50, 0.25, pre_idle=0.25)
                trial(prefix + 'first_keys_after_1000ms', 20, 1, pre_idle=1)
                if results.get('capture_trigger'):
                    break
                if args.stop_prehook_ms:
                    slow = [row for row in all_stage_records if row['hook'] - row['inject'] > args.stop_prehook_ms * 1000]
                    if slow:
                        results['capture_trigger'] = dict(reason='pre_hook_threshold', threshold_ms=args.stop_prehook_ms,
                            completed_cycles=cycle + 1, sequences=[row['sequence'] for row in slow])
                        break
        elif not args.idle_only:
            trial('typing_20hz', 100, 0.05)
            trial('rapid_100hz', 100, 0.01)
            trial('rapid_200hz', 100, 0.005)
            trial('rapid_with_status_and_diagnostics', 100, 0.005, True)
            # Keep each measured batch within the 128-entry bounded trace ring.
            for index in range(10):
                trial('soak_' + str(index), 100, 0.01, index % 2 == 0)
            if args.extra_scenarios:
                trial('rapid_500hz', 100, 0.002)
                trial('four_key_bursts_with_diagnostics', 100, 0, True, burst_size=4, burst_gap=0.02)
        if args.idle_wake_count:
            trial('first_key_after_idle', args.idle_wake_count, args.idle_gap_ms / 1000,
                  pre_idle=args.idle_gap_ms / 1000)
        results['total_measured_presses'] = len(all_latencies)
        results['pooled_injection_to_mock_complete_us'] = summarize(all_latencies)
        results['pooled_pipe_receive_to_mock_complete_us'] = summarize(all_service_latencies)
        results['injection_to_mock_complete_us_samples'] = all_latencies
        results['pipe_receive_to_mock_complete_us_samples'] = all_service_latencies
        if sed_trace:
            results['pooled_sed_stages_us'] = {name: summarize(values) for name, values in all_sed_stages.items()}
            results['sed_stage_records'] = all_stage_records
            # The legacy pipe_receive_to_mock_complete metric begins at
            # Service submission. This span includes Pipe delivery as well.
            pipe_to_mock = [row['mock_complete'] - row['native_enter'] for row in all_stage_records]
            results['pooled_pipe_entry_to_mock_complete_us'] = summarize(pipe_to_mock)
            results['pipe_entry_to_mock_complete_us_samples'] = pipe_to_mock
            results['sed_gc_summary'] = sed_trace.gc_summary()
        results['osk_executable_running_during_test'] = bool(osk is not None and osk.poll() is None)
        results['final_status'] = backend.control('status')
        (report / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(f"PASS: {results['total_measured_presses']} actual OSK DLL presses through real SED/service; zero extra/released PLAY or service drops.")
        print('QPC timing and logs: ' + str(report))
        if not args.quiet:
            print(json.dumps({k: v for k, v in results.items() if k.startswith(('typing', 'rapid'))}, indent=2))
    except Exception as exc:
        results['failure'] = repr(exc)
        if sed_trace:
            results['sed_trace_at_failure'] = sed_trace.snapshot_raw()
        results['input_probe_at_failure'] = probe.snapshot()
        try:
            results['service_status_at_failure'] = backend.control('status')
        except Exception:
            pass
        (report / 'results-failed.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        raise
    finally:
        if observer_started:
            observer.stop()
        for contender in contenders:
            if contender.poll() is None:
                contender.terminate()
            contender.wait(timeout=5)
        for process in (sed, osk):
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
        if sed_trace:
            sed_trace.close()
        log.close()
        backend.close()


if __name__ == '__main__':
    main()
