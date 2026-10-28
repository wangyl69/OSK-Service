"""Offline all-stage statistics, preserving per-scenario denominators and tails."""
import argparse
import csv
import json
from pathlib import Path
from host_latency_stages import BOUNDARIES, durations


def stats(values):
    ordered = sorted(values)
    count = len(ordered)
    return dict(samples=count, **{f'p{q}_us': ordered[(count*q+99)//100-1] for q in (50, 95, 99)},
                max_us=ordered[-1], over_2ms=sum(x > 2000 for x in ordered),
                over_5ms=sum(x > 5000 for x in ordered))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.campaign / 'manifest.json').read_text(encoding='utf-8-sig'))
    table, slow, runs = [], [], []
    for entry in manifest['runs']:
        assert entry['exit_code'] == 0 and len(entry['captures']) == 1, entry
        directory = Path(entry['captures'][0])
        data = json.loads((directory / 'results.json').read_text(encoding='utf-8'))
        if manifest.get('kernel_trace', True):
            capture = json.loads((directory / 'capture.json').read_text(encoding='utf-8-sig'))
            assert capture['trace_started'] and capture['wpr_stop_exit'] == capture['pipeline_exit'] == 0
        rows = data['sed_stage_records']
        assert len(rows) == data['total_measured_presses'] and rows
        assert data['final_status']['dropped'] == 0 and data['final_status']['generation'] == 1
        assert data['final_status']['play_sent'] == len(rows) + 1  # One excluded warmup PLAY.
        assert all(t['relative_priority'] == 2 for t in data['verified_sed_priorities'].values())
        assert data['verified_service_priorities']['input_thread_priority'] == 2
        assert data['verified_service_priorities']['hid_thread_priority'] == 2
        kernel_path = directory / 'kernel-thread-events.json'
        kernel_lost = None
        if kernel_path.exists():
            kernel = json.loads(kernel_path.read_text(encoding='utf-8'))
            kernel_lost = kernel['lost']
            assert kernel_lost == 0 and kernel['switches'] > 0 and kernel['ready'] > 0
        values = [durations(row) for row in rows]
        for stage in BOUNDARIES:
            table.append(dict(round=entry['round'], scenario=entry['scenario'], workers=entry['workers'],
                              stage=stage, **stats([value[stage] for value in values])))
        for row, value in zip(rows, values):
            if any(x > 2000 for x in value.values()):
                slow.append(dict(round=entry['round'], scenario=entry['scenario'], workers=entry['workers'],
                                 sequence=row['sequence'], trial=row['trial'], durations_us=value, timestamps=row))
        runs.append(dict(round=entry['round'], scenario=entry['scenario'], workers=entry['workers'],
                         presses=len(rows), events_over_2ms=sum(any(x > 2000 for x in v.values()) for v in values),
                         events_over_5ms=sum(any(x > 5000 for x in v.values()) for v in values),
                         capture_trigger=data.get('capture_trigger'), etl_events_lost=kernel_lost, capture=str(directory)))
    summary = dict(scope=manifest['scope'], units='us', measured_presses=sum(r['presses'] for r in runs),
                   kernel_trace=manifest.get('kernel_trace', True), warmup_excluded=True, runs=runs, stages=table)
    (args.campaign / 'stage-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    (args.campaign / 'slow-events.json').write_text(json.dumps(slow, indent=2), encoding='utf-8')
    with (args.campaign / 'stages.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    print(json.dumps(runs, indent=2))


if __name__ == '__main__':
    main()
