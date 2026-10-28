"""Summarize same-run scheduling priority and interrupt context without causal claims."""
import argparse
from collections import Counter
import json

from pathlib import Path


def union_length(intervals):
    total = 0.0
    end = None
    for a, b in sorted(intervals):
        total += max(0, b - max(a, end if end is not None else a))
        end = max(b, end if end is not None else b)
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    args = parser.parse_args()
    root = args.capture
    kernel = json.loads((root / 'kernel-thread-events.json').read_text())
    correlated = json.loads((root / 'kernel-correlated-events.json').read_text())
    output = []
    for row in correlated['events']:
        if row['pipe_service_us'] <= 5000:
            continue
        ready = next((e for e in row['events'] if e['kind'] == 'ready'), None)
        if ready is None:
            continue
        entered = next((e for e in row['events'] if e['kind'] == 'switch'
                        and e['new_tid'] == row['worker_tid'] and e['qpc_us'] >= ready['qpc_us']), None)
        if entered is None:
            continue
        a, b = ready['qpc_us'], entered['qpc_us']
        window_start = min(row['timestamps']['native_enter'], row['timestamps']['service_wait_enter'] or a) - 1000
        switches = [e for e in kernel['nearby_switches'] if window_start <= e['qpc_us'] <= b]
        snapshot = next((w for w in kernel.get('window_states', []) if abs(w['start_us']-window_start)<1), None)
        if snapshot:
            switches = (snapshot['prior_switches'] or []) + switches
        cpus = sorted({e['cpu'] for e in switches})
        cpu_rows = []
        for cpu in cpus:
            es = sorted((e for e in switches if e['cpu'] == cpu), key=lambda e: e['qpc_us'])
            before = [e for e in es if e['qpc_us'] <= a]
            last = before[-1] if before else None
            current = a
            durations = Counter()
            for e in [e for e in es if a < e['qpc_us'] < b] + [dict(qpc_us=b)]:
                key = ('unknown', -1) if last is None else (
                    'Idle' if last['new_tid'] == 0 else last.get('new_process') or 'unknown', last['new_priority'])
                durations[key] += e['qpc_us'] - current
                current, last = e['qpc_us'], e
            intervals = [(max(a, e['start_us']), min(b, e['end_us'])) for e in kernel['interrupts']
                         if e['cpu'] == cpu and e['start_us'] < b and a < e['end_us']]
            cpu_rows.append(dict(cpu=cpu, thread_context_us=[dict(process=p, priority=n, us=t)
                for (p, n), t in durations.most_common()], interrupt_union_us=union_length(intervals),
                longest_interrupt_overlap_us=max((y-x for x, y in intervals), default=0)))
        output.append(dict(trial=row['trial'], pipe_service_us=row['pipe_service_us'],
            ready_to_first_run_us=b-a, worker_priority_at_switch_in=entered['new_priority'],
            switch_in_cpu=entered['cpu'], cpus=cpu_rows))
    (root / 'cpu-contention.json').write_text(json.dumps(dict(
        note='Thread context intervals include interrupt time. Unknown CPU state is not idle. No affinity/eligibility or power-state inference.',
        events=output), indent=2), encoding='utf-8')
    print('Wrote contention summaries:', len(output))


if __name__ == '__main__':
    main()
