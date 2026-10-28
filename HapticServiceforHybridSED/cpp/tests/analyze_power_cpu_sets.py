"""Reconstruct parking selection from same-run snapshots and Park/Unpark events.

Parking is a power policy state, not an immutable thread affinity prohibition.
"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    args = parser.parse_args()
    root = args.capture
    kernel = json.loads((root / 'kernel-thread-events.json').read_text())
    snapshots = json.loads((root / 'cpu-snapshots.json').read_text())
    correlated = json.loads((root / 'kernel-correlated-events.json').read_text())['events']
    output = []
    for event in correlated:
        if event['pipe_service_us'] <= 5000:
            continue
        row = event['timestamps']
        notify = row['service_notify_enter']
        candidates = [s for s in snapshots if s['service']['qpc_ns']/1000 <= notify]
        if not candidates:
            output.append(dict(error='No preceding snapshot', notify_us=notify))
            continue
        snapshot = max(candidates, key=lambda s: s['service']['qpc_ns'])
        base = snapshot['service']['qpc_ns']/1000
        state = {(c['group'], c['logical']): c['parked'] for c in snapshot['service']['system_cpu_sets']}
        for item in sorted(kernel['power_events'], key=lambda e: e['qpc_us']):
            if base < item['qpc_us'] <= notify and item['name'] in ('ParkCore', 'UnparkCore'):
                state[int(item['payload']['Group']), int(item['payload']['Number'])] = item['name'] == 'ParkCore'
        output.append(dict(pipe_service_us=event['pipe_service_us'], notify_us=notify,
            unparked_at_notify=[dict(group=g, logical=c) for (g, c), parked in sorted(state.items()) if not parked],
            source_snapshot_trial=snapshot['trial'], source_snapshot_us=base,
            restrictions=snapshot['service'],
            park_changes_in_wait=[v for v in kernel['power_events']
                if row['service_wait_enter'] <= v['qpc_us'] <= row['mock_complete'] and v['name'] in ('ParkCore', 'UnparkCore')],
            worker_priority_changes=[v for v in kernel['priority_decoded_events']
                if v['payload'].get('ThreadId') == str(event['worker_tid']) and v['qpc_us'] <= row['mock_complete']],
            ready_to_run_us=event['ready_to_first_run_us'], worker_switches=event['events']))
    (root / 'power-cpu-restrictions.json').write_text(json.dumps(dict(
        note='Snapshots are batch-boundary observations; transitions reconstruct parking selection, not full CPU eligibility or residency.',
        events=output), indent=2), encoding='utf-8')
    print('Power/CPU restriction windows:', len(output))


if __name__ == '__main__':
    main()
