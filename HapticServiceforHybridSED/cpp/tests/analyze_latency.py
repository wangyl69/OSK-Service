"""Split persisted real-pipeline mock timings; never inject input or change CPU affinity.

Each matching pair of samples shares the same completion timestamp. Subtracting
service duration from total duration gives DLL entry -> Service submit entry.
It cannot distinguish hook scheduling, SED queueing, and pipe delivery within
that upstream segment, nor measure physical HID/I2C transport.
"""
import argparse
import json
from pathlib import Path


def summarize(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError('No timing samples')
    result = {name: ordered[(len(ordered) * pct + 99) // 100 - 1]
              for name, pct in [('p50', 50), ('p95', 95), ('p99', 99), ('max', 100)]}
    result['p999'] = ordered[(len(ordered) * 999 + 999) // 1000 - 1]
    result['samples'] = len(ordered)
    result['strictly_over_us'] = {str(limit): sum(value > limit for value in ordered)
                                for limit in (1000, 5000, 10000, 20000, 50000)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs='+', help='Result directories or results.json files')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    profiles = []
    groups = {}
    seen = set()
    for requested in args.reports:
        path = (requested / 'results.json' if requested.is_dir() else requested).resolve()
        if path in seen:
            parser.error('Duplicate report: ' + str(path))
        seen.add(path)
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            total = data['injection_to_mock_complete_us_samples']
            service = data['pipe_receive_to_mock_complete_us_samples']
            count = data['total_measured_presses']
            if len(total) != count or len(service) != count or not count:
                raise ValueError('Timing sample count mismatch')
            if any(type(t) is not int or type(s) is not int or not 0 <= s <= t
                   for t, s in zip(total, service)):
                raise ValueError('Invalid timing sample or boundary order')
            upstream = [t - s for t, s in zip(total, service)]
            priority = data['thread_priority']
            if data.get('controlled_cpu_load'):
                raise ValueError('This report uses the historical affinity stress profile; use unpinned reports')
            if data['final_status']['dropped']:
                raise ValueError('Report has dropped events; complete FIFO correlation is required')
        except (OSError, KeyError, ValueError, TypeError) as exc:
            parser.error(str(path) + ': ' + str(exc))
        stages = {'dll_to_submit_us': upstream, 'submit_to_mock_complete_us': service,
                  'dll_to_mock_complete_us': total}
        rows = data.get('sed_stage_records', [])
        if rows:
            if len(rows) != count:
                parser.error(str(path) + ': SED stage record count mismatch')
            boundaries = (('inject_to_hook', 'inject', 'hook'), ('hook_to_publish', 'hook', 'publish'),
                          ('publish_to_dequeue', 'publish', 'dequeue'), ('dequeue_to_write', 'dequeue', 'write_enter'),
                          ('write_preparation', 'write_enter', 'native_enter'),
                          ('sed_hook_to_pipe_write', 'hook', 'native_enter'),
                          ('pipe_write_to_mock_complete', 'native_enter', 'mock_complete'),
                          ('sed_hook_to_mock_complete', 'hook', 'mock_complete'),
                          ('native_to_service_submit', 'native_enter', 'service_submit'),
                          ('native_api_call', 'native_enter', 'native_return'),
                          ('native_return_to_write_finish', 'native_return', 'write_finish'))
            try:
                for index, row in enumerate(rows):
                    if row['mock_complete'] - row['inject'] != total[index] or row['mock_complete'] - row['service_submit'] != service[index]:
                        raise ValueError('SED/Service timing correlation mismatch')
                    if row['state'] != 1 or any(row[end] < row[begin] for _, begin, end in boundaries):
                        raise ValueError('Invalid SED trace state or timestamps')
                for name, begin, end in boundaries:
                    stages[name + '_us'] = [row[end] - row[begin] for row in rows]
                if 'service_dequeue' in rows[0]:
                    if any(not r['service_submit'] <= r['service_dequeue'] <= r['service_trigger_enter'] <= r['mock_complete'] for r in rows):
                        raise ValueError('Invalid detailed Service timing boundaries')
                    for name, begin, end in (
                        ('service_queue_us', 'service_submit', 'service_dequeue'),
                        ('service_prepare_us', 'service_dequeue', 'service_trigger_enter'),
                        ('service_build_and_mock_send_us', 'service_trigger_enter', 'mock_complete'),
                        ('sed_hook_to_service_submit_us', 'hook', 'service_submit'),
                        ('dll_to_pipe_write_enter_us', 'inject', 'native_enter')):
                        stages[name] = [r[end] - r[begin] for r in rows]
                if 'enqueue_enter' in rows[0]:
                    for row in rows:
                        if not row['publish'] <= row['enqueue_enter'] <= row['dequeue'] or row['enqueue_return'] < row['enqueue_enter']:
                            raise ValueError('Invalid enqueue boundaries')
                    stages['publish_to_enqueue_enter_us'] = [r['enqueue_enter'] - r['publish'] for r in rows]
                    stages['enqueue_api_call_us'] = [r['enqueue_return'] - r['enqueue_enter'] for r in rows]
                    stages['queue_wait_after_add_return_lower_bound_us'] = [max(0, r['dequeue'] - r['enqueue_return']) for r in rows]
                    if data.get('sed_sender_native_wake'):
                        if any(r['wake_signal_return'] < r['enqueue_return'] for r in rows):
                            raise ValueError('Invalid native event signal timestamp')
                        stages['queue_wait_after_wake_signal_return_lower_bound_us'] = [max(0, r['dequeue'] - r['wake_signal_return']) for r in rows]
                if 'service_submit_locked' in rows[0]:
                    for row in rows:
                        if not row['service_submit'] <= row['service_submit_locked'] <= row['service_enqueue_stamp'] <= row['service_dequeue']:
                            raise ValueError('Invalid submission lock boundaries')
                        if not row['service_worker_lock_request'] <= row['service_worker_locked'] <= row['service_dequeue']:
                            raise ValueError('Invalid worker lock boundaries')
                    for name, begin, end in (
                        ('service_submit_lock_us', 'service_submit', 'service_submit_locked'),
                        ('service_worker_lock_us', 'service_worker_lock_request', 'service_worker_locked'),
                        ('service_after_enqueue_stamp_us', 'service_enqueue_stamp', 'service_dequeue')):
                        stages[name] = [r[end] - r[begin] for r in rows]
            except (KeyError, TypeError, ValueError) as exc:
                parser.error(str(path) + ': ' + str(exc))
        load_workers = data.get('unrestricted_cpu_load_workers', 0)
        group_name = priority + ('/unrestricted-load-' + str(load_workers) if load_workers else '')
        group_name += '/mode-' + data.get('requested_status_mode', 'unrecorded')
        group_name += '/mock-ack-' + str(data.get('mock_status_delay_ms', 0)) + 'ms'
        group_name += '/service-wait-' + data.get('service_idle_wait_mode', 'unrecorded')
        group_name += '/scenario-' + data.get('scenario_set', 'standard')
        group_name += '/worker-wake-' + data.get('service_worker_wake_mode',
            data.get('final_status', {}).get('worker_wake_mode', 'condition-variable'))
        group_name += '/sed-queue-' + data.get('sed_queue_implementation', 'BlockingCollection')
        if data.get('sed_sender_native_wake'):
            group_name += '/native-wake'
        if data.get('sed_stage_trace_enabled'):
            group_name += '/sed-trace-v2' if rows and 'enqueue_enter' in rows[0] else '/sed-trace-v1'
        profile = {'report': str(path), 'thread_priority': priority,
                   'sed_sender_native_wake': data.get('sed_sender_native_wake', False),
                   'unrestricted_cpu_load_workers': load_workers, 'samples': count,
                   'stages': {name: summarize(values) for name, values in stages.items()},
                   'longest_events': [
                       {'sample_index': i, 'dll_to_submit_us': upstream[i],
                        'submit_to_mock_complete_us': service[i], 'dll_to_mock_complete_us': total[i],
                        **({'sed_trace': rows[i]} if rows else {})}
                       for i in sorted(range(count), key=lambda i: total[i], reverse=True)[:10]]}
        profiles.append(profile)
        if 'sed_gc_summary' in data:
            profile['sed_gc_summary'] = data['sed_gc_summary']
        pooled = groups.setdefault(group_name, {name: [] for name in stages})
        for name, values in stages.items():
            pooled.setdefault(name, []).extend(values)
    result = {
        'scope': 'DLL entry -> Service submit after pipe read/parsing -> mock completion; no physical HID/I2C',
        'note': 'The upstream segment still combines Windows hook delivery, SED, and pipe delivery; per-stage percentiles must not be added.',
        'profiles': profiles,
        'pooled_by_priority': {priority: {
            'samples': len(stages['dll_to_mock_complete_us']),
            'stages': {name: summarize(values) for name, values in stages.items()}}
            for priority, stages in groups.items()}}
    encoded = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(encoded + '\n', encoding='utf-8')
        print('Saved: ' + str(args.output.resolve()))
    print(json.dumps(result['pooled_by_priority'], indent=2))


if __name__ == '__main__':
    main()
