"""Correlate measured QPC boundaries with same-capture Ready/CSwitch events.

Nearby processes are context, not proof they blocked an unpinned worker.
"""
import argparse
from collections import Counter
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    args = parser.parse_args()
    root = args.capture
    result = json.loads((root / 'results.json').read_text(encoding='utf-8'))
    kernel = json.loads((root / 'kernel-thread-events.json').read_text(encoding='utf-8'))
    rows = result['sed_stage_records']
    chosen = sorted(rows, key=lambda r: r['mock_complete'] - r['native_enter'], reverse=True)
    chosen = [r for i, r in enumerate(chosen) if i < 20 or r['mock_complete'] - r['native_enter'] > 5000]
    output = []
    for r in chosen:
        tid = r['service_worker_tid']
        notify, end = r['service_notify_enter'], r['service_worker_locked']
        start = min(notify, r['service_wait_enter']) if r['service_wait_enter'] else notify
        events = [e for e in kernel['events'] if start <= e['qpc_us'] <= end
                  and ((e['kind'] == 'ready' and e['tid'] == tid)
                       or (e['kind'] == 'switch' and tid in (e['old_tid'], e['new_tid'])))]
        events.sort(key=lambda e: e['qpc_us'])
        ready = next((e for e in events if e['kind'] == 'ready'), None)
        entered = next((e for e in events if e['kind'] == 'switch' and e['new_tid'] == tid
                        and (ready is None or e['qpc_us'] >= ready['qpc_us'])), None)
        context = Counter(e.get('new_process') or 'unknown' for e in kernel.get('nearby_switches', [])
                          if start <= e['qpc_us'] <= end)
        output.append(dict(trial=r['trial'], submit_us=r['service_submit'], worker_tid=tid,
            pipe_service_us=r['mock_complete']-r['native_enter'],
            notify_to_ready_us=None if ready is None else ready['qpc_us']-notify,
            ready_precedes_notify=ready is not None and ready['qpc_us'] < notify,
            ready_to_first_run_us=None if ready is None or entered is None else entered['qpc_us']-ready['qpc_us'],
            worker_lock_us=r['service_worker_locked']-r['service_worker_lock_request'],
            events=events, nearby_process_switch_counts=context.most_common(10),
            timestamps=r))
    (root / 'kernel-correlated-events.json').write_text(json.dumps(dict(
        note='Same-run QPC correlation. Nearby process activity is not causal attribution; missing Ready is not zero delay.',
        events=output), indent=2), encoding='utf-8')
    for event in output[:5]:
        print(event['trial'], 'Pipe+Service us', event['pipe_service_us'],
              'Ready->run us', event['ready_to_first_run_us'])


if __name__ == '__main__':
    main()
